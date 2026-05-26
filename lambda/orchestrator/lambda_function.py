"""
Video/Audio Transcription Pipeline
- Short videos (<1hr, <2GB): Direct Transcribe
- Large videos (>1hr or >2GB): FFmpeg audio extraction via audio-extractor Lambda,
  then Transcribe on the extracted audio
- No more MediaConvert dependency
"""

import boto3
import os
import uuid
import json
import logging
from urllib.parse import unquote_plus
from botocore.exceptions import ClientError

s3_client = boto3.client('s3')
transcribe_client = boto3.client('transcribe')
dynamodb = boto3.resource('dynamodb')
lambda_client = boto3.client('lambda')

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BUCKET_NAME = os.environ.get('BUCKET_NAME', '')
TRACKING_TABLE = os.environ.get('TRACKING_TABLE', 'video-processing-jobs')
AUDIO_EXTRACTOR_FUNCTION = os.environ.get('AUDIO_EXTRACTOR_FUNCTION', 'audio-extractor')


def lambda_handler(event, context):
    """Main handler - routes based on event type"""
    try:
        # S3 event
        if 'Records' in event and 's3' in event['Records'][0]:
            bucket = event['Records'][0]['s3']['bucket']['name']
            key = unquote_plus(event['Records'][0]['s3']['object']['key'])

            if not key:
                return {'statusCode': 400, 'body': 'Empty key'}

            # Completed transcription
            if key.startswith('transcriptions/') and key.endswith('.json'):
                return handle_transcription_complete(bucket, key)

            # Audio chunk ready for transcription (from audio-extractor)
            elif key.startswith('audio/') and key.endswith('.mp3'):
                return handle_audio_ready(bucket, key)

            # Legacy: video chunk from MediaConvert (keep backward compat)
            elif key.startswith('chunks/') and key.endswith('.mp4'):
                return handle_audio_ready(bucket, key)

            # New video upload
            else:
                return handle_new_video(bucket, key)

        # Direct invocation - merge transcripts
        elif 'action' in event and event['action'] == 'merge_and_summarize':
            return merge_and_summarize(event)

        return {'statusCode': 400, 'body': 'Unknown event'}

    except Exception as e:
        logger.error(f"Error: {str(e)}")
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}


def handle_new_video(bucket, key):
    """Handle new video upload"""
    supported_formats = ['mp3', 'mp4', 'wav', 'flac', 'm4a', 'ogg', 'amr', 'webm', 'mov', 'avi']
    file_extension = key.lower().split('.')[-1]

    if file_extension not in supported_formats:
        logger.warning(f"Unsupported format: {file_extension}")
        return {'statusCode': 200, 'body': 'Unsupported format'}

    # Skip already processed files
    if key.startswith(('transcriptions/', 'summaries/', 'chunks/', 'audio/')):
        return {'statusCode': 200, 'body': 'Already processed'}

    # Get file size
    response = s3_client.head_object(Bucket=bucket, Key=key)
    file_size_mb = response['ContentLength'] / (1024 * 1024)
    estimated_hours = file_size_mb / 500

    logger.info(f"New video: {key}, Size: {file_size_mb:.1f}MB, Est: {estimated_hours:.1f}hrs")

    # Large files: extract audio via FFmpeg Lambda, then transcribe
    if estimated_hours > 1.0 or file_size_mb > 2000:
        logger.info(f"Large video detected - sending to audio-extractor")
        return start_audio_extraction(bucket, key, file_size_mb)
    else:
        logger.info(f"Short video - direct transcription")
        return start_direct_transcription(bucket, key, file_extension)


def start_audio_extraction(bucket, key, file_size_mb):
    """Invoke audio-extractor Lambda to extract audio from large video files"""
    job_id = str(uuid.uuid4())
    estimated_hours = file_size_mb / 500
    estimated_chunks = max(1, int((estimated_hours * 3600) / 1800) + 1)

    # Store job in DynamoDB
    table = dynamodb.Table(TRACKING_TABLE)
    table.put_item(Item={
        'job_id': job_id,
        'source_key': key,
        'bucket': bucket,
        'status': 'extracting_audio',
        'estimated_chunks': estimated_chunks,
        'chunks_transcribed': 0,
        'file_size_mb': int(file_size_mb)
    })

    # Invoke audio-extractor asynchronously
    payload = {
        'bucket': bucket,
        'key': key,
        'job_id': job_id
    }

    lambda_client.invoke(
        FunctionName=AUDIO_EXTRACTOR_FUNCTION,
        InvocationType='Event',
        Payload=json.dumps(payload)
    )

    logger.info(f"Audio extraction started for {key}, job_id: {job_id}")

    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': 'Audio extraction started',
            'job_id': job_id,
            'estimated_chunks': estimated_chunks
        })
    }


def handle_audio_ready(bucket, key):
    """Handle audio file ready for transcription (from audio-extractor or legacy chunks)"""
    if key.endswith('.temp') or '.write_access_check' in key:
        return {'statusCode': 200, 'body': 'Skipped temp file'}

    # Extract job_id from path: audio/{job_id}/chunk_001.mp3 or audio/{job_id}/filename.mp3
    parts = key.split('/')
    if len(parts) < 3:
        return {'statusCode': 400, 'body': 'Invalid audio path'}

    job_id = parts[1]
    audio_filename = parts[2]

    # Determine chunk index
    import re
    match = re.search(r'chunk[_-]?(\d+)', audio_filename)
    chunk_index = int(match.group(1)) if match else 0

    # Determine media format
    media_format = 'mp3' if key.endswith('.mp3') else 'mp4'

    logger.info(f"Transcribing audio chunk {chunk_index} for job {job_id}: {key}")

    transcribe_job_name = f"chunk-{job_id}-{chunk_index:03d}"

    try:
        transcribe_client.start_transcription_job(
            TranscriptionJobName=transcribe_job_name,
            IdentifyLanguage=True,
            LanguageOptions=[
                'en-US', 'es-US', 'es-ES', 'fr-FR', 'de-DE', 'it-IT',
                'pt-BR', 'pt-PT', 'zh-CN', 'ja-JP', 'ko-KR'
            ],
            MediaFormat=media_format,
            Media={'MediaFileUri': f"s3://{bucket}/{key}"},
            OutputBucketName=bucket,
            OutputKey=f'transcriptions/chunks/{job_id}/chunk-{chunk_index:03d}.json',
            Settings={
                'ShowSpeakerLabels': True,
                'MaxSpeakerLabels': 10
            }
        )
        logger.info(f"Started transcription: {transcribe_job_name}")
        return {'statusCode': 200, 'body': json.dumps({
            'message': 'Audio transcription started',
            'job_id': job_id,
            'chunk_index': chunk_index
        })}
    except ClientError as e:
        logger.error(f"Transcription failed: {e}")
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}


def handle_transcription_complete(bucket, key):
    """Handle completed transcription"""
    if '/chunks/' in key:
        return handle_chunk_transcription_complete(bucket, key)
    else:
        return create_summary(bucket, key)


def handle_chunk_transcription_complete(bucket, key):
    """Handle chunk transcription completion"""
    parts = key.split('/')
    if len(parts) < 4:
        return {'statusCode': 400}

    job_id = parts[2]

    table = dynamodb.Table(TRACKING_TABLE)
    response = table.update_item(
        Key={'job_id': job_id},
        UpdateExpression='ADD chunks_transcribed :inc',
        ExpressionAttributeValues={':inc': 1},
        ReturnValues='ALL_NEW'
    )

    item = response['Attributes']
    chunks_transcribed = item.get('chunks_transcribed', 0)
    estimated_chunks = item.get('estimated_chunks', 0)

    logger.info(f"Job {job_id}: {chunks_transcribed}/{estimated_chunks} chunks transcribed")

    if chunks_transcribed >= estimated_chunks:
        logger.info(f"All chunks transcribed for {job_id} - starting merge")
        lambda_client.invoke(
            FunctionName=os.environ['AWS_LAMBDA_FUNCTION_NAME'],
            InvocationType='Event',
            Payload=json.dumps({
                'action': 'merge_and_summarize',
                'job_id': job_id,
                'bucket': bucket
            })
        )

    return {'statusCode': 200}


def merge_and_summarize(event):
    """Merge all chunk transcripts and create summary"""
    job_id = event['job_id']
    bucket = event['bucket']

    logger.info(f"Merging transcripts for job {job_id}")

    table = dynamodb.Table(TRACKING_TABLE)
    response = table.get_item(Key={'job_id': job_id})
    item = response['Item']
    source_key = item['source_key']

    # List all chunk transcriptions
    prefix = f'transcriptions/chunks/{job_id}/'
    response = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)

    if 'Contents' not in response:
        logger.error(f"No transcriptions found for {job_id}")
        return {'statusCode': 404}

    chunk_files = sorted([
        obj['Key'] for obj in response['Contents']
        if obj['Key'].endswith('.json') and '.temp' not in obj['Key']
        and '.write_access_check' not in obj['Key']
    ])

    merged_transcript = []
    for chunk_key in chunk_files:
        try:
            obj = s3_client.get_object(Bucket=bucket, Key=chunk_key)
            transcript_data = json.loads(obj['Body'].read().decode('utf-8'))
            if 'results' in transcript_data and 'transcripts' in transcript_data['results']:
                text = transcript_data['results']['transcripts'][0]['transcript']
                merged_transcript.append(text)
        except Exception as e:
            logger.error(f"Error reading {chunk_key}: {e}")

    full_transcript = ' '.join(merged_transcript)
    logger.info(f"Merged transcript length: {len(full_transcript)} characters")

    # Save merged transcript
    video_name = source_key.split("/")[-1]
    merged_key = f'transcriptions/{video_name}.txt'
    s3_client.put_object(Bucket=bucket, Key=merged_key, Body=full_transcript.encode('utf-8'), ContentType='text/plain')

    # Create summary using Bedrock
    summary = create_bedrock_summary(full_transcript)

    summary_key = f'summaries/{video_name}_summary.txt'
    s3_client.put_object(Bucket=bucket, Key=summary_key, Body=summary.encode('utf-8'), ContentType='text/plain')

    # Update DynamoDB
    table.update_item(
        Key={'job_id': job_id},
        UpdateExpression='SET #status = :status, merged_transcript_key = :transcript, summary_key = :summary',
        ExpressionAttributeNames={'#status': 'status'},
        ExpressionAttributeValues={
            ':status': 'completed',
            ':transcript': merged_key,
            ':summary': summary_key
        }
    )

    logger.info(f"Job {job_id} completed successfully")
    return {'statusCode': 200, 'body': json.dumps({
        'message': 'Processing complete',
        'job_id': job_id,
        'transcript': merged_key,
        'summary': summary_key
    })}


def create_bedrock_summary(transcript_text):
    """Create executive summary using Bedrock"""
    bedrock_client = boto3.client('bedrock-runtime')

    executive_prompt_template = """You are an executive assistant preparing a professional meeting summary that will be sent via email to stakeholders.
Analyze the following meeting transcript and create a polished, executive-ready summary.

TRANSCRIPT:
{transcript}

Write the summary in this exact format:

MEETING SUMMARY
Date: [Extract from context or write "See subject line"]
Duration: [Estimate based on content length]

EXECUTIVE OVERVIEW
[2-3 sentence high-level overview of what the meeting was about and the main outcome]

KEY DISCUSSION POINTS
• [Concise bullet points of the main topics discussed, max 5-7 points]

DECISIONS MADE
• [List concrete decisions that were reached, if any]
• [Write "No formal decisions recorded" if none were made]

ACTION ITEMS
• [Owner if identifiable]: [Specific action item with deadline if mentioned]
• [Write "No specific action items assigned" if none were identified]

NEXT STEPS
• [What needs to happen next based on the discussion]

RISKS & OPEN QUESTIONS
• [Any unresolved issues, concerns, or risks mentioned]
• [Write "None identified" if not applicable]

---
This summary was auto-generated from a meeting recording.

IMPORTANT RULES:
- Be concise and professional — this goes directly into an email
- Use clear, direct language suitable for executives
- Focus on outcomes and actions, not play-by-play of the conversation
- If the meeting was in Spanish or another language, write the summary in that same language
- Do not include filler words, small talk, or off-topic discussions
- Keep the total summary under 500 words"""

    # Handle long transcripts by summarizing in chunks first
    if len(transcript_text) > 100000:
        chunks = [transcript_text[i:i+50000] for i in range(0, len(transcript_text), 50000)]
        chunk_summaries = []
        for i, chunk in enumerate(chunks):
            prompt = f"Extract the key points, decisions, and action items from this meeting segment (Part {i+1}/{len(chunks)}). Be concise:\n\n{chunk}"
            response = bedrock_client.invoke_model(
                modelId='anthropic.claude-3-haiku-20240307-v1:0',
                body=json.dumps({
                    'anthropic_version': 'bedrock-2023-05-31',
                    'max_tokens': 1000,
                    'messages': [{'role': 'user', 'content': prompt}]
                })
            )
            result = json.loads(response['body'].read())
            chunk_summaries.append(result['content'][0]['text'])
        combined = "\n\n".join(chunk_summaries)
        final_prompt = executive_prompt_template.format(transcript=combined)
    else:
        final_prompt = executive_prompt_template.format(transcript=transcript_text)

    response = bedrock_client.invoke_model(
        modelId='anthropic.claude-3-haiku-20240307-v1:0',
        body=json.dumps({
            'anthropic_version': 'bedrock-2023-05-31',
            'max_tokens': 2000,
            'messages': [{'role': 'user', 'content': final_prompt}]
        })
    )
    result = json.loads(response['body'].read())
    return result['content'][0]['text']


def start_direct_transcription(bucket, key, file_extension):
    """Direct transcription for shorter videos"""
    job_name = f"transcribe-{uuid.uuid4().hex[:12]}"

    media_format_mapping = {
        'mp3': 'mp3', 'mp4': 'mp4', 'wav': 'wav', 'flac': 'flac',
        'm4a': 'mp4', 'ogg': 'ogg', 'amr': 'amr', 'webm': 'webm',
        'mov': 'mp4', 'avi': 'mp4'
    }
    media_format = media_format_mapping.get(file_extension, 'mp4')

    file_name = os.path.splitext(os.path.basename(key))[0]
    output_key = f"transcriptions/{file_name}-transcription.json"

    transcribe_client.start_transcription_job(
        TranscriptionJobName=job_name,
        IdentifyLanguage=True,
        LanguageOptions=[
            'en-US', 'es-US', 'es-ES', 'fr-FR', 'de-DE', 'it-IT',
            'pt-BR', 'pt-PT', 'zh-CN', 'ja-JP', 'ko-KR', 'ar-SA',
            'hi-IN', 'ru-RU', 'nl-NL', 'sv-SE', 'da-DK', 'no-NO',
            'fi-FI', 'pl-PL', 'tr-TR', 'he-IL', 'th-TH', 'vi-VN', 'ms-MY'
        ],
        MediaFormat=media_format,
        Media={'MediaFileUri': f"s3://{bucket}/{key}"},
        OutputBucketName=bucket,
        OutputKey=output_key,
        Settings={
            'ShowSpeakerLabels': True,
            'MaxSpeakerLabels': 10,
            'ShowAlternatives': True,
            'MaxAlternatives': 3
        }
    )

    logger.info(f"Direct transcription started: {job_name}")
    return {'statusCode': 200, 'body': json.dumps({
        'message': 'Direct transcription started',
        'job_name': job_name
    })}


def create_summary(bucket, key):
    """Create summary for single transcription"""
    try:
        obj = s3_client.get_object(Bucket=bucket, Key=key)
        transcript_data = json.loads(obj['Body'].read().decode('utf-8'))

        transcript_text = ""
        if 'results' in transcript_data and 'transcripts' in transcript_data['results']:
            transcript_text = transcript_data['results']['transcripts'][0]['transcript']

        if not transcript_text.strip():
            return {'statusCode': 200, 'body': 'Empty transcript'}

        summary = create_bedrock_summary(transcript_text)

        filename = key.split('/')[-1]
        summary_key = f"summaries/{filename.replace('.json', '_summary.txt')}"

        s3_client.put_object(Bucket=bucket, Key=summary_key, Body=summary, ContentType='text/plain')
        logger.info(f"Summary created: {summary_key}")

        return {'statusCode': 200, 'body': json.dumps({
            'message': 'Summary created',
            'summary_key': summary_key
        })}
    except Exception as e:
        logger.error(f"Summary creation failed: {e}")
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
