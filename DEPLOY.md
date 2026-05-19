# Video Processing Pipeline — Deployment Guide

Deploy the entire video transcription + AI summary pipeline to any AWS account with one command.

## What Gets Deployed

- **S3 Bucket** — Upload videos here (private, auto-named)
- **Lambda: transcribe-customer-csc** — Main orchestrator (routes videos, starts transcription, generates summaries)
- **Lambda: audio-extractor** — Docker container with FFmpeg for large video processing
- **DynamoDB: video-processing-jobs** — Tracks job progress
- **SQS Dead Letter Queue** — Catches failed invocations
- **IAM Roles** — Least-privilege permissions for all services
- **S3 → Lambda trigger** — Automatically processes any uploaded video

## Prerequisites

| Requirement | Check Command | Install |
|---|---|---|
| Node.js (for CDK) | `node --version` | `brew install node` |
| Docker (running) | `docker info` | Docker Desktop |
| AWS CLI v2 | `aws --version` | `brew install awscli` |
| Python 3.10+ | `python3 --version` | Already installed |
| CDK dependencies | — | Step 1 below |

**Also required in the target AWS account:**
- Bedrock model access enabled for `anthropic.claude-3-haiku-20240307-v1:0` (go to Bedrock console → Model access → Enable)

## Step-by-Step Deployment

### Step 1: Install Python dependencies

```bash
cd "/Users/trresam/Cloud Project /video-pipeline-cdk"
pip3 install -r requirements.txt
```

### Step 2: Start Docker

Make sure Docker Desktop is running. Verify with:

```bash
docker info
```

### Step 3: Log in to the target AWS account

```bash
# For the Prod account:
aws sso login --profile AWS-Team-Prod

# For your Work account:
aws sso login --profile AWS-Trresam-Account

# For GenAIDemos:
aws sso login --profile GenAIDemos
```

### Step 4: Bootstrap CDK (first time only per account)

This creates a CDK staging bucket in the account. Only needed once per account/region.

```bash
npx cdk bootstrap --profile AWS-Team-Prod
```

### Step 5: Deploy

```bash
npx cdk deploy --profile AWS-Team-Prod
```

CDK will:
1. Show you all resources it will create
2. Ask for confirmation → type `y`
3. Build the Docker image for audio-extractor (~1-2 min)
4. Deploy all resources via CloudFormation (~2-3 min)

### Step 6: Note the outputs

After deployment, CDK prints:

```
Outputs:
VideoPipelineStack.BucketName = videopipelinestack-videobucket-xxxxx
VideoPipelineStack.TableName = video-processing-jobs
VideoPipelineStack.OrchestratorArn = arn:aws:lambda:...
VideoPipelineStack.AudioExtractorArn = arn:aws:lambda:...
```

**Save the BucketName** — that's where you upload videos.

## Usage

Upload a video and the pipeline runs automatically:

```bash
aws s3 cp my-meeting.mp4 s3://<BUCKET_NAME>/ --profile AWS-Team-Prod
```

**Supported formats:** .mp4, .mov, .mp3, .wav, .flac, .m4a, .ogg, .amr, .webm, .avi

**What happens:**
1. S3 triggers the orchestrator Lambda
2. Short videos (<1hr, <2GB) → direct Transcribe
3. Large videos → audio extraction → chunked transcription → merge
4. Transcription complete → Bedrock generates meeting summary
5. Results saved to `transcriptions/` and `summaries/` prefixes in the bucket

**Check results:**

```bash
# List transcriptions
aws s3 ls s3://<BUCKET_NAME>/transcriptions/ --profile AWS-Team-Prod

# List summaries
aws s3 ls s3://<BUCKET_NAME>/summaries/ --profile AWS-Team-Prod

# Download a summary
aws s3 cp s3://<BUCKET_NAME>/summaries/my-meeting.mp4_summary.txt . --profile AWS-Team-Prod
```

## Tear Down

To remove everything from the account:

```bash
npx cdk destroy --profile AWS-Team-Prod
```

This deletes all resources including the S3 bucket and its contents.

## Troubleshooting

| Problem | Solution |
|---|---|
| `Docker daemon not running` | Start Docker Desktop |
| `CDKToolkit stack not found` | Run `npx cdk bootstrap --profile <PROFILE>` |
| `No module named 'aws_cdk'` | Run `pip3 install -r requirements.txt` |
| `ExpiredToken` | Re-run `aws sso login --profile <PROFILE>` |
| Video uploaded but no transcription | Check Lambda logs: `aws logs tail /aws/lambda/transcribe-customer-csc --profile <PROFILE>` |
| Summary not generated | Verify Bedrock model access is enabled in the account |
| Audio extractor timeout | File may be too large for Lambda's 10GB /tmp — split manually |
