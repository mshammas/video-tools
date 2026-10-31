"""The option combinations whose yt-dlp/ffmpeg command lines are pinned.

Each case names a golden file under tests/golden/invocations/. The goldens were
captured from yt-download.sh, the bash script yt_download.py replaced, so they
record the behaviour the port had to preserve rather than whatever the Python
happens to do today. See tests/README.md.

Adding a case means capturing its golden too, which can no longer be done from
the old script — write the expected text by hand and say why in the review.
"""

from typing import NamedTuple

from support import BATCH, MISSING, NO_BATCH, OUT, SRC

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
URL2 = "https://youtu.be/abc"
PLAYLIST = "https://www.youtube.com/playlist?list=PLtest"
CHANNEL_VIDEOS = "https://www.youtube.com/@someone/videos"
SHORT = "https://www.youtube.com/shorts/abc123"
WATCH_IN_PLAYLIST = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLtest"
FAIL_403 = "https://youtu.be/FAIL403"
FAIL_PLAIN = "https://youtu.be/FAILPLAIN"


class Case(NamedTuple):
    name: str
    argv: list
    # yt-dlp's reported download size, for the disk-space check.
    size: str = "1000"
    # False mimics --skip-existing finding the file already there: yt-dlp moves
    # nothing into place, so it prints no output path.
    write_path: bool = True


CASES = [
    # --- the download and option matrix ---------------------------------
    Case("default-mp4", ["-o", OUT, URL]),
    Case("mkv", ["-o", OUT, "--mkv", URL]),
    Case("max-height", ["-o", OUT, "-m", "1080", URL]),
    Case("mkv-max-height", ["-o", OUT, "--mkv", "-m", "720", URL]),
    Case("container-passthrough", ["-o", OUT, "-c", "mp4", URL]),
    Case("keep-and-skip-existing", ["-o", OUT, "-k", "--skip-existing", URL]),
    Case("list-formats", ["-F", URL]),
    Case("list-formats-two-urls", ["-F", URL, URL2]),
    Case("chapters", ["-o", OUT, "-C", URL]),
    Case("chapters-precise", ["-o", OUT, "-C", "-P", URL]),
    Case("chapters-segments-only", ["-o", OUT, "-C", "--segments-only", URL]),
    Case("strict", ["-o", OUT, "-s", URL]),
    Case("two-urls", ["-o", OUT, URL, URL2]),

    # --- playlist expansion ----------------------------------------------
    Case("playlist-expand", ["-o", OUT, PLAYLIST]),
    Case("playlist-no-expand", ["-o", OUT, "--no-playlist-expand", PLAYLIST]),
    Case("channel-videos-expand", ["-o", OUT, CHANNEL_VIDEOS]),
    # A Short is a single video; expanding it would be wrong.
    Case("short-is-one-video", ["-o", OUT, SHORT]),
    Case("watch-in-playlist-default", ["-o", OUT, WATCH_IN_PLAYLIST]),
    Case("watch-in-playlist-yes", ["-o", OUT, "--yes-playlist", WATCH_IN_PLAYLIST]),

    # --- splitting a file we already have --------------------------------
    Case("split-file", ["--split-file", SRC, URL]),
    Case("split-file-precise", ["--split-file", SRC, "-P", URL]),
    Case("split-file-segments-only", ["--split-file", SRC, "--segments-only", URL]),
    Case("split-file-two-urls", ["--split-file", SRC, URL, URL2]),
    Case("split-file-missing", ["--split-file", MISSING, URL]),

    # --- batches ----------------------------------------------------------
    Case("batch-file", ["-o", OUT, "-b", BATCH]),
    Case("batch-file-and-cli-url", ["-o", OUT, "-b", BATCH, "https://youtu.be/zzz"]),
    Case("batch-file-missing", ["-o", OUT, "-b", NO_BATCH]),

    # --- failures ---------------------------------------------------------
    Case("plain-failure", ["-o", OUT, FAIL_PLAIN]),
    Case("http-403-non-strict", ["-o", OUT, FAIL_403]),
    Case("http-403-strict", ["-o", OUT, "-s", FAIL_403]),
    Case("batch-carries-on", ["-o", OUT, FAIL_PLAIN, URL]),
    Case("batch-stop-on-error", ["-o", OUT, "--stop-on-error", FAIL_PLAIN, URL]),
    Case("precise-without-chapters", ["-o", OUT, "-P", URL]),
    Case("segments-only-without-chapters", ["-o", OUT, "--segments-only", URL]),
    Case("out-of-space", ["-o", OUT, URL], size="999999999999999"),
    Case("chapters-already-downloaded", ["-o", OUT, "-C", "--skip-existing", URL],
         write_path=False),
]


# ---------------------------------------------------------------------------
# Chapter discovery. Each entry is a yt-dlp info dict; the golden holds the
# chapters the old script's embedded Python helper derived from it, as TSV.
# ---------------------------------------------------------------------------

CHAPTER_CASES = {
    "native": {
        "duration": 300,
        "chapters": [{"start_time": 0, "end_time": 100, "title": "Intro"},
                     {"start_time": 100, "end_time": 300, "title": "Body"}],
    },
    "native-no-end-times": {
        "duration": 300,
        "chapters": [{"start_time": 0, "title": "A"},
                     {"start_time": 120, "title": "B"}],
    },
    "native-unsafe-title-chars": {
        "duration": 60,
        "chapters": [{"start_time": 0, "end_time": 60,
                      "title": 'a/b\\c:d*e?f"g<h>i|j   k'}],
    },
    "native-empty-title": {
        "duration": 60,
        "chapters": [{"start_time": 0, "end_time": 60, "title": "   "}],
    },
    "native-zero-length-span": {
        "duration": 100,
        "chapters": [{"start_time": 0, "end_time": 0, "title": "Zero"},
                     {"start_time": 10, "end_time": 100, "title": "Real"}],
    },
    "native-missing-start-time": {
        "duration": 100,
        "chapters": [{"end_time": 50, "title": "No start"},
                     {"start_time": 50, "end_time": 100, "title": "Fine"}],
    },
    "native-beats-description": {
        "duration": 300,
        "chapters": [{"start_time": 0, "end_time": 300, "title": "Native"}],
        "description": "0:00 Desc one\n1:00 Desc two",
    },
    "description-plain": {
        "duration": 400,
        "description": "0:00 Intro\n1:30 Middle\n5:00 End",
    },
    "description-dashes-and-brackets": {
        "duration": 7400,
        "description": "(0:00) - Welcome\n[1:02:03] — Deep dive\n2:03:00 | Q&A",
    },
    # One mark is not enough to trust a description parse.
    "description-single-mark": {
        "duration": 300,
        "description": "0:00 Only one",
    },
    "description-duplicate-starts": {
        "duration": 300,
        "description": "0:00 First\n0:00 Duplicate\n2:00 Second",
    },
    "description-out-of-order": {
        "duration": 300,
        "description": "4:00 Later\n0:00 Earlier\n2:00 Middle",
    },
    "description-hours": {
        "duration": 10000,
        "description": "0:00 Start\n1:00:00 Hour one\n2:30:45 Two thirty",
    },
    "description-noise-lines": {
        "duration": 300,
        "description": "Follow me!\n0:00 Intro\nhttp://example.com\n2:00 Outro\nthanks",
    },
    "description-no-duration": {
        "description": "0:00 A\n1:00 B",
    },
    "description-unsafe-title-chars": {
        "duration": 300,
        "description": "0:00 a/b:c\n2:00 d*e?f",
    },
    "nothing-to-go-on": {"duration": 300},
    "empty-info": {},
}


# ---------------------------------------------------------------------------
# Which links name a listing worth expanding into its videos. The golden holds
# the old script's looks_like_playlist() verdict for each.
# ---------------------------------------------------------------------------

PLAYLIST_URLS = [
    "https://www.youtube.com/watch?v=abc",
    "https://youtu.be/abc",
    "https://www.youtube.com/playlist?list=PL123",
    "https://www.youtube.com/watch?v=abc&list=PL123",
    "https://www.youtube.com/@someone/videos",
    "https://www.youtube.com/@someone/videos/",
    "https://www.youtube.com/@someone/videos?view=0",
    "https://www.youtube.com/c/Channel/videos",
    "https://www.youtube.com/@someone/shorts",
    "https://www.youtube.com/shorts/abc123",
    "https://www.youtube.com/shorts/abc123?feature=share",
    "https://www.youtube.com/@someone/streams",
    "https://www.youtube.com/@someone",
    "https://www.youtube.com/@someone/videos/extra",
    "https://www.youtube.com/@someone/featured",
]
