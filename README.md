# OTT CustomJson Preprocessor

Prepare local media (`.mkv`, `.mp4`, ...) for a [OpenTogetherTube](https://opentogethertube.com/) instance using its
**custom media format**.

For every media file in the input directory it produces, in the output directory:

| Output | What it is |
| --- | --- |
| `<name>.mp4` | H.264/AAC, `+faststart`, browser-playable |
| `<name>.json` | OTT custom media manifest |
| `<name>.thumb.jpg` | poster image (embedded cover art, else a sampled frame) |
| `<name>.<lang>.ass` | subtitle track(s), styling preserved |
| `<name>.<lang>.vtt` | same track(s), unstyled WebVTT fallback |

Naming follows the original filename verbatim: `somefile.mkv` →
`somefile.mkv.mp4`, `somefile.mkv.json`, `somefile.mkv.en.ass`, ...

> **Note on `.ass` subtitles.** This tool fully supports styled ASS/SSA subtitles, but styled-subtitle playback is still an **experimental** feature
> If your OTT build chokes on the styled tracks, set `OTT_SUB_FORMAT=vtt` to emit WebVTT only.

## Requirements

- Python 3.10+
- [`ffmpeg`](https://ffmpeg.org/) and `ffprobe` on your `PATH` (e.g. `apt install ffmpeg`)

## Usage

run the last release:
```bash
OTT_FILEUPLOAD_BASE_URL=https://host:port/path \
  uvx oot-customjson-preprocessor
```

run current main branch
```bash
OTT_FILEUPLOAD_BASE_URL=https://host:port/path \
  uvx --from git+https://github.com/bjuergens/oot-customjson-preprocessor oot-customjson-preprocessor
```

By default it reads `./input/` and writes `./output/`.

## Configuration

All configuration is via environment variables. `OTT_FILEUPLOAD_BASE_URL` is
**required**; the rest are optional.

| Variable | Default | Meaning |
| --- | --- | --- |
| `OTT_FILEUPLOAD_BASE_URL` | _(required)_ | Base URL the produced files will be served from |
| `OTT_INPUT_DIR` | `./input` | Where to read media from |
| `OTT_OUTPUT_DIR` | `./output` | Where to write outputs |
| `OTT_CRF` | `20` | x264 quality, lower = better |
| `OTT_PRESET` | `medium` | x264 preset |
| `OTT_KEYINT` | `2` | seconds between forced keyframes when reencoding |
| `OTT_KEYINT_MAX` | `10` | auto mode: max keyframe gap (s) tolerated before forcing a reencode |
| `OTT_ABR` | `192k` | audio bitrate |
| `OTT_SUB_LANG` | `en` | subtitle language to keep |
| `OTT_ALL_SUBS` | _(off)_ | set to `1` to export every text subtitle track |
| `OTT_REENCODE` | `auto` | `auto` \| `always` \| `never` (see below) |
| `OTT_SUB_FORMAT` | `both` | `both` \| `ass` \| `vtt` \| `none` |

### Reencode modes

- **`auto`** — try a stream copy first (an MKV/TS with browser-OK codecs just
  remuxes to MP4). If the video wouldn't direct-play (Hi10P/High-10, HEVC,
  4:2:2/4:4:4, or a keyframe gap > `OTT_KEYINT_MAX`) or the copy fails, fall back
  to an H.264 reencode. Audio is always transcoded to AAC.
- **`always`** — full reencode (video → H.264, all audio → AAC).
- **`never`** — copy every stream verbatim; warns when it copies something that
  may not play.

