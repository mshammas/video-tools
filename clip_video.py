#!/usr/bin/env python3
"""Clip a segment from a local video file or a YouTube video.

Examples:
    ./clip_video.py input.mp4 00:01:30 00:02:45
    ./clip_video.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ" 0:30 1:15 -n chorus.mp4
    ./clip_video.py input.mkv 1:02:03.5 1:03:00 --fast -o ~/Clips

Time format: HH:MM:SS[.ms], MM:SS[.ms] or SS[.ms]

Requires: ffmpeg on PATH; yt-dlp (pip install yt-dlp) for YouTube links.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import deps

TIME_RE = re.compile(r"^(?:(\d+):)?(?:(\d+):)?(\d+(?:\.\d+)?)$")


def parse_time(value: str) -> float:
    """Convert HH:MM:SS[.ms] / MM:SS[.ms] / SS[.ms] to seconds."""
    match = TIME_RE.match(value.strip())
    if not match:
        raise argparse.ArgumentTypeError(
            f"invalid time '{value}' (expected HH:MM:SS, MM:SS or SS, optionally with .ms)"
        )
    parts = [p for p in match.groups() if p is not None]
    *whole, seconds = parts
    if any(int(p) >= 60 for p in whole[1:]) or (whole and float(seconds) >= 60):
        raise argparse.ArgumentTypeError(f"invalid time '{value}': minutes/seconds must be < 60")
    total = float(seconds)
    for i, part in enumerate(reversed(whole), start=1):
        total += int(part) * 60**i
    return total


def format_time(seconds: float) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def is_url(source: str) -> bool:
    return urlparse(source).scheme in ("http", "https")


def require(*tools: str) -> None:
    """Ensure each tool is available, auto-installing any that are missing."""
    missing = deps.ensure(list(tools))
    if missing:
        lines = "\n".join(f"  - {t}: {deps.hint(t)}" for t in missing)
        sys.exit(f"Error: required tool(s) could not be installed:\n{lines}")


def clip_local(src: Path, start: float, end: float, output: Path, fast: bool) -> None:
    cmd = [
        # Only show real errors plus the progress line; decoder warnings such as
        # "Late SEI is not implemented" are harmless noise.
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", "-y",
        "-ss", format_time(start),
        "-i", str(src),
        "-t", format_time(end - start),
    ]
    if fast:
        # Stream copy: instant, but cuts snap to the nearest keyframe.
        cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    else:
        # Re-encode: frame-accurate cuts.
        cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-c:a", "aac", "-b:a", "192k"]
    cmd.append(str(output))
    subprocess.run(cmd, check=True)


def clip_youtube(url: str, start: float, end: float, output: Path, fast: bool) -> None:
    cmd = [
        "yt-dlp",
        "--download-sections", f"*{format_time(start)}-{format_time(end)}",
        "-f", "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b",
        "--merge-output-format", output.suffix.lstrip(".") or "mp4",
        "--no-playlist",
        "-o", str(output),
        "--force-overwrites",
        # Hide harmless ffmpeg decoder warnings (e.g. "Late SEI is not implemented").
        "--downloader-args", "ffmpeg_i:-loglevel error -stats",
    ]
    if not fast:
        cmd.append("--force-keyframes-at-cuts")
    cmd.append(url)
    subprocess.run(cmd, check=True)


def default_name(source: str, start: float, end: float) -> str:
    tag = f"{format_time(start)}-{format_time(end)}".replace(":", "").replace(".", "")
    if is_url(source):
        return f"youtube_clip_{tag}.mp4"
    src = Path(source)
    return f"{src.stem}_clip_{tag}{src.suffix or '.mp4'}"


def resolve_output(source: str, start: float, end: float, outdir, name) -> Path:
    """Work out the output path from the folder (-o) and file name (-n)."""
    if outdir is None:
        outdir = Path(".")
    if name is None:
        name = default_name(source, start, end)
    output = outdir.expanduser() / name
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Clip a video (local file or YouTube URL).")
    parser.add_argument("source", help="Path to a local video file or a YouTube URL")
    parser.add_argument("start", type=parse_time, help="Start time (HH:MM:SS[.ms], MM:SS or SS)")
    parser.add_argument("end", type=parse_time, help="End time (HH:MM:SS[.ms], MM:SS or SS)")
    parser.add_argument(
        "-o", "--outdir", type=Path,
        help="Output folder, created if missing (default: the current folder)",
    )
    parser.add_argument(
        "-n", "--name",
        help="Output file name (default: auto-named from the source and times)",
    )
    parser.add_argument(
        "--fast", action="store_true",
        help="Skip re-encoding (much faster, but cuts snap to keyframes)",
    )
    args = parser.parse_args()

    if args.end <= args.start:
        parser.error("end time must be after start time")

    output = resolve_output(args.source, args.start, args.end, args.outdir, args.name)
    require("ffmpeg")

    try:
        if is_url(args.source):
            require("yt-dlp")
            clip_youtube(args.source, args.start, args.end, output, args.fast)
        else:
            src = Path(args.source)
            if not src.is_file():
                sys.exit(f"Error: file not found: {src}")
            clip_local(src, args.start, args.end, output, args.fast)
    except subprocess.CalledProcessError as exc:
        sys.exit(f"Error: {exc.cmd[0]} failed with exit code {exc.returncode}")

    print(f"Saved clip: {output}")


if __name__ == "__main__":
    main()
