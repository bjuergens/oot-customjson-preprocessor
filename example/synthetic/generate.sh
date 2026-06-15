#!/usr/bin/env bash
# One-off generator for the synthetic test fixture.
#
# Produces a tiny, fast-to-reencode .mkv with:
#   - a video track showing a running clock / frame counter and moving elements
#     (ffmpeg's built-in `testsrc2`, which renders its own digits -- no fonts needed)
#   - an embedded .ass subtitle track (tagged language=eng) with a few sample lines
#
# The outputs (clock.mkv, subs.ass) are committed next to this script so the e2e
# tests can consume them without regenerating. Re-run this only when you want to
# refresh the fixture. Keep it small: 320x240, 12 fps, 60 s, high CRF.
set -euo pipefail

cd "$(dirname "$0")"

command -v ffmpeg >/dev/null || { echo "ffmpeg not found on PATH" >&2; exit 1; }

# 1) Sample subtitle track. Kept as a readable artifact and muxed into the mkv.
cat > subs.ass <<'ASS'
[Script Info]
ScriptType: v4.00+
PlayResX: 320
PlayResY: 240

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Sans,18,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:06.00,Default,,0,0,0,,this is a dialog line
Dialogue: 0,0:00:07.00,0:00:12.00,Default,,0,0,0,,this line has {\i1}italics{\i0} in it
Dialogue: 0,0:00:13.00,0:00:20.00,Default,,0,0,0,,{\move(20,200,300,200)}this line moves across the screen
Dialogue: 0,0:00:21.00,0:00:26.00,Default,,0,0,0,,some line at [00:00:21]
Dialogue: 0,0:00:27.00,0:00:35.00,Default,,0,0,0,,{\t(\fscx150\fscy150)}this line changes over time
Dialogue: 0,0:00:40.00,0:00:50.00,Default,,0,0,0,,a plain closing dialog line
ASS

# 2) Synthetic video + muxed subtitle track.
ffmpeg -y -hide_banner -loglevel error \
  -f lavfi -i "testsrc2=size=320x240:rate=12" \
  -i subs.ass \
  -t 60 \
  -map 0:v -map 1 \
  -c:v libx264 -preset veryfast -crf 34 -pix_fmt yuv420p \
  -c:s ass -metadata:s:s:0 language=eng \
  clock.mkv

echo "✅ wrote $(pwd)/clock.mkv ($(du -h clock.mkv | cut -f1)) and subs.ass"
