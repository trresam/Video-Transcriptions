#!/usr/bin/env python3
import aws_cdk as cdk
from video_pipeline_stack import VideoPipelineStack

app = cdk.App()
VideoPipelineStack(app, "VideoPipelineStack")
app.synth()
