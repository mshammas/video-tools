"""Test helpers: stand-ins for the tools yt_download.py drives.

yt_download.py's whole job is to build correct yt-dlp and ffmpeg command lines,
so the tests replace the subprocess module inside it and record what it would
have run. Nothing is downloaded and neither tool has to be installed, which
also means these tests run identically on macOS, Linux and Windows.
"""

import io
import json
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
if str(REPO_ROOT) not in sys.path:          # so `import yt_download` works
    sys.path.insert(0, str(REPO_ROOT))

GOLDEN_DIR = TESTS_DIR / "golden"

# A version far enough in the future that the staleness check stays quiet, so
# the goldens don't rot as the clock moves.
FAKE_YTDLP_VERSION = "2099.01.01"

# The info JSON the fake yt-dlp answers `-J` with: three native chapters, one
# of them carrying characters no filesystem accepts.
DEFAULT_INFO = {
    "duration": 300,
    "title": "T",
    "chapters": [
        {"start_time": 0, "end_time": 100, "title": "One: intro"},
        {"start_time": 100, "end_time": 200, "title": 'Two/bad*chars?'},
        {"start_time": 200, "end_time": 300, "title": "Three"},
    ],
}

# What the fake yt-dlp answers `--flat-playlist` with: one entry that carries a
# URL, one that only has an id, and a blank line.
PLAYLIST_LISTING = "https://www.youtube.com/watch?v=aaa|aaa\nNA|bbb\n\n"

# Markers in a case's argv, swapped for real paths when the case runs. Keeping
# them out of the goldens is what makes the goldens machine-independent.
OUT = "@OUT@"            # the output directory
SRC = "@SRC@"            # an existing local file, for --split-file
MISSING = "@MISSING@"    # a path that does not exist
BATCH = "@BATCH@"        # an existing batch file holding two URLs
NO_BATCH = "@NOBATCH@"   # a batch file that does not exist
# The directory holding all of the above. Substituted last, so it catches paths
# the tool derives for itself -- chapter segments land in a folder named after
# the video, which is not any of the markers above.
ROOT = "@ROOT@"

BATCH_FILE_BODY = (
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ\n"
    "\n"
    "# a comment\n"
    "  https://youtu.be/abc  \n"
)

class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


class FakePopen:
    """Just enough of Popen for run_download()'s --strict branch."""

    def __init__(self, returncode, output):
        self.returncode = returncode
        self.stdout = io.BytesIO(output.encode())

    def wait(self):
        return self.returncode


class FakeSubprocess:
    """Replaces the subprocess module inside yt_download, recording every call.

    Answers the probe modes the way yt-dlp does (--version, -J, --simulate,
    --flat-playlist) and lets a test force a download to fail.
    """

    # yt_download refers to these constants when wiring up stdio.
    DEVNULL = -3
    PIPE = -1
    STDOUT = -2

    def __init__(self, out_dir, info=None, size="1000", write_path=True):
        self.calls = []
        self.out_dir = Path(out_dir)
        self.info = DEFAULT_INFO if info is None else info
        self.size = size
        self.write_path = write_path

    # -- recording ---------------------------------------------------------

    def run(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        code, output = self._respond(cmd)
        return FakeCompletedProcess(code, output)

    def Popen(self, cmd, **kwargs):           # noqa: N802 - mirrors subprocess
        self.calls.append(list(cmd))
        code, output = self._respond(cmd)
        return FakePopen(code, output)

    # -- behaviour ---------------------------------------------------------

    def _respond(self, cmd):
        cmd = list(cmd)
        if Path(cmd[0]).name.startswith("ffmpeg"):
            return 0, ""
        if "--version" in cmd:
            return 0, FAKE_YTDLP_VERSION + "\n"
        if "-J" in cmd:
            return 0, json.dumps(self.info)
        if "--flat-playlist" in cmd:
            return 0, PLAYLIST_LISTING
        if "--simulate" in cmd:
            return 0, self.size + "\n"
        if "-F" in cmd:
            return 0, "(format table)\n"
        return self._download(cmd)

    def _download(self, cmd):
        url = cmd[-1]
        if "FAIL403" in url:
            return 1, "ERROR: unable to download video data: HTTP Error 403: Forbidden\n"
        if "FAILPLAIN" in url:
            return 1, "ERROR: something else went wrong\n"
        if "--print-to-file" in cmd and self.write_path:
            # Stand in for the file yt-dlp would have produced, so the chapter
            # split has something real to work on.
            landed = self.out_dir / "video [id].mp4"
            landed.parent.mkdir(parents=True, exist_ok=True)
            landed.write_bytes(b"\0" * 16)
            dest = cmd[cmd.index("--print-to-file") + 2]
            Path(dest).write_text(str(landed) + "\n", encoding="utf-8")
        return 0, ""


def normalise(calls, paths):
    """Render recorded argv as the machine-independent text held in a golden.

    Real paths become their markers and separators are forced to '/', so one
    golden covers all three platforms.
    """
    lines = []
    for cmd in calls:
        tool = Path(cmd[0]).name
        args = [str(a) for a in cmd[1:]]
        # The temp file --print-to-file writes the output path into has a random
        # name, so blank it by position rather than by matching its text: on
        # Windows %TEMP% may contain a space, which no path pattern survives.
        if "--print-to-file" in args:
            target = args.index("--print-to-file") + 2
            if target < len(args):
                args[target] = "@PATHFILE@"
        out = []
        for arg in args:
            for marker, actual in paths.items():
                if actual:
                    arg = arg.replace(str(actual), marker)
            out.append(arg.replace("\\", "/"))
        lines.append(f"{tool} {json.dumps(out)}")
    return lines


def golden_text(exit_code, lines):
    return "\n".join([f"exit {exit_code}"] + lines) + "\n"


class CapturedStdout:
    """yt_download writes yt-dlp's piped output to sys.stdout.buffer."""

    def __enter__(self):
        self._real = sys.stdout
        sys.stdout = io.TextIOWrapper(io.BytesIO())
        return self

    def __exit__(self, *exc):
        sys.stdout = self._real
        return False


def materialise(case, root):
    """Swap a case's path markers for real paths under `root`.

    Returns (argv, marker->path map, output dir). The map is what normalise()
    uses to put the markers back, so a golden never holds a real path.
    """
    root = Path(root)
    out = root / "out"
    out.mkdir(parents=True, exist_ok=True)
    src = root / "source.mp4"
    src.write_bytes(b"\0" * 16)
    batch = root / "batch.txt"
    batch.write_text(BATCH_FILE_BODY, encoding="utf-8")
    paths = {
        OUT: out,
        SRC: src,
        BATCH: batch,
        MISSING: root / "does-not-exist.mp4",
        NO_BATCH: root / "does-not-exist.txt",
        ROOT: root,                       # last: the catch-all, see above
    }
    argv = [str(paths.get(arg, arg)) for arg in case.argv]
    return argv, paths, out
