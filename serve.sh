# serve.sh: loops the compositor's finished JPEG and serves it as MJPEG over HTTP.

set -eu

FRAME="${1:-/data/frame.jpg}"
PORT="${2:-8084}"
QUALITY="${3:-5}"
FPS="${4:-2}"

while [ ! -f "$FRAME" ]; do
    echo "waiting for $FRAME ..."
    sleep 2
done

echo "serving $FRAME as MJPEG over HTTP on port $PORT at ${FPS} fps"

exec ffmpeg \
    -hide_banner \
    -loglevel warning \
    -f image2 \
    -loop 1 \
    -framerate "$FPS" \
    -i "$FRAME" \
    -c:v mjpeg \
    -pix_fmt yuvj420p \
    -q:v "$QUALITY" \
    -f mjpeg \
    -listen 1 \
    "http://0.0.0.0:${PORT}/"