import aws_cdk as cdk
from aws_cdk import (
    Stack, Duration, RemovalPolicy, CfnOutput,
    aws_s3 as s3,
    aws_lambda as _lambda,
    aws_dynamodb as dynamodb,
    aws_sqs as sqs,
    aws_iam as iam,
    aws_s3_notifications as s3n,
)
from constructs import Construct


class VideoPipelineStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # DynamoDB table for job tracking
        tracking_table = dynamodb.Table(
            self, "TrackingTable",
            table_name="video-processing-jobs",
            partition_key=dynamodb.Attribute(name="job_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # S3 bucket
        bucket = s3.Bucket(
            self, "VideoBucket",
            bucket_name=None,  # auto-generated unique name
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # Dead letter queue
        dlq = sqs.Queue(
            self, "DLQ",
            queue_name="video-pipeline-dlq",
            retention_period=Duration.days(14),
        )

        # Audio extractor Lambda (Docker image with FFmpeg)
        audio_extractor = _lambda.DockerImageFunction(
            self, "AudioExtractor",
            function_name="audio-extractor",
            code=_lambda.DockerImageCode.from_image_asset("lambda/audio-extractor"),
            timeout=Duration.seconds(900),
            memory_size=2048,
            ephemeral_storage_size=cdk.Size.mebibytes(4096),
            environment={
                "TRACKING_TABLE": tracking_table.table_name,
            },
        )

        # Main orchestrator Lambda
        orchestrator = _lambda.Function(
            self, "Orchestrator",
            function_name="transcribe-customer-csc",
            runtime=_lambda.Runtime.PYTHON_3_10,
            handler="lambda_function.lambda_handler",
            code=_lambda.Code.from_asset("lambda/orchestrator"),
            timeout=Duration.seconds(900),
            memory_size=1024,
            dead_letter_queue=dlq,
            environment={
                "TRACKING_TABLE": tracking_table.table_name,
                "AUDIO_EXTRACTOR_FUNCTION": audio_extractor.function_name,
            },
        )

        # Permissions
        bucket.grant_read_write(orchestrator)
        bucket.grant_read_write(audio_extractor)
        tracking_table.grant_read_write_data(orchestrator)
        tracking_table.grant_read_write_data(audio_extractor)
        audio_extractor.grant_invoke(orchestrator)
        orchestrator.grant_invoke(orchestrator)  # self-invoke for merge_and_summarize

        # Transcribe permissions
        transcribe_policy = iam.PolicyStatement(
            actions=["transcribe:StartTranscriptionJob", "transcribe:GetTranscriptionJob"],
            resources=["*"],
        )
        orchestrator.add_to_role_policy(transcribe_policy)

        # Bedrock permissions
        bedrock_policy = iam.PolicyStatement(
            actions=["bedrock:InvokeModel"],
            resources=["*"],
        )
        orchestrator.add_to_role_policy(bedrock_policy)

        # S3 event notification → orchestrator Lambda
        bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.LambdaDestination(orchestrator),
        )

        # Outputs
        CfnOutput(self, "BucketName", value=bucket.bucket_name)
        CfnOutput(self, "TableName", value=tracking_table.table_name)
        CfnOutput(self, "OrchestratorArn", value=orchestrator.function_arn)
        CfnOutput(self, "AudioExtractorArn", value=audio_extractor.function_arn)
