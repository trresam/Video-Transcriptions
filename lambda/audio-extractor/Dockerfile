FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.10

# Install FFmpeg (static build for x86_64 Linux)
RUN yum install -y tar xz && \
    curl -L https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz -o /tmp/ffmpeg.tar.xz && \
    tar -xf /tmp/ffmpeg.tar.xz -C /tmp && \
    cp /tmp/ffmpeg-*-amd64-static/ffmpeg /usr/local/bin/ffmpeg && \
    cp /tmp/ffmpeg-*-amd64-static/ffprobe /usr/local/bin/ffprobe && \
    chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe && \
    rm -rf /tmp/ffmpeg* && \
    yum clean all

# Copy function code
COPY audio_extractor.py ${LAMBDA_TASK_ROOT}/

CMD ["audio_extractor.lambda_handler"]
