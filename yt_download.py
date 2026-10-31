#!/usr/bin/env python3
"""Download a YouTube video in its highest available resolution.

Examples:
    ./yt_download.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    ./yt_download.py --mkv -o ~/Videos URL1 URL2
    ./yt_download.py --chapters "https://www.youtube.com/playlist?list=PL..."
    ./yt_download.py --split-file ~/Videos/talk.mp4 URL

Several URLs (or --batch-file) download one after another, and a playlist URL
expands into the videos it contains.

MP4 is the default output (H.264 + AAC, remuxed with +faststart): the most
compatible result, playing in QuickTime, iOS and virtually everywhere. Because
H.264 tops out at 1080p on YouTube, use --mkv when you want the highest
resolution (1440p/4K are served only as VP9/AV1, which live in MKV).

Requires: yt-dlp and ffmpeg. Missing tools are installed automatically (see
deps.py) unless --no-install is given.

Runs anywhere Python does: macOS, Linux and Windows alike, with no shell needed.
"""

# Annotations as strings, so the 'X | None' forms below stay valid on the
# Python 3.8 this project supports.
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

import deps

# Multiplier over the estimated download size that must be free on the
# destination disk: the merge (and, for MP4, the remux; for --chapters, the
# split) briefly holds a second full copy alongside the original.
SPACE_SAFETY = 2

# yt-dlp builds older than this are worth a warning: YouTube changes its
# defenses often and breaks stale builds, usually as "HTTP Error 403".
STALE_DAYS = 30

# Exit codes the GUI and shell callers rely on.
EXIT_STRICT_403 = 3
EXIT_NO_SPACE = 4

# Characters no filesystem we target accepts in a name.
UNSAFE_CHARS_RE = re.compile(r'[\\/:*?"<>|]+')

# A description line that opens with a timestamp, e.g. "0:00 Intro",
# "01:23 - Topic", "(1:02:03) Section".
DESC_TS_RE = re.compile(
    r'^\s*[\[\(]?\s*((?:\d{1,2}:)?\d{1,2}:\d{2})\s*[\]\)]?\s*[-–—:|.]*\s*(.*?)\s*$')

# What yt-dlp reports as a blocked (usually stale) build.
FORBIDDEN_RE = re.compile(r"HTTP Error 403|403: Forbidden|Forbidden", re.I)


class Chapter(NamedTuple):
    start: float
    end: float
    title: str


def log(*parts: object) -> None:
    """Progress and diagnostics go to stderr, unbuffered, as the old script did.

    The GUI merges our stderr into stdout and reads it line by line, so every
    message must reach it immediately rather than sitting in a block buffer.
    """
    print(*parts, file=sys.stderr, flush=True)


def require(*tools: str, auto_install: bool = True) -> None:
    """Ensure each tool is available, auto-installing any that are missing."""
    missing = deps.ensure(list(tools), auto_install=auto_install, on_log=log)
    if missing:
        lines = "\n".join(f"  - {t}: {deps.hint(t)}" for t in missing)
        verb = "are not installed" if not auto_install else "could not be installed"
        log(f"Error: required tool(s) {verb}:\n{lines}")
        if "yt-dlp" in missing:
            log("  (if pip used --user, add its bin dir to PATH, e.g. "
                "`python3 -m site --user-base`/bin)")
        sys.exit(1)


def human(num_bytes: float) -> str:
    """Format a byte count the way the shell's awk helper did: '1.4 GB'."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} PB"


# ---------------------------------------------------------------------------
# yt-dlp housekeeping: freshness warning and --update.
# ---------------------------------------------------------------------------

def ytdlp_version() -> str | None:
    try:
        out = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True)
    except OSError:
        return None
    return out.stdout.strip() or None


def version_age_days(version: str) -> int | None:
    """Age of a date-stamped yt-dlp version (YYYY.MM.DD[...]), or None."""
    fields = version.split(".")[:3]
    if len(fields) != 3 or not all(f.isdigit() for f in fields):
        return None
    try:
        released = datetime.date(*(int(f) for f in fields))
    except ValueError:
        return None
    return (datetime.date.today() - released).days


def check_ytdlp_freshness(strict: bool) -> None:
    """Warn (or, under --strict, refuse) when the yt-dlp on PATH looks stale.

    yt-dlp versions are date-stamped, so this stays a purely offline check.
    """
    version = ytdlp_version()
    if not version:
        return
    age = version_age_days(version)
    if age is None or age <= STALE_DAYS:
        return
    log(f"{'Error' if strict else 'Warning'}: yt-dlp is {age} days old (version {version}).")
    log("       YouTube may reject downloads (HTTP 403) with a stale build.")
    log(f"       Update the copy on your PATH ({shutil.which('yt-dlp')}):")
    log('         python3 -m pip install -U --pre "yt-dlp[default]"')
    log(f"       (or run: {Path(sys.argv[0]).name} --update, or: brew upgrade yt-dlp)")
    log("")
    if strict:
        sys.exit(1)


def update_ytdlp() -> int:
    """Update the yt-dlp on PATH, picking the method that matches its install.

    Installs vary (pip, Homebrew, standalone binary), so rather than assuming
    one, follow the evidence: a pip install's shebang names the very interpreter
    whose pip must do the upgrade.
    """
    path = shutil.which("yt-dlp")
    if not path:
        log("Error: yt-dlp is not on PATH; nothing to update.")
        return 1
    log(f"Updating yt-dlp at: {path}")
    try:
        with open(path, "rb") as fh:
            shebang = fh.readline(512).decode("utf-8", "replace").strip()
    except OSError:
        shebang = ""
    if shebang.startswith("#!") and "python" in shebang:
        # Handles both an absolute path and a `/usr/bin/env python3` form.
        interp = shebang[2:].split()
        cmd = [*interp, "-m", "pip", "install", "-U", "--pre", "yt-dlp[default]"]
    elif "/Cellar/" in path or "/homebrew/" in path:
        cmd = ["brew", "upgrade", "yt-dlp"]
    else:
        # Standalone binary: yt-dlp can self-update in place.
        cmd = ["yt-dlp", "-U", "--update-to", "nightly"]
    log("Running: " + " ".join(cmd))
    return subprocess.run(cmd).returncode


# ---------------------------------------------------------------------------
# Format selection.
# ---------------------------------------------------------------------------

def build_format(max_height: str | None, remux_mp4: bool) -> str:
    """The yt-dlp format selector.

    Every preferred alternative pairs a video-only stream ([acodec=none]) with
    an audio-only stream ([vcodec=none]) — i.e. true adaptive/DASH streams that
    we merge ourselves. This deliberately excludes YouTube's legacy
    *progressive* formats (e.g. format 18, which carries both codecs in one
    file): those are frequently rejected with "HTTP 403: Forbidden", and low
    -m caps used to fall onto them. A bare progressive file ("b") remains only
    as the last-ditch fallback for videos that genuinely offer nothing else.
    """
    hf = f"[height<={max_height}]" if max_height else ""
    vonly = "bv*[acodec=none]"          # adaptive video-only (no progressive)
    aonly = "ba[vcodec=none]"           # adaptive audio-only
    if not remux_mp4:
        return f"{vonly}{hf}+{aonly}/b{hf}"
    # For MP4 output, prefer H.264 (avc1) video + AAC (mp4a) audio so the remux
    # stays lossless (VP9/AV1 would force a re-encode into MP4). Widen in steps:
    # avc1+aac -> avc1+any audio -> any adaptive video+audio, then last-resort
    # progressive.
    return (f"{vonly}[vcodec^=avc1]{hf}+{aonly}[acodec^=mp4a]"
            f"/{vonly}[vcodec^=avc1]{hf}+{aonly}"
            f"/{vonly}{hf}+{aonly}"
            f"/b{hf}")


def build_base_args(args: argparse.Namespace, fmt: str) -> list[str]:
    """The yt-dlp arguments every URL in the batch shares.

    --no-playlist keeps each item one video: playlist links are expanded into
    their videos up front, so everything downstream handles a single video.
    """
    base = [
        "--format", fmt,
        "--output", str(Path(args.outdir) / "%(title)s [%(id)s].%(ext)s"),
        "--embed-metadata",
        "--embed-thumbnail",
        "--no-playlist",
    ]
    if args.keep:
        base.append("--keep-video")
    if args.skip_existing:
        base.append("--no-overwrites")
    if args.remux_mp4:
        # Merge to MKV (accepts any codec, always succeeds), then remux to MP4
        # without re-encoding. +faststart moves the index to the front so the
        # file streams/seeks smoothly (QuickTime, AirPlay, web).
        base += [
            "--merge-output-format", "mkv",
            "--remux-video", "mp4",
            "--postprocessor-args", "VideoRemuxer:-movflags +faststart",
        ]
    else:
        base += ["--merge-output-format", args.container]
    return base


# ---------------------------------------------------------------------------
# Chapters. Prefer the video's native chapters; otherwise parse timestamps out
# of its description.
# ---------------------------------------------------------------------------

def sanitize(title: str) -> str:
    title = UNSAFE_CHARS_RE.sub(" ", title)
    return re.sub(r"\s+", " ", title).strip() or "Untitled"


def timestamp_to_secs(stamp: str) -> int:
    secs = 0
    for part in stamp.split(":"):
        secs = secs * 60 + int(part)
    return secs


def chapters_from_info(info: dict) -> list[Chapter]:
    """Turn one video's yt-dlp info dict into a chapter list."""
    duration = info.get("duration") or 0
    native = info.get("chapters") or []
    spans: list[list] = []

    if native:
        for chapter in native:
            start = chapter.get("start_time")
            if start is None:
                continue
            end = chapter.get("end_time")
            spans.append([float(start),
                          float(end) if end is not None else None,
                          (chapter.get("title") or "").strip()])
    else:
        marks = []
        for line in (info.get("description") or "").splitlines():
            match = DESC_TS_RE.match(line)
            if match:
                marks.append((timestamp_to_secs(match.group(1)), match.group(2).strip()))
        marks.sort(key=lambda m: m[0])
        dedup: list[tuple[int, str]] = []
        for start, title in marks:
            if dedup and dedup[-1][0] == start:   # drop duplicate start times
                continue
            dedup.append((start, title))
        # Require at least two ascending marks to trust the parse.
        if len(dedup) >= 2:
            for i, (start, title) in enumerate(dedup):
                end = dedup[i + 1][0] if i + 1 < len(dedup) else duration
                spans.append([float(start), float(end), title])

    # Resolve any missing end times, and drop empty/zero-length spans.
    chapters = []
    for i, (start, end, title) in enumerate(spans):
        if end is None:
            end = spans[i + 1][0] if i + 1 < len(spans) else duration
        if not end or end <= start:
            continue
        chapters.append(Chapter(start, float(end), sanitize(title)))
    return chapters


def fetch_chapters(url: str) -> list[Chapter] | None:
    """One URL's chapters, or None when there are none to split on.

    Called before anything is downloaded, so a video with nothing to split
    fails fast and a batch can skip straight to the next URL.
    """
    log("Fetching video info to locate chapters...")
    proc = subprocess.run(["yt-dlp", "-J", "--no-playlist", url],
                          capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        log("Error: could not fetch or parse video info for chapters.")
        return None
    try:
        chapters = chapters_from_info(json.loads(proc.stdout))
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        log(f"Error: could not parse video info for chapters ({exc}).")
        return None
    if not chapters:
        log("Error: no chapters found — the video has no native chapters and no")
        log("       usable timestamps were parsed from its description. Nothing to")
        log("       split. Re-run without --chapters to download the whole video.")
        return None
    log(f"Found {len(chapters)} chapter(s):")
    for i, chapter in enumerate(chapters, start=1):
        log(f"{i:2d}. {chapter.title}")
    log("")
    return chapters


def split_into_chapters(full: Path, chapters: list[Chapter], precise: bool,
                        segments_only: bool, user_supplied: bool) -> int:
    """Split a file into one file per chapter. Returns an exit status."""
    if not full.is_file():
        log("Error: could not determine the file to split.")
        return 1
    outdir = full.parent / full.stem
    outdir.mkdir(parents=True, exist_ok=True)
    total = len(chapters)
    pad = max(2, len(str(total)))
    if precise:
        log(f"Splitting into {total} chapter file(s), frame-exact (re-encoding), in: {outdir}")
    else:
        log(f"Splitting into {total} chapter file(s) in: {outdir}")

    for idx, chapter in enumerate(chapters, start=1):
        num = f"{idx:0{pad}d}"
        outfile = outdir / f"{num} - {chapter.title}{full.suffix}"
        log(f"  [{num}/{total}] {chapter.title}")
        # -ss before -i seeks quickly; -t (duration) after -i is version-robust.
        cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y",
               "-ss", f"{chapter.start:.3f}", "-i", str(full),
               "-t", f"{chapter.end - chapter.start:.3f}"]
        if precise:
            # Frame-exact: re-encode. With transcoding, input -ss performs an
            # accurate seek (decodes to the exact time), so cuts land on the
            # right frame. -map 0:V:0 takes the real video (excluding any
            # attached cover image); -map 0:a? includes audio if present.
            cmd += ["-map", "0:V:0", "-map", "0:a?",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]
        else:
            # Fast path: keyframe-aligned stream copy (no re-encode, may drift).
            cmd += ["-map", "0", "-c", "copy"]
        cmd += ["-avoid_negative_ts", "make_zero", str(outfile)]
        result = subprocess.run(cmd, stdin=subprocess.DEVNULL)
        if result.returncode != 0:
            log(f"Error: ffmpeg failed on chapter {num} (exit {result.returncode}).")
            return 1

    if segments_only and user_supplied:
        # Never delete a file the user supplied, even with --segments-only.
        log(f"Done. Segments written; kept your source file (never deleted): {full}")
    elif segments_only:
        full.unlink(missing_ok=True)
        log("Done. Removed the full-length video (segments only).")
    else:
        log(f"Done. Kept the full-length video: {full}")
    return 0


# ---------------------------------------------------------------------------
# Disk space.
# ---------------------------------------------------------------------------

def check_disk_space(url: str, fmt: str, outdir: Path) -> int:
    """Refuse a doomed download when the destination disk lacks room.

    The estimate comes from yt-dlp (sum of the selected video+audio streams);
    if it can't be determined, the check is skipped rather than guessed. Room
    is needed for the file plus the transient second copy the merge/remux/split
    makes.
    """
    proc = subprocess.run(
        ["yt-dlp", "-f", fmt, "--no-playlist", "--simulate",
         "--print", "%(filesize,filesize_approx)s", url],
        capture_output=True, text=True)
    estimate = (proc.stdout.strip().splitlines() or [""])[0].strip()
    if not estimate.isdigit() or int(estimate) <= 0:
        log("Note: could not estimate download size; skipping disk-space check.")
        return 0
    est = int(estimate)
    need = est * SPACE_SAFETY
    try:
        avail = shutil.disk_usage(outdir).free
    except OSError:
        log("Note: could not read free space; skipping check.")
        return 0
    if avail < need:
        log("Error: not enough free disk space for this download.")
        log(f"       Estimated download: ~{human(est)}")
        log(f"       Needed (x{SPACE_SAFETY} for the merge/remux's temporary copy): ~{human(need)}")
        log(f"       Free on {outdir.resolve()}: ~{human(avail)}")
        log("       Free up space, or pass --skip-space-check to override.")
        return EXIT_NO_SPACE
    return 0


# ---------------------------------------------------------------------------
# Playlist expansion. A playlist link is replaced by the videos it contains, so
# every item in the batch is a single video and the per-video machinery
# (chapters, space check, output-path capture) keeps working untouched.
# ---------------------------------------------------------------------------

def looks_like_playlist(url: str) -> bool:
    """Links worth expanding: a playlist, or a channel tab that lists videos.

    A bare channel URL is not one — yt-dlp lists its *tabs*, not its videos —
    so point it at the /videos tab instead.
    """
    if "/playlist" in url or "list=" in url:
        return True
    # The tab name has to END the path: '/shorts/abc123' is one Short, not a
    # listing, and '/videos/anything' is not a tab either.
    for tab in ("videos", "shorts", "streams"):
        if re.search(rf"/{tab}/?(\?|$)", url):
            return True
    return False


def expand_urls(urls: list[str], yes_playlist: bool) -> list[str]:
    out: list[str] = []
    for url in urls:
        if not looks_like_playlist(url):
            out.append(url)
            continue
        # A '...watch?v=...&list=...' link names one video inside a playlist,
        # which is what you get when you copy the address while watching it.
        # Downloading just that video is almost always what was meant.
        if "v=" in url and not yes_playlist:
            log("Note: this link names one video inside a playlist, so only that")
            log("      video will be downloaded. Pass --yes-playlist (or use the")
            log("      playlist's own URL) to get every video in it.")
            out.append(url)
            continue
        log(f"Expanding playlist: {url}")
        # --flat-playlist lists entries without visiting each video page.
        # Printing the id alongside the URL covers entries with no URL of
        # their own.
        proc = subprocess.run(
            ["yt-dlp", "--flat-playlist", "--print", "%(url)s|%(id)s", url],
            capture_output=True, text=True)
        if proc.returncode != 0:
            log("Warning: could not read that playlist; keeping the URL as it is.")
            out.append(url)
            continue
        found = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            entry, _, vid = line.partition("|")
            if entry.startswith("http"):
                found.append(entry)
            elif vid and vid != "NA":
                found.append(f"https://www.youtube.com/watch?v={vid}")
        if not found:
            log("Warning: that playlist listed no videos; keeping the URL as it is.")
            out.append(url)
        else:
            log(f"  -> {len(found)} video(s).")
            out += found
    return out


# ---------------------------------------------------------------------------
# Download.
# ---------------------------------------------------------------------------

def run_download(url: str, ytdlp_args: list[str], strict: bool) -> int:
    """Run yt-dlp for one URL.

    Without --strict, yt-dlp's own exit status and error text pass straight
    through. With --strict, additionally detect a runtime "HTTP 403: Forbidden"
    (YouTube blocking a stale/blocked build) and turn it into a
    clearly-diagnosed failure pointing at the fix, rather than leaving the raw
    traceback as the last word.
    """
    cmd = ["yt-dlp", *ytdlp_args, url]
    if not strict:
        return subprocess.run(cmd).returncode

    # Capture the output while it stays live on screen, so we can scan it.
    # Chunks, not lines: yt-dlp draws its progress with '\r'.
    captured = bytearray()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert proc.stdout is not None
    while True:
        chunk = proc.stdout.read(4096)
        if not chunk:
            break
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
        captured += chunk
        del captured[:-65536]       # only the tail matters for the scan
    status = proc.wait()
    if status != 0 and FORBIDDEN_RE.search(captured.decode("utf-8", "replace")):
        log("")
        log("Error: YouTube returned HTTP 403 (Forbidden) during download.")
        log("       This almost always means yt-dlp is out of date or blocked;")
        log("       YouTube changes its defenses and stale builds get rejected.")
        log(f"       Update the copy on your PATH ({shutil.which('yt-dlp')}) and retry:")
        log(f"         {Path(sys.argv[0]).name} --update")
        log('       (or: python3 -m pip install -U --pre "yt-dlp[default]")')
        return EXIT_STRICT_403
    return status


def process_one(url: str, args: argparse.Namespace, base_args: list[str], fmt: str) -> int:
    """Everything one video needs, from chapter discovery through splitting.

    Returns the status of whichever step failed, so the caller can record it
    and move on.
    """
    ytdlp_args = list(base_args)
    chapters: list[Chapter] | None = None
    pathfile: Path | None = None

    if args.chapters:
        chapters = fetch_chapters(url)
        if chapters is None:
            return 1
        # Capture the final output path so we know which file to split.
        handle, name = tempfile.mkstemp(prefix="yt-download-path.")
        os.close(handle)
        pathfile = Path(name)
        ytdlp_args += ["--print-to-file", "after_move:filepath", str(pathfile)]

    try:
        if not args.skip_space_check:
            status = check_disk_space(url, fmt, Path(args.outdir))
            if status != 0:
                return status

        status = run_download(url, ytdlp_args, args.strict)
        if status != 0:
            return status

        if chapters is not None and pathfile is not None:
            printed = pathfile.read_text(encoding="utf-8", errors="replace").strip()
            if not printed:
                # With --skip-existing, a video already on disk is never moved
                # into place, so no path is printed. There is nothing new to
                # split; say so and move on rather than calling it a failure.
                log("Already downloaded; leaving its existing chapter files alone.")
                return 0
            full = Path(printed.splitlines()[-1])
            return split_into_chapters(full, chapters, args.precise,
                                       args.segments_only, user_supplied=False)
        return 0
    finally:
        if pathfile is not None:
            pathfile.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Command line.
# ---------------------------------------------------------------------------

def read_batch_file(path: Path) -> list[str]:
    """URLs one per line, ignoring blank lines and '#' comments."""
    if not path.is_file():
        sys.exit(f"Error: --batch-file: no such file: {path}")
    urls = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()                     # also drops CR from CRLF files
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download a YouTube video in its highest available resolution.",
        epilog="MP4 (H.264/AAC, +faststart) is the default and plays virtually "
               "everywhere, but H.264 tops out at 1080p on YouTube — use --mkv "
               "for 1440p/4K. In a batch, one failure does not stop the rest: "
               "the run ends with a summary and exits non-zero if anything failed.")
    parser.add_argument("urls", nargs="*", metavar="URL",
                        help="One or more YouTube URLs (a playlist URL expands "
                             "into its videos)")
    parser.add_argument("-o", "--outdir", default=".",
                        help="Output directory, created if missing (default: "
                             "the current directory)")
    parser.add_argument("-m", "--max", dest="max_height", metavar="HEIGHT",
                        help="Cap resolution to HEIGHT pixels (e.g. 1080, 720)")

    container = parser.add_mutually_exclusive_group()
    container.add_argument("-p", "--mp4", action="store_true",
                           help="Produce a QuickTime/iOS-friendly MP4 (the "
                                "default): prefer H.264 + AAC, merge to MKV "
                                "first (reliable), then losslessly remux to MP4 "
                                "with +faststart")
    container.add_argument("--mkv", action="store_true",
                           help="Output an MKV with the best available codec "
                                "(AV1/VP9/H.264) instead of the default MP4. "
                                "Best for max quality / 4K")
    container.add_argument("-c", "--container", metavar="EXT",
                           help="Advanced: merge straight into EXT (mkv or mp4) "
                                "with the best codec, skipping the "
                                "H.264-preferred remux")

    parser.add_argument("-k", "--keep", action="store_true",
                        help="Keep intermediate files (separate video/audio "
                             "streams and the pre-remux merge)")
    parser.add_argument("-C", "--chapters", action="store_true",
                        help="Split the video into one file per chapter, using "
                             "its native chapters or, failing that, timestamps "
                             "parsed from the description. Segments land in a "
                             "per-video subfolder; the full-length video is kept "
                             "alongside them")
    parser.add_argument("-P", "--precise", action="store_true",
                        help="With --chapters, cut on exact frame boundaries by "
                             "re-encoding each segment instead of the default "
                             "fast keyframe-aligned stream copy")
    parser.add_argument("--segments-only", action="store_true",
                        help="With --chapters, delete the full-length video "
                             "after splitting (default is to keep it)")
    parser.add_argument("--split-file", metavar="PATH",
                        help="Split an EXISTING local file PATH into chapters "
                             "using the chapters from the given URL, without "
                             "downloading anything. PATH itself is always kept. "
                             "Implies --chapters")
    parser.add_argument("--skip-space-check", action="store_true",
                        help="Skip the pre-download free-space check")
    parser.add_argument("-b", "--batch-file", metavar="PATH",
                        help="Read URLs from PATH, one per line; blank lines and "
                             "lines starting with '#' are ignored")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Leave a video alone when its output file is "
                             "already there, instead of downloading it again")
    parser.add_argument("--stop-on-error", action="store_true",
                        help="Abort the batch as soon as one video fails (the "
                             "default is to carry on and list the failures)")
    parser.add_argument("--no-playlist-expand", action="store_true",
                        help="Never expand a playlist URL; download only the "
                             "single video the link points at")
    parser.add_argument("--yes-playlist", action="store_true",
                        help="Expand the playlist even when the URL also names "
                             "one specific video (a '...watch?v=...&list=...' "
                             "link, which by default downloads just that video)")
    parser.add_argument("-F", "--list-formats", action="store_true",
                        help="List all available formats and exit (no download)")
    parser.add_argument("-u", "--update", action="store_true",
                        help="Update yt-dlp (the copy on your PATH) and exit")
    parser.add_argument("-s", "--strict", action="store_true",
                        help="Fail hard on yt-dlp problems instead of muddling "
                             "on: refuse to start if yt-dlp is stale, and "
                             "diagnose a runtime HTTP 403 clearly")
    parser.add_argument("--no-install", action="store_true",
                        help="Do not auto-install missing dependencies; just "
                             "error with install hints")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # Resolve the container choice: MP4-by-remux is the default.
    args.remux_mp4 = not (args.mkv or args.container)
    if not args.container:
        args.container = "mkv"

    urls = list(args.urls)
    if args.batch_file:
        urls += read_batch_file(Path(args.batch_file))

    if not urls and not args.update:
        parser.error("no YouTube URL given")

    # --split-file splits an existing local file instead of downloading. It
    # still needs the URL as the chapter source, and implies chapters mode. One
    # file means one chapter source, so exactly one URL.
    if args.split_file:
        args.chapters = True
        if len(urls) != 1:
            sys.exit("Error: --split-file splits one file, so it takes exactly one URL\n"
                     "       (the video whose chapters mark the split points).")
        if not Path(args.split_file).is_file():
            sys.exit(f"Error: --split-file: no such file: {args.split_file}")

    if args.precise and not args.chapters:
        sys.exit("Error: --precise has no effect without --chapters.")
    if args.segments_only and not args.chapters:
        sys.exit("Error: --segments-only has no effect without --chapters.")

    required = ["yt-dlp"] if args.update or args.list_formats else ["yt-dlp", "ffmpeg"]
    require(*required, auto_install=not args.no_install)

    if args.update:
        return update_ytdlp()

    check_ytdlp_freshness(args.strict)

    if args.list_formats:
        for url in urls:
            if len(urls) > 1:
                log("")
                log(f"=== {url}")
            subprocess.run(["yt-dlp", "-F", "--no-playlist", url])
        return 0

    Path(args.outdir).mkdir(parents=True, exist_ok=True)

    # --split-file: no download; split the existing file using the URL's chapters.
    if args.split_file:
        chapters = fetch_chapters(urls[0])
        if chapters is None:
            return 1
        return split_into_chapters(Path(args.split_file), chapters, args.precise,
                                   args.segments_only, user_supplied=True)

    if not args.no_playlist_expand:
        urls = expand_urls(urls, args.yes_playlist)

    fmt = build_format(args.max_height, args.remux_mp4)
    base_args = build_base_args(args, fmt)

    total = len(urls)
    failures: list[tuple[str, int]] = []
    done = 0
    last_status = 0

    for idx, url in enumerate(urls, start=1):
        # This marker is what the GUI reads to show overall batch progress;
        # keep the "[n/m]" shape if you change it.
        if total > 1:
            log("")
            log(f"=== [{idx}/{total}] {url}")
        status = process_one(url, args, base_args, fmt)
        if status == 0:
            done += 1
            continue
        last_status = status
        failures.append((url, status))
        if args.stop_on_error:
            if total > 1:
                log(f"Stopping here: [{idx}/{total}] failed (exit {status}) "
                    "and --stop-on-error is set.")
            break
        if total > 1:
            log(f"That one failed (exit {status}); carrying on with the next URL.")

    # A single URL keeps its exact exit status, as it did before batch mode.
    if total == 1:
        return last_status

    log("")
    log(f"Batch summary: {done} of {total} succeeded, {len(failures)} failed.")
    for url, status in failures:
        log(f"  failed (exit {status}): {url}")
    return 1 if failures else 0


def _exit_on_signal(signum, _frame):
    """Leave by the normal route, so the finally blocks still tidy up.

    The GUI's Stop button SIGTERMs our process group; Python's default handler
    would kill us outright and leave the scratch files behind, where the shell
    version's EXIT trap used to clean up.
    """
    raise SystemExit(128 + signum)


if __name__ == "__main__":
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _exit_on_signal)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
