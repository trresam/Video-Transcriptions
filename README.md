# Video Transcription Pipeline

Automated pipeline that transcribes meeting videos and generates AI summaries using AWS services.

**Upload a video → get a transcription + executive summary automatically.**

## What It Deploys

| Resource | Purpose |
|---|---|
| S3 Bucket | Upload videos, stores transcriptions & summaries |
| Lambda: Orchestrator | Orchestrates the pipeline |
| Lambda: audio-extractor | FFmpeg container for large video processing |
| DynamoDB table | Tracks job progress |
| SQS Dead Letter Queue | Catches failures |
| S3 → Lambda trigger | Auto-processes uploaded videos |

## Deploy (AWS CloudShell — Easiest)

1. Log into the **AWS Console** for the target account
2. Click the **CloudShell** icon (top navigation bar)
3. Run:

```bash
git clone https://github.com/trresam/Video-Transcriptions.git
cd Video-Transcriptions
pip3 install -r requirements.txt
npx cdk bootstrap
npx cdk deploy
```

4. Type `y` when prompted to confirm
5. Wait ~3 minutes — done!

> **Important:** Before deploying, enable Bedrock model access for **Claude 3 Haiku** in the target account:  
> Bedrock Console → Model access → Enable `anthropic.claude-3-haiku-20240307-v1:0`

## Deploy (Local Mac/Linux)

```bash
git clone https://github.com/trresam/Video-Transcriptions.git
cd Video-Transcriptions
pip3 install -r requirements.txt
aws sso login --profile <YOUR_PROFILE>
npx cdk bootstrap --profile <YOUR_PROFILE>
npx cdk deploy --profile <YOUR_PROFILE>
```

Requires: Node.js, Python 3.10+, Docker (for building audio-extractor image), AWS CLI.

## ⚠️ Docker Not Available?

If you get a Docker error like `access_denied` or `403` (common on corporate laptops where Docker Desktop requires a license), **use CloudShell instead**:

1. Open the **AWS Console** → click **CloudShell** (top nav bar)
2. Run the 5 commands from the "Deploy (AWS CloudShell)" section above

CloudShell has Docker pre-installed — no license needed. This is the easiest deployment method.

## Usage

After deployment, CDK prints the bucket name. Upload a video:

```bash
aws s3 cp my-meeting.mp4 s3://<BUCKET_NAME>/
```

**Supported formats:** .mp4, .mov, .mp3, .wav, .flac, .m4a, .ogg, .webm, .avi

**Processing:**
- Short videos (<1hr, <2GB) → direct transcription
- Large videos → audio extraction → chunked transcription → merge

**Get results:**

```bash
aws s3 ls s3://<BUCKET_NAME>/transcriptions/
aws s3 ls s3://<BUCKET_NAME>/summaries/
aws s3 cp s3://<BUCKET_NAME>/summaries/my-meeting.mp4_summary.txt .
```

## Tear Down

```bash
npx cdk destroy
```

Removes all resources including the bucket and its contents.

## Architecture

```
S3 Upload (.mov/.mp4)
    → Lambda (orchestrator)
        → Short: Amazon Transcribe (direct)
        → Long: audio-extractor (FFmpeg) → Transcribe
    → Bedrock (Claude) → AI Summary
    → S3 (transcriptions/ + summaries/)
```

## Supported Languages

Auto-detected: English, Spanish, French, German, Italian, Portuguese, Chinese, Japanese, Korean, Arabic, Hindi, Russian, Dutch, Swedish, Danish, Norwegian, Finnish, Polish, Turkish, Hebrew, Thai, Vietnamese, Malay.
