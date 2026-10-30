#!/usr/bin/env python3
"""Mix multiple video files into one using ffmpeg.

Modes:
  concat  Play the videos one after another, optionally with crossfades.
  grid    Show the videos side by side in a grid, playing at the same time.
  blend   Lay the videos on top of each other and average the frames.

Inputs may have different resolutions, frame rates and audio layouts. Each one
is scaled and padded to the output size so they fit together. Inputs with no
audio track get silence in concat mode and are left out of the audio mix in
grid and blend modes.

Requires ffmpeg and ffprobe on PATH (macOS: brew install ffmpeg).

Examples:
  python mix_videos.py concat a.mp4 b.mov c.mkv -o joined.mp4
  python mix_videos.py concat a.mp4 b.mp4 -o faded.mp4 --crossfade 1 --transition dissolve
  python mix_videos.py grid a.mp4 b.mp4 c.mp4 d.mp4 -o grid.mp4 --size 1920x1080
  python mix_videos.py blend a.mp4 b.mp4 -o blended.mp4 --weights 3 1
"""

import argparse
import json
import math
import subprocess
import sys

import deps

SAMPLE_RATE = 48000


def probe(path):
    """Return (duration_seconds, has_audio) for a media file."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
         "-of", "json", path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.exit(f"ffprobe failed on {path}:\n{result.stderr.strip()}")
    info = json.loads(result.stdout)
    duration = float(info.get("format", {}).get("duration", 0) or 0)
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    return duration, has_audio


def fit(label, out, width, height, fps):
    """Filter that scales a video stream to fit width x height, letterboxing as needed."""
    return (
        f"[{label}]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,fps={fps},format=yuv420p[{out}]"
    )


def normalize_audio(label, out):
    return f"[{label}]aresample={SAMPLE_RATE},aformat=channel_layouts=stereo[{out}]"


def build_concat(inputs, width, height, fps, crossfade=0, transition="fade"):
    n = len(inputs)
    if n == 1:
        crossfade = 0
    filters, segments = [], []
    for i, (_, duration, has_audio) in enumerate(inputs):
        filters.append(fit(f"{i}:v", f"v{i}", width, height, fps))
        if has_audio:
            filters.append(normalize_audio(f"{i}:a", f"a{i}"))
        else:
            filters.append(
                f"anullsrc=r={SAMPLE_RATE}:cl=stereo,atrim=duration={duration:.3f}[a{i}]"
            )
        if crossfade:
            # Crossfade offsets are computed from the probed durations, so pin every
            # segment's video and audio to exactly that length (holding the last frame
            # or padding with silence if a stream runs short).
            filters.append(
                f"[v{i}]tpad=stop_mode=clone:stop_duration={duration:.3f},"
                f"trim=duration={duration:.3f},setpts=PTS-STARTPTS[vt{i}]"
            )
            filters.append(
                f"[a{i}]apad,atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[at{i}]"
            )
        segments.append(f"[v{i}][a{i}]")

    if not crossfade:
        filters.append(f"{''.join(segments)}concat=n={n}:v=1:a=1[vout][aout]")
        return filters, True

    # Chain xfade/acrossfade: each transition starts `crossfade` seconds before the
    # end of everything joined so far.
    v_prev, a_prev, offset = "vt0", "at0", 0.0
    for i in range(1, n):
        offset += inputs[i - 1][1] - crossfade
        v_out = "vout" if i == n - 1 else f"vx{i}"
        a_out = "aout" if i == n - 1 else f"ax{i}"
        filters.append(
            f"[{v_prev}][vt{i}]xfade=transition={transition}:duration={crossfade}"
            f":offset={offset:.3f}[{v_out}]"
        )
        filters.append(f"[{a_prev}][at{i}]acrossfade=d={crossfade}[{a_out}]")
        v_prev, a_prev = v_out, a_out
    return filters, True


def audio_mix(inputs, duration_mode, weights=None):
    """Filters that mix the audio of every input that has some. Returns (filters, has_audio)."""
    indices = [i for i, (_, _, has_audio) in enumerate(inputs) if has_audio]
    if not indices:
        return [], False
    filters = [normalize_audio(f"{i}:a", f"a{i}") for i in indices]
    labels = "".join(f"[a{i}]" for i in indices)
    if len(indices) == 1:
        filters.append(f"{labels}anull[aout]")
    else:
        weight_opt = ""
        if weights:
            weight_opt = ":weights=" + " ".join(str(weights[i]) for i in indices)
        filters.append(
            f"{labels}amix=inputs={len(indices)}:duration={duration_mode}"
            f":normalize=1{weight_opt}[aout]"
        )
    return filters, True


def build_grid(inputs, width, height, fps, shortest, cols=None):
    n = len(inputs)
    cols = cols or math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    # Even cell sizes keep yuv420p happy.
    cell_w = (width // cols) // 2 * 2
    cell_h = (height // rows) // 2 * 2

    filters, layout = [], []
    for i in range(n):
        filters.append(fit(f"{i}:v", f"v{i}", cell_w, cell_h, fps))
        layout.append(f"{(i % cols) * cell_w}_{(i // cols) * cell_h}")
    stacked = "".join(f"[v{i}]" for i in range(n))
    filters.append(
        f"{stacked}xstack=inputs={n}:layout={'|'.join(layout)}:fill=black"
        f":shortest={1 if shortest else 0}[grid]"
    )
    # Pad out to the exact requested size when it doesn't divide evenly.
    filters.append(f"[grid]pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black[vout]")

    audio_filters, has_audio = audio_mix(inputs, "shortest" if shortest else "longest")
    return filters + audio_filters, has_audio


def build_blend(inputs, width, height, fps, shortest, weights):
    n = len(inputs)
    filters = [fit(f"{i}:v", f"v{i}", width, height, fps) for i in range(n)]
    labels = "".join(f"[v{i}]" for i in range(n))
    weight_opt = f":weights={' '.join(map(str, weights))}" if weights else ""
    filters.append(
        f"{labels}mix=inputs={n}{weight_opt}:duration={'shortest' if shortest else 'longest'}[vout]"
    )
    audio_filters, has_audio = audio_mix(inputs, "shortest" if shortest else "longest", weights)
    return filters + audio_filters, has_audio


def parse_size(value):
    try:
        w, h = (int(x) for x in value.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError("size must look like 1280x720")
    if w <= 0 or h <= 0:
        raise argparse.ArgumentTypeError("size must be positive")
    return w // 2 * 2, h // 2 * 2


def main():
    parser = argparse.ArgumentParser(
        description="Mix multiple video files into one (concat, grid, or blend).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:" + __doc__.split("Examples:", 1)[1],
    )
    parser.add_argument("mode", choices=["concat", "grid", "blend"])
    parser.add_argument("inputs", nargs="+", help="input video files")
    parser.add_argument("-o", "--output", required=True, help="output file, e.g. out.mp4")
    parser.add_argument("--size", type=parse_size, default=(1280, 720),
                        help="output resolution WxH (default 1280x720)")
    parser.add_argument("--fps", type=float, default=30, help="output frame rate (default 30)")
    parser.add_argument("--crossfade", type=float, default=0, metavar="SECONDS",
                        help="concat mode: crossfade length between clips (default 0, a hard cut)")
    parser.add_argument("--transition", default="fade",
                        help="concat mode: xfade transition used with --crossfade, e.g. fade, "
                             "dissolve, wipeleft, slideright, circleopen, fadeblack (default fade)")
    parser.add_argument("--cols", type=int, help="grid mode: number of columns (default: auto)")
    parser.add_argument("--shortest", action="store_true",
                        help="grid/blend: stop when the shortest input ends (default: longest)")
    parser.add_argument("--weights", type=float, nargs="+",
                        help="blend mode: per-input weights, e.g. --weights 2 1")
    parser.add_argument("--crf", type=int, default=20,
                        help="x264 quality, lower is better (default 20)")
    parser.add_argument("--preset", default="medium", help="x264 preset (default medium)")
    parser.add_argument("-y", "--overwrite", action="store_true", help="overwrite the output file")
    parser.add_argument("--dry-run", action="store_true", help="print the ffmpeg command and exit")
    args = parser.parse_args()

    missing = deps.ensure(["ffmpeg", "ffprobe"])
    if missing:
        lines = "\n".join(f"  - {t}: {deps.hint(t)}" for t in missing)
        sys.exit(f"Error: required tool(s) could not be installed:\n{lines}")
    if args.mode != "concat" and len(args.inputs) < 2:
        parser.error(f"{args.mode} mode needs at least two inputs")
    if args.weights and args.mode != "blend":
        parser.error("--weights only applies to blend mode")
    if args.weights and len(args.weights) != len(args.inputs):
        parser.error("--weights needs one value per input")
    if args.cols is not None and args.cols < 1:
        parser.error("--cols must be at least 1")

    if args.crossfade < 0:
        parser.error("--crossfade can't be negative")
    if args.crossfade and args.mode != "concat":
        parser.error("--crossfade only applies to concat mode")

    inputs = [(path, *probe(path)) for path in args.inputs]
    if args.crossfade and len(inputs) > 1:
        shortest_path, shortest_duration, _ = min(inputs, key=lambda x: x[1])
        if args.crossfade >= shortest_duration:
            parser.error(f"--crossfade {args.crossfade}s must be shorter than the shortest clip "
                         f"({shortest_path}, {shortest_duration:.2f}s)")
    width, height = args.size

    if args.mode == "concat":
        filters, has_audio = build_concat(inputs, width, height, args.fps, args.crossfade,
                                         args.transition)
    elif args.mode == "grid":
        filters, has_audio = build_grid(inputs, width, height, args.fps, args.shortest, args.cols)
    else:
        filters, has_audio = build_blend(inputs, width, height, args.fps, args.shortest,
                                         args.weights)

    # Only show real errors plus the progress line, not the full encoder report.
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-stats",
           "-y" if args.overwrite else "-n"]
    for path, _, _ in inputs:
        cmd += ["-i", path]
    cmd += ["-filter_complex", ";".join(filters), "-map", "[vout]"]
    if has_audio:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "libx264", "-crf", str(args.crf), "-preset", args.preset,
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", args.output]

    if args.dry_run:
        print(" ".join(f"'{c}'" if " " in c or ";" in c or "[" in c else c for c in cmd))
        return
    sys.exit(subprocess.run(cmd).returncode)


if __name__ == "__main__":
    main()
