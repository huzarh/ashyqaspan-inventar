#!/bin/sh
# Pulls frames for camera $1 only while someone is watching: app.py touches $DIR/.wanted on every
# camera request, so the weak Wi-Fi link carries no video when nobody has the camera dialog open.
DIR=/var/lib/atolye-cam
while true; do
  if [ -f "$DIR/.wanted" ] && [ $(( $(date +%s) - $(stat -c %Y "$DIR/.wanted") )) -lt 30 ]; then
    /usr/bin/ffmpeg -nostdin -loglevel error -rtsp_transport tcp -timeout 10000000 -i "$URL" -t 30 \
      -an -vf fps=1/2,scale=960:-2 -q:v 7 -f image2 -update 1 -atomic_writing 1 -y "$DIR/$1.jpg" || sleep 3
  else
    sleep 2
  fi
done
