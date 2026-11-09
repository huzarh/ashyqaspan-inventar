#!/bin/sh
# Continuous recording of camera $1 from the local go2rtc relay (no extra connection to the camera).
# Video is copied, not re-encoded, into clock-aligned 5-minute fragmented MP4 files that stay
# playable even if power is lost mid-segment. atolye-rec-prune keeps the total under the size limit.
DIR=/var/lib/atolye-rec/$1
mkdir -p "$DIR"
exec /usr/bin/ffmpeg -nostdin -loglevel error -rtsp_transport tcp -timeout 15000000 \
  -i "rtsp://127.0.0.1:8554/$1" -map 0:v -c copy \
  -f segment -segment_time 300 -segment_atclocktime 1 -reset_timestamps 1 -strftime 1 \
  -segment_format mp4 -segment_format_options movflags=+frag_keyframe+empty_moov+default_base_moof \
  "$DIR/%Y-%m-%d_%H-%M-%S.mp4"
