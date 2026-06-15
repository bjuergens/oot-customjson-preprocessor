#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Prepare media for OpenTogetherTube (self-hosted, custom-media-format).

For every media file in INPUT_DIR this produces, in OUTPUT_DIR:
  <name>.mp4                 - H.264/AAC, +faststart, web-playable (see OTT_REENCODE)
  <name>.json                - OTT custom media manifest
  <name>.thumb.jpg           - poster image (embedded cover art, else a frame)
  <name>.<lang>.ass          - subtitle track(s), styling preserved
  <name>.<lang>.vtt          - same track(s), unstyled WebVTT fallback

By default every English-looking text subtitle track is exported (full, signs,
songs, CC, ...), with the most complete one marked as the OTT default;
non-English tracks are ignored. OTT_ALL_SUBS=1 exports every text track;
OTT_SUB_LANG=<code> changes the wanted language.

Naming follows the original filename verbatim: "somefile.mkv" ->
"somefile.mkv.mp4", "somefile.mkv.json", "somefile.mkv.en.ass", ...

Run:  uv run script.py
Config via env vars (OTT_FILEUPLOAD_BASE_URL is required, the rest optional):
  OTT_FILEUPLOAD_BASE_URL   REQUIRED, no default. Base URL the produced files
                   will be served from (e.g. https://host:port/path).
  OTT_INPUT_DIR    default: <script dir>/input
  OTT_OUTPUT_DIR   default: <script dir>/output
  OTT_CRF          x264 quality, lower=better (default: 20)
  OTT_PRESET       x264 preset (default: medium)
  OTT_KEYINT       seconds between forced keyframes when reencoding (default: 2)
  OTT_KEYINT_MAX   auto mode: max keyframe gap (s) in a copyable source before a
                   reencode is forced for seekability. Recommended 1-10 (default
                   10); outside that warns but is allowed; non-numeric is an error.
  OTT_ABR          audio bitrate (default: 192k)
  OTT_SUB_LANG     subtitle language to keep (default: en)
  OTT_ALL_SUBS     set to 1 to export every text subtitle track instead

  OTT_REENCODE     auto | always | never   (default: auto)
                     auto   - try a stream copy first (an MKV/TS with browser-OK
                              codecs just remuxes to MP4). If the video copy
                              would not direct-play (Hi10P/High-10, HEVC,
                              4:2:2/4:4:4, or a keyframe gap > OTT_KEYINT_MAX) or
                              the copy fails, fall back to an H.264 reencode.
                              Audio is always transcoded to AAC (all tracks
                              kept); only never mode copies audio as-is.
                     always - full reencode (video -> H.264, all audio -> AAC).
                     never  - copy every stream verbatim, never reencode. Fast,
                              but WARNS when it copies something that may not play.
  OTT_SUB_FORMAT   both | ass | vtt | none  (default: both)
                     which subtitle outputs to emit per selected track.
"""
from __future__ import annotations
import json
import math
import os
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import NoReturn
from urllib.parse import quote

SCRIPT_DIR = Path(__file__).resolve().parent
FILEUPLOAD_BASE_URL = os.environ.get("OTT_FILEUPLOAD_BASE_URL", "").strip().rstrip("/")
INPUT_DIR = Path(os.environ.get("OTT_INPUT_DIR", SCRIPT_DIR / "input"))
OUTPUT_DIR = Path(os.environ.get("OTT_OUTPUT_DIR", SCRIPT_DIR / "output"))
CRF = os.environ.get("OTT_CRF", "20")
PRESET = os.environ.get("OTT_PRESET", "medium")
KEYINT = os.environ.get("OTT_KEYINT", "2")
KEYINT_MAX = os.environ.get("OTT_KEYINT_MAX", "10")
ABR = os.environ.get("OTT_ABR", "192k")
# subtitle selection: default keeps every English-looking text track in SUB_LANG
SUB_LANG = os.environ.get("OTT_SUB_LANG", "en").strip().lower()
INCLUDE_ALL_SUBS = os.environ.get("OTT_ALL_SUBS", "").strip().lower() in ("1", "true", "yes", "on")

# reencode policy
REENCODE_MODE = os.environ.get("OTT_REENCODE", "auto").strip().lower()
# subtitle output formats
SUB_FORMAT = os.environ.get("OTT_SUB_FORMAT", "both").strip().lower()

# max chars for the per-track "name" shown in the OTT UI (kept conservative)
NAME_MAXLEN = 20

# containers we attempt; anything ffprobe reports as having a video stream is processed
MEDIA_EXTS = {".mkv", ".mp4", ".mov", ".avi", ".webm", ".ts", ".m4v", ".flv", ".wmv", ".mpg", ".mpeg", ".ogv"}
# text subtitle codecs we can turn into .ass/.vtt
TEXT_SUB_CODECS = {"ass", "ssa", "subrip", "srt", "mov_text", "webvtt", "text", "microdvd"}
# bitmap subs need OCR; we skip them and warn
BITMAP_SUB_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub", "dvbsub", "pgssub"}

# --- browser direct-play heuristics (what OTT's <video> can decode) ----------
# H.264 profiles a browser will decode. Hi10P / 4:2:2 / 4:4:4 / Extended are out.
BROWSER_OK_H264_PROFILES = {
    "constrained baseline", "baseline", "main",
    "high", "progressive high", "constrained high",
}
# 8-bit 4:2:0 only (yuvj = full-range, still 8-bit 4:2:0)
BROWSER_OK_PIX_FMTS = {"yuv420p", "yuvj420p"}
# audio codecs that play from an MP4 in essentially every browser.
# AAC is universal; MP3-in-MP4 also plays in Chrome+Firefox. Opus/FLAC in MP4
# are inconsistent across versions, and AC3/E-AC3/DTS aren't browser-supported,
# so everything outside this set is reencoded to AAC.
BROWSER_OK_AUDIO = {"aac", "mp3"}


def fail(msg: str) -> NoReturn:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def validate_config() -> None:
    if not FILEUPLOAD_BASE_URL:
        fail("OTT_FILEUPLOAD_BASE_URL is required (base URL the produced files "
             "will be served from, e.g. https://host:port/path)")
    if REENCODE_MODE not in ("auto", "always", "never"):
        fail(f"OTT_REENCODE must be auto|always|never, got: {REENCODE_MODE!r}")
    if SUB_FORMAT not in ("both", "ass", "vtt", "none"):
        fail(f"OTT_SUB_FORMAT must be both|ass|vtt|none, got: {SUB_FORMAT!r}")
    try:
        v = float(KEYINT_MAX)
    except ValueError:
        fail(f"OTT_KEYINT_MAX must be a number, got: {KEYINT_MAX!r}")
    if not math.isfinite(v):
        fail(f"OTT_KEYINT_MAX must be a finite number, got: {KEYINT_MAX!r}")
    if not (1.0 <= v <= 10.0):
        print(f"WARNING: OTT_KEYINT_MAX={v:g}s is outside the recommended 1-10s range "
              f"(allowed, but unusual)", file=sys.stderr)


def sub_formats() -> tuple[str, ...]:
    # ass first so it wins the `default` flag when both are emitted
    return {"both": ("ass", "vtt"), "ass": ("ass",), "vtt": ("vtt",), "none": ()}[SUB_FORMAT]


def check_tools() -> tuple[str, str]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg:
        fail("ffmpeg not found on PATH. Install it (e.g. `apt install ffmpeg`) and retry.")
    if not ffprobe:
        fail("ffprobe not found on PATH (normally ships with ffmpeg).")
    return ffmpeg, ffprobe


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def probe(ffprobe: str, path: Path) -> dict:
    """ffprobe a file to JSON. A bad return code or unparseable output is an
    unrecoverable failure -- we can't sensibly process what we can't read."""
    cp = run([ffprobe, "-v", "quiet", "-print_format", "json",
              "-show_format", "-show_streams", str(path)])
    if cp.returncode != 0:
        fail(f"ffprobe failed on {path} (rc={cp.returncode}): {cp.stderr.strip() or '?'}")
    try:
        return json.loads(cp.stdout)
    except json.JSONDecodeError as e:
        fail(f"ffprobe returned invalid JSON for {path}: {e}")


def tag(stream: dict, key: str) -> str:
    """Raw metadata tag value (stripped), '' if absent. e.g. language, title."""
    return ((stream.get("tags", {}) or {}).get(key, "") or "").strip()


def disposition(stream: dict, key: str) -> int:
    return int((stream.get("disposition", {}) or {}).get(key, 0) or 0)


def split_video_streams(streams: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (real_video_streams, cover-art/attached_pic streams).

    MP4s (and some MKVs) carry embedded poster art as an attached_pic video
    stream; it must not be mistaken for the main video or muxed into output."""
    vids = [s for s in streams if s.get("codec_type") == "video"]
    posters = [s for s in vids if disposition(s, "attached_pic")]
    real = [s for s in vids if not disposition(s, "attached_pic")]
    return real, posters


# title tokens that mark a partial (non-complete) track
SIGNS_TOKENS = ("sign", "song", "s&s", "s & s", "forced", "caption")


def is_partial_track(s: dict) -> bool:
    """Signs&Songs / forced / caption tracks don't carry full dialogue."""
    if disposition(s, "forced"):
        return True
    title_lc = tag(s, "title").lower()
    return any(tok in title_lc for tok in SIGNS_TOKENS)


def matches_lang(s: dict) -> bool:
    """Loose 'looks like SUB_LANG' test on the language tag or title."""
    lang = tag(s, "language").lower()
    title = tag(s, "title").lower()
    return lang.startswith(SUB_LANG) or SUB_LANG in lang or SUB_LANG in title


def select_subtitle_streams(sub_streams: list[dict]) -> tuple[list[dict], list[tuple[dict, str]]]:
    """Return (selected, skipped-with-reason).

    Default: every text track that looks like SUB_LANG (full, signs, songs, CC,
    ...), ordered so the most complete one is first -- it becomes the OTT
    default. With OTT_ALL_SUBS=1: every text track, in file order."""
    text, skipped = [], []
    for s in sub_streams:
        codec = s.get("codec_name", "")
        if codec in BITMAP_SUB_CODECS:
            skipped.append((s, "bitmap/needs-OCR"))
        elif codec not in TEXT_SUB_CODECS:
            skipped.append((s, f"unhandled:{codec}"))
        else:
            text.append(s)

    if INCLUDE_ALL_SUBS:
        return text, skipped

    wanted = []
    for s in text:
        if matches_lang(s):
            wanted.append(s)
        else:
            skipped.append((s, f"lang!={SUB_LANG}"))
    # most complete first (non-partial, default-flagged) -> becomes OTT default;
    # sort is stable, so ties keep their original file order.
    wanted.sort(key=lambda s: (is_partial_track(s), not disposition(s, "default")))
    return wanted, skipped


class CopyUnviable(Exception):
    """A direct stream-copy would produce a file a browser can't direct-play."""


# --- reencode decision -------------------------------------------------------
def check_video_copyable(vstream: dict) -> str:
    """Raise CopyUnviable if a browser couldn't direct-play a copy of this video
    stream; otherwise return a short descriptive note. (Hi10P/High-10, HEVC,
    4:2:2/4:4:4, etc. are the traps anime hits.)"""
    codec = vstream.get("codec_name", "")
    if codec != "h264":
        raise CopyUnviable(f"video {codec or '?'}")
    pix = (vstream.get("pix_fmt") or "").lower()
    if pix not in BROWSER_OK_PIX_FMTS:
        raise CopyUnviable(f"pix_fmt {pix or '?'}")
    prof = (vstream.get("profile") or "").lower()
    if prof and prof not in BROWSER_OK_H264_PROFILES:
        raise CopyUnviable(f"profile {vstream.get('profile')}")
    return f"h264/{pix}/8-bit"


def audio_not_browser_safe(astream: dict) -> bool:
    return astream.get("codec_name") not in BROWSER_OK_AUDIO


def max_keyframe_gap(ffprobe: str, src: Path, vindex: int, sample_seconds: int = 60) -> float | None:
    """Largest gap (s) between keyframes in the first `sample_seconds` of video.

    Demux-only (reads packet flags, no decode) so it's fast even on big files.
    Returns None if it can't be determined. If the sample window holds at most
    one keyframe, the real gap is at least the window length, so we report that
    (which will exceed any sane OTT_KEYINT_MAX and force a reencode)."""
    cp = run([ffprobe, "-v", "error", "-select_streams", str(vindex),
              "-read_intervals", f"%+{sample_seconds}",
              "-show_entries", "packet=pts_time,dts_time,flags",
              "-of", "csv=print_section=0", str(src)])
    if cp.returncode != 0 or not cp.stdout.strip():
        return None
    times: list[float] = []
    for line in cp.stdout.splitlines():
        parts = line.split(",")
        if len(parts) < 3 or "K" not in parts[2]:
            continue
        raw = parts[0] if parts[0] not in ("", "N/A") else parts[1]
        try:
            times.append(float(raw))
        except ValueError:
            continue
    if not times:
        return None
    if len(times) < 2:
        return float(sample_seconds)  # >= one full window with no second keyframe
    times.sort()
    return max(b - a for a, b in zip(times, times[1:]))


def build_av_cmd(ffmpeg: str, src: Path, out_mp4: Path, vindex: int,
                 copy_video: bool, copy_audio: bool) -> list[str]:
    # map the real video by absolute index (skips attached_pic) + every audio track
    cmd = [ffmpeg, "-y", "-i", str(src), "-map", f"0:{vindex}", "-map", "0:a?", "-sn"]
    if copy_video:
        cmd += ["-c:v", "copy"]
    else:
        cmd += ["-c:v", "libx264", "-crf", CRF, "-preset", PRESET, "-pix_fmt", "yuv420p",
                # seekability: force a keyframe every KEYINT seconds (reencode only)
                "-force_key_frames", f"expr:gte(t,n_forced*{KEYINT})"]
    # audio is always reencoded to AAC except in never mode (copy_audio=True)
    cmd += ["-c:a", "copy"] if copy_audio else ["-c:a", "aac", "-b:a", ABR]
    # faststart: move the moov atom to the front for instant web playback
    cmd += ["-movflags", "+faststart", str(out_mp4)]
    return cmd


def run_av(cmd: list[str], what: str) -> None:
    """Run an ffmpeg AV command; raise RuntimeError (with ffmpeg's last line) on failure."""
    cp = run(cmd)
    if cp.returncode != 0:
        last = cp.stderr.strip().splitlines()[-1] if cp.stderr else "?"
        raise RuntimeError(f"{what}: {last}")


def copy_flow(ffmpeg: str, ffprobe: str, src: Path, out_mp4: Path,
              vindex: int, vstream: dict, audio: list[dict]) -> None:
    """Fast path: copy the video stream verbatim, reencode audio to AAC. Raises
    CopyUnviable if a browser couldn't direct-play the copied video, or
    RuntimeError if ffmpeg fails -- either way the caller falls back to reencode."""
    note = check_video_copyable(vstream)  # raises CopyUnviable if not copyable
    gap = max_keyframe_gap(ffprobe, src, vindex)
    limit = float(KEYINT_MAX)
    if gap is None:
        note += ", keyframe gap unknown"
    elif gap > limit:
        raise CopyUnviable(f"keyframe gap {gap:.1f}s > {limit:g}s")
    else:
        note += f", keyframe gap {gap:.1f}s"
    print(f"      video: copy ({note})")
    print(f"      audio: {len(audio)} track(s) -> AAC" if audio else "      audio: none")
    run_av(build_av_cmd(ffmpeg, src, out_mp4, vindex, copy_video=True, copy_audio=False),
           "ffmpeg copy failed")


def reencode_flow(ffmpeg: str, src: Path, out_mp4: Path, vindex: int, audio: list[dict]) -> bool:
    print(f"      video: reencode -> H.264 8-bit (crf{CRF}/{PRESET}, keyframe/{KEYINT}s)")
    print(f"      audio: {len(audio)} track(s) -> AAC" if audio else "      audio: none")
    try:
        run_av(build_av_cmd(ffmpeg, src, out_mp4, vindex, copy_video=False, copy_audio=False),
               "reencode failed")
    except RuntimeError as exc:
        print(f"    ! {exc}")
        return False
    return True


def remux_flow(ffmpeg: str, src: Path, out_mp4: Path, vindex: int,
               vstream: dict, audio: list[dict]) -> bool:
    """never mode: copy every stream verbatim, warn on anything a browser may
    not play, and do NOT fall back to a reencode."""
    print("      reencode=never (remux only)")
    try:
        check_video_copyable(vstream)
    except CopyUnviable as e:
        print(f"      WARNING: copying video that may NOT direct-play ({e})")
    bad = sum(1 for a in audio if audio_not_browser_safe(a))
    if bad:
        print(f"      WARNING: copying {bad} audio track(s) that may NOT direct-play")
        print("      hint: OTT_REENCODE=auto re-encodes only the incompatible stream(s)")
    print(f"      audio: {len(audio)} track(s), copy" if audio else "      audio: none")
    try:
        run_av(build_av_cmd(ffmpeg, src, out_mp4, vindex, copy_video=True, copy_audio=True),
               "remux failed")
    except RuntimeError as exc:
        print(f"    ! {exc}")
        return False
    return True


def transcode(ffmpeg: str, ffprobe: str, src: Path, out_mp4: Path,
              vstream: dict, vindex: int, audio: list[dict]) -> bool:
    if len(audio) > 1:
        print(f"      note: keeping all {len(audio)} audio tracks "
              f"(OTT may only expose the first)")

    if REENCODE_MODE == "always":
        return reencode_flow(ffmpeg, src, out_mp4, vindex, audio)
    if REENCODE_MODE == "never":
        return remux_flow(ffmpeg, src, out_mp4, vindex, vstream, audio)

    # auto: attempt the copy flow first; fall back to a reencode on any non-fatal
    # failure. Interrupts (Ctrl-C), SystemExit and OOM must NOT become a reencode.
    try:
        copy_flow(ffmpeg, ffprobe, src, out_mp4, vindex, vstream, audio)
        return True
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as exc:
        print(f"      copy not used ({exc}) -> re-encoding instead")
        print("      hint: OTT_REENCODE=never copies as-is (may not play); "
              "OTT_KEYINT_MAX tunes the keyframe-gap limit")
        return reencode_flow(ffmpeg, src, out_mp4, vindex, audio)


def extract_sub(ffmpeg: str, src: Path, abs_index: int, out_path: Path, fmt: str, src_codec: str) -> bool:
    if fmt == "ass":
        scodec = "copy" if src_codec in ("ass", "ssa") else "ass"
    else:  # vtt
        scodec = "webvtt"
    cp = run([ffmpeg, "-y", "-i", str(src), "-map", f"0:{abs_index}",
              "-c:s", scodec, str(out_path)])
    if cp.returncode != 0:
        print(f"    ! {fmt} extract failed (stream {abs_index}): "
              f"{cp.stderr.strip().splitlines()[-1] if cp.stderr else '?'}")
        return False
    return True


def url_for(filename: str) -> str:
    return f"{FILEUPLOAD_BASE_URL}/{quote(filename)}"


def make_thumbnail(ffmpeg: str, src: Path, name: str, vindex: int,
                   posters: list[dict], duration: float) -> str | None:
    """Use embedded cover art if present, else grab a frame at ~30%.
    Returns the served URL, or None if both attempts fail."""
    out = OUTPUT_DIR / f"{name}.thumb.jpg"
    if posters:
        idx = int(posters[0]["index"])
        cp = run([ffmpeg, "-y", "-i", str(src), "-map", f"0:{idx}",
                  "-frames:v", "1", str(out)])
        if cp.returncode == 0 and out.exists():
            print(f"    -> {out.name}  (embedded cover art)")
            return url_for(out.name)
        print("    ~ cover-art extract failed; sampling a frame instead")
    ts = max(0.0, duration * 0.30)
    cp = run([ffmpeg, "-y", "-ss", f"{ts:.3f}", "-i", str(src),
              "-map", f"0:{vindex}", "-frames:v", "1", "-q:v", "2", str(out)])
    if cp.returncode == 0 and out.exists():
        print(f"    -> {out.name}  (frame @ {ts:.0f}s)")
        return url_for(out.name)
    print("    ~ thumbnail generation failed; manifest will omit it")
    return None


def build_display_names(selected: list[dict]) -> list[str]:
    """Prefer the original track title; only number when labels would collide.

    e.g. tracks titled "Full", "Signs/Songs", "Closed Captions" keep those
    names verbatim; two tracks that would otherwise both read "English" become
    "English 1" / "English 2"."""
    raw = []
    for s in selected:
        raw.append(tag(s, "title") or tag(s, "language").upper() or "Sub")
    counts = Counter(raw)
    seen: dict[str, int] = defaultdict(int)
    out = []
    for b in raw:
        if counts[b] > 1:
            seen[b] += 1
            out.append(f"{b} {seen[b]}")
        else:
            out.append(b)
    return out


def describe_sub(s: dict) -> str:
    """Short identifier for a subtitle stream, e.g. 'de "Deutsch (Full)"'."""
    code = tag(s, "language") or "und"
    title = tag(s, "title")
    return f'{code} "{title}"' if title else code


def process_file(ffmpeg: str, ffprobe: str, src: Path) -> bool:
    name = src.name  # e.g. "somefile.mkv" -> outputs are "somefile.mkv.*"
    title = src.stem  # "somefile"
    print(f"\n=== {name} ===")

    info = probe(ffprobe, src)
    streams = info.get("streams", [])
    real_video, posters = split_video_streams(streams)
    if not real_video:
        print("    skip: no video stream")
        return False
    vstream = real_video[0]
    vindex = int(vstream["index"])
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    height = int(vstream.get("height") or 720)
    try:
        src_duration = float(info.get("format", {}).get("duration", 0) or 0)
    except (TypeError, ValueError):
        src_duration = 0.0

    # 1) video/audio -> mp4 (copy-first, reencode fallback; see OTT_REENCODE)
    out_mp4 = OUTPUT_DIR / f"{name}.mp4"
    print(f"    -> {out_mp4.name}")
    if not transcode(ffmpeg, ffprobe, src, out_mp4, vstream, vindex, audio):
        return False

    # 1b) poster image: embedded cover art, else a frame from the video
    thumb_url = make_thumbnail(ffmpeg, src, name, vindex, posters, src_duration)

    # exact duration from the produced mp4
    out_info = probe(ffprobe, out_mp4)
    try:
        duration = round(float(out_info.get("format", {}).get("duration")), 3)
    except (TypeError, ValueError):
        duration = round(float(info.get("format", {}).get("duration", 0) or 0), 3)

    # 2) subtitle tracks -> .ass / .vtt   (default: English-looking tracks)
    formats = sub_formats()
    sub_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
    selected, skipped = select_subtitle_streams(sub_streams)
    for s, reason in skipped:
        print(f"    ~ sub stream {int(s['index'])} [{describe_sub(s)}]: ignored ({reason})")
    if formats and sub_streams and not selected:
        print(f"    ~ no subtitle track selected (wanted lang={SUB_LANG}); "
              f"set OTT_SUB_LANG=<code> or OTT_ALL_SUBS=1")

    text_tracks: list[dict] = []
    if formats and selected:
        disp_names = build_display_names(selected)
        seen_codes: dict[str, int] = {}
        first = True
        for s, disp in zip(selected, disp_names):
            codec = s.get("codec_name", "")
            abs_index = int(s["index"])
            code = tag(s, "language") or "und"
            n = seen_codes.get(code, 0)
            seen_codes[code] = n + 1
            label = code if n == 0 else f"{code}{n + 1}"  # unique filename per code

            for fmt in formats:
                out_name = f"{name}.{label}.{fmt}"
                if not extract_sub(ffmpeg, src, abs_index, OUTPUT_DIR / out_name, fmt, codec):
                    continue
                print(f"    -> {out_name}  [{disp}]")
                suffix = f" ({fmt.upper()})"
                ui_name = disp[:NAME_MAXLEN - len(suffix)] + suffix
                text_tracks.append({
                    "url": url_for(out_name),
                    "contentType": "text/x-ass" if fmt == "ass" else "text/vtt",
                    "name": ui_name,
                    "srclang": code,
                    "default": first,
                })
                first = False
    elif not formats:
        print("    ~ subtitles disabled (OTT_SUB_FORMAT=none)")

    # 3) manifest
    manifest = {
        "title": title[:100],
        "duration": duration,
        "live": False,
        "sources": [{
            "url": url_for(out_mp4.name),
            "contentType": "video/mp4",
            "quality": height,
        }],
        "textTracks": text_tracks,
    }
    if thumb_url:
        manifest["thumbnail"] = thumb_url
    out_json = OUTPUT_DIR / f"{name}.json"
    out_json.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(f"    -> {out_json.name}  ({len(text_tracks)} text track(s), {duration}s, {height}p)")
    return True


def main() -> None:
    validate_config()
    ffmpeg, ffprobe = check_tools()
    if not INPUT_DIR.is_dir():
        fail(f"input dir not found: {INPUT_DIR}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    candidates = sorted(
        p for p in INPUT_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in MEDIA_EXTS
    )
    if not candidates:
        print(f"No media files found in {INPUT_DIR}")
        return

    print(f"ffmpeg   : {ffmpeg}")
    print(f"base     : {FILEUPLOAD_BASE_URL}")
    print(f"input    : {INPUT_DIR}")
    print(f"output   : {OUTPUT_DIR}")
    print(f"reencode : {REENCODE_MODE}" + (f" (max keyframe gap {KEYINT_MAX}s)" if REENCODE_MODE == "auto" else ""))
    print(f"subs     : {SUB_FORMAT} (lang={SUB_LANG}, all={'yes' if INCLUDE_ALL_SUBS else 'no'})")
    print(f"files    : {len(candidates)}")

    ok = 0
    for p in candidates:
        try:
            ok += bool(process_file(ffmpeg, ffprobe, p))
        except Exception as e:  # one bad file shouldn't abort the batch
            print(f"    ! unexpected error on {p.name}: {e}")
    print(f"\nDone: {ok}/{len(candidates)} processed -> {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
