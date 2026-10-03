# imgstream: turns one still JPEG into an MJPEG stream, with sensor text on the frame.

FROM python:3.13-slim

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ffmpeg tzdata \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "Pillow>=11.3,<12"

WORKDIR /app

COPY fonts/ /app/fonts/
COPY compositor.py /app/compositor.py
COPY serve.sh /app/serve.sh
RUN chmod +x /app/serve.sh

RUN mkdir -p /data

ENV IMAGE_URL="" \
    OUTPUT_PATH=/data/frame.jpg \
    HA_URL="" \
    HA_TOKEN="" \
    SENSOR_ENTITY="" \
    SENSOR_UNIT="" \
    TEXT_ENABLED=true \
    TEXT_CORNER=bottom-left \
    MARGIN_X=6 \
    MARGIN_BOTTOM=8 \
    BASE_FRAC=0.10 \
    MAX_WIDTH_FRAC=0.40 \
    STAMP_SIZE=17 \
    PLACEHOLDER_COLOR=#6e6e6e \
    TEXT_COLOR=#FFFFFF \
    UNAVAILABLE_TEXT=unavailable \
    POLL_SECONDS=5 \
    SENSOR_POLL_SECONDS=60 \
    STALE_SECONDS=60 \
    SENSOR_HOLD_SECONDS=1800 \
    HTTP_TIMEOUT=10 \
    FONT_PATH=/app/fonts/RobotoCondensed.ttf \
    STREAM_PORT=8084 \
    STREAM_QUALITY=5 \
    STREAM_FPS=2 \
    TZ=Etc/UTC \
    DIAGNOSE=false \
    DIAGNOSE_AGE=false

CMD ["/bin/sh", "-c", "while true; do python /app/compositor.py; echo 'compositor exited, restarting'; sleep 5; done & exec /app/serve.sh /data/frame.jpg ${STREAM_PORT} ${STREAM_QUALITY} ${STREAM_FPS}"]
