"""Unit tests for the pure helpers in the preprocessor.

These cover the metadata/decision logic that doesn't need ffmpeg or real files.
The ffmpeg-driven flows (transcode/extract/thumbnail) are integration territory
and are not exercised here.
"""
from __future__ import annotations

import pytest

from ott_customjson_preprocessor import preprocessor as p


def stream(codec_type="subtitle", **kw):
    s = {"codec_type": codec_type}
    s.update(kw)
    return s


def test_tag_reads_and_strips():
    s = stream(tags={"language": "  eng ", "title": "Full"})
    assert p.tag(s, "language") == "eng"
    assert p.tag(s, "title") == "Full"
    assert p.tag(s, "missing") == ""


def test_disposition_defaults_to_zero():
    assert p.disposition(stream(), "default") == 0
    assert p.disposition(stream(disposition={"default": 1}), "default") == 1


def test_split_video_streams_separates_cover_art():
    real = stream("video", index=0)
    poster = stream("video", index=2, disposition={"attached_pic": 1})
    reals, posters = p.split_video_streams([real, poster, stream("audio", index=1)])
    assert reals == [real]
    assert posters == [poster]


def test_is_partial_track_detects_forced_and_titles():
    assert p.is_partial_track(stream(disposition={"forced": 1}))
    assert p.is_partial_track(stream(tags={"title": "Signs & Songs"}))
    assert not p.is_partial_track(stream(tags={"title": "Full Dialogue"}))


def test_check_video_copyable_accepts_browser_safe_h264():
    note = p.check_video_copyable({"codec_name": "h264", "pix_fmt": "yuv420p", "profile": "High"})
    assert "h264" in note


@pytest.mark.parametrize("vstream", [
    {"codec_name": "hevc", "pix_fmt": "yuv420p"},
    {"codec_name": "h264", "pix_fmt": "yuv420p10le"},
    {"codec_name": "h264", "pix_fmt": "yuv420p", "profile": "High 10"},
])
def test_check_video_copyable_rejects_unplayable(vstream):
    with pytest.raises(p.CopyUnviable):
        p.check_video_copyable(vstream)


def test_build_display_names_numbers_only_on_collision():
    selected = [
        stream(tags={"title": "Full"}),
        stream(tags={"language": "eng"}),
        stream(tags={"language": "eng"}),
    ]
    assert p.build_display_names(selected) == ["Full", "ENG 1", "ENG 2"]


def test_select_subtitle_streams_skips_bitmap_and_picks_complete_first():
    full = stream(codec_name="ass", tags={"language": "eng", "title": "Full"})
    signs = stream(codec_name="ass", tags={"language": "eng", "title": "Signs"})
    bitmap = stream(codec_name="hdmv_pgs_subtitle", tags={"language": "eng"})
    selected, skipped = p.select_subtitle_streams([signs, full, bitmap])
    assert selected[0] is full  # most complete first -> becomes OTT default
    assert any("bitmap" in reason for _, reason in skipped)
