#!/usr/bin/env bash
#
# yt-download.sh — download a YouTube video in its highest available resolution.
#
# Usage:
#   ./yt-download.sh [options] <youtube-url> [<youtube-url> ...]
#
# Several URLs (or --batch-file) download one after another, and a playlist URL
# expands into the videos it contains. See the batch options below.
#
# Options:
#   -o, --outdir DIR      Output directory (default: current directory)
#   -m, --max HEIGHT      Cap resolution to HEIGHT pixels (e.g. 1080, 720)
#   -p, --mp4             Produce a QuickTime/iOS-friendly MP4 (THIS IS THE
#                         DEFAULT): prefer H.264 video + AAC audio, merge to MKV
#                         first (reliable), then losslessly remux to MP4 with
#                         +faststart. H.264 tops out at 1080p on YouTube, so use
#                         --mkv for higher resolutions (4K is VP9/AV1 only).
#       --mkv             Output an MKV with the best available codec (AV1/VP9/
#                         H.264) instead of the default MP4. Best for max quality
#                         / 4K; plays in VLC/IINA and modern players.
#   -c, --container EXT   Advanced: merge straight into EXT (mkv or mp4) with the
#                         best codec, skipping the H.264-preferred remux.
#   -k, --keep            Keep intermediate files (separate video/audio streams
#                         and the pre-remux merge) instead of deleting them
#   -C, --chapters        Split the video into one file per chapter. Uses the
#                         video's native chapters (from description timestamps
#                         that follow YouTube's rules); if there are none, falls
#                         back to parsing timestamps out of the description text.
#                         Segments land in a per-video subfolder; the full-length
#                         video is kept alongside them (use --segments-only to
#                         delete it). Requires python3.
#   -P, --precise         With --chapters, cut on exact frame boundaries by
#                         re-encoding each segment (H.264/AAC) instead of the
#                         default fast keyframe-aligned stream copy. Slower and
#                         slightly lossy, but segments start/end exactly on time.
#       --segments-only   With --chapters, delete the full-length video after
#                         splitting (default is to keep it).
#       --split-file PATH Split an EXISTING local file PATH into chapters using
#                         the chapters/timestamps from the given URL, without
#                         downloading anything. Segments are written next to
#                         PATH; PATH itself is always kept. Implies --chapters.
#                         (Download options like -m/-p/-c are ignored; -P works.)
#       --skip-space-check Skip the pre-download free-space check (which, by
#                         default, refuses to start if the destination disk
#                         likely lacks room for the download plus its merge).
#   -b, --batch-file PATH Read URLs from PATH, one per line; blank lines and
#                         lines starting with '#' are ignored. Combines with any
#                         URLs given on the command line.
#       --skip-existing   Leave a video alone when its output file is already
#                         there, instead of downloading it again. Makes
#                         re-running a batch after a failure cheap.
#       --stop-on-error   Abort the batch as soon as one video fails. The
#                         default is to carry on and list the failures at the
#                         end.
#       --no-playlist-expand
#                         Never expand a playlist URL; download only the single
#                         video the link points at.
#       --yes-playlist    Expand the playlist even when the URL also names one
#                         specific video (a '...watch?v=...&list=...' link,
#                         which by default downloads just that one video).
#   -F, --list-formats    List all available formats and exit (no download)
#   -u, --update          Update yt-dlp (the copy on your PATH) and exit
#   -s, --strict          Fail hard on yt-dlp problems instead of muddling on:
#                         refuse to start if yt-dlp is stale, and turn a runtime
#                         "HTTP 403: Forbidden" into a clearly-diagnosed error
#                         (these usually mean yt-dlp needs updating; see -u)
#       --no-install      Do not auto-install missing dependencies (yt-dlp /
#                         ffmpeg / python3); just error with install hints.
#   -h, --help            Show this help and exit
#
# Requires: yt-dlp and ffmpeg (plus python3 for --chapters). If any are missing
# they are auto-installed when a package manager is available — Homebrew on
# macOS; apt/dnf/yum/pacman/zypper on Linux; winget/choco/scoop on Windows —
# unless --no-install is given, in which case it errors with instructions.
#
# Platforms: runs on macOS and Linux directly. On Windows, run it under Git Bash
# (from Git for Windows), or inside WSL with Linux-side yt-dlp/ffmpeg and
# Linux-side paths; native cmd/PowerShell is not supported.
#
# Notes:
#   * MP4 is the default output (H.264 + AAC, remuxed with +faststart): the most
#     compatible result, playing in QuickTime, iOS and virtually everywhere.
#     Because H.264 tops out at 1080p on YouTube, use --mkv when you want the
#     highest resolution (4K/1440p are served only as VP9/AV1, which live in MKV).
#   * The merge/remux step temporarily needs disk space for a second full copy
#     of the file, but the pre-download space check now guards against this.
#   * Batch mode: give it several URLs, a playlist, or --batch-file, and the
#     videos download one after another. Playlists are expanded up front, so
#     every item is a single video and all the options above still apply to it.
#     One failure doesn't stop the rest; the run ends with a summary and exits
#     non-zero if anything failed. Use --stop-on-error to bail out on the first.

set -euo pipefail

OUTDIR="."
MAXHEIGHT=""
CONTAINER="mkv"
REMUX_MP4=1          # default: produce a compatible H.264/AAC MP4
LIST_ONLY=0
DO_UPDATE=0
STRICT=0
KEEP=0
CHAPTERS=0
PRECISE=0
SEGMENTS_ONLY=0
SPLIT_FILE=""
SKIP_SPACE_CHECK=0
NO_INSTALL=0
URLS=()
BATCH_FILE=""
SKIP_EXISTING=0
STOP_ON_ERROR=0
EXPAND_PLAYLIST=1
YES_PLAYLIST=0

# Multiplier over the estimated download size that must be free on the
# destination disk: the merge (and, for --mp4, the remux; for --chapters, the
# split) briefly holds a second full copy alongside the original.
SPACE_SAFETY=2

usage() {
  # Print the contiguous leading comment block (everything after the shebang up
  # to the first non-comment line), stripping the leading "# ".
  awk 'NR==1 {next} /^#/ {sub(/^# ?/, ""); print; next} {exit}' "$0"
  exit "${1:-0}"
}

# Parse arguments.
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o|--outdir)       OUTDIR="$2"; shift 2 ;;
    -m|--max)          MAXHEIGHT="$2"; shift 2 ;;
    -c|--container)    CONTAINER="$2"; REMUX_MP4=0; shift 2 ;;
    -p|--mp4)          REMUX_MP4=1; shift ;;
    --mkv)             REMUX_MP4=0; CONTAINER="mkv"; shift ;;
    -k|--keep)         KEEP=1; shift ;;
    -C|--chapters)     CHAPTERS=1; shift ;;
    -P|--precise)      PRECISE=1; shift ;;
    --segments-only)   SEGMENTS_ONLY=1; shift ;;
    --split-file)      SPLIT_FILE="$2"; shift 2 ;;
    --skip-space-check) SKIP_SPACE_CHECK=1; shift ;;
    -b|--batch-file)   BATCH_FILE="$2"; shift 2 ;;
    --skip-existing)   SKIP_EXISTING=1; shift ;;
    --stop-on-error)   STOP_ON_ERROR=1; shift ;;
    --no-playlist-expand) EXPAND_PLAYLIST=0; shift ;;
    --yes-playlist)    YES_PLAYLIST=1; shift ;;
    --no-install)      NO_INSTALL=1; shift ;;
    -F|--list-formats) LIST_ONLY=1; shift ;;
    -u|--update)       DO_UPDATE=1; shift ;;
    -s|--strict)       STRICT=1; shift ;;
    -h|--help)         usage 0 ;;
    -*)                echo "Unknown option: $1" >&2; usage 1 ;;
    *)                 URLS+=("$1"); shift ;;
  esac
done

# --batch-file: read URLs one per line, ignoring blank lines and '#' comments.
# They join any URLs given on the command line, in file order.
if [[ -n "$BATCH_FILE" ]]; then
  if [[ ! -f "$BATCH_FILE" ]]; then
    echo "Error: --batch-file: no such file: $BATCH_FILE" >&2
    exit 1
  fi
  # The `|| [[ -n "$line" ]]` keeps a last line that has no trailing newline.
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"                        # tolerate CRLF files
    line="${line#"${line%%[![:space:]]*}"}"     # trim leading whitespace
    line="${line%"${line##*[![:space:]]}"}"     # trim trailing whitespace
    [[ -z "$line" || "$line" == \#* ]] && continue
    URLS+=("$line")
  done < "$BATCH_FILE"
fi

# A URL is required for everything except --update.
if [[ ${#URLS[@]} -eq 0 && "$DO_UPDATE" -eq 0 ]]; then
  echo "Error: no YouTube URL given." >&2
  usage 1
fi

# --split-file splits an existing local file instead of downloading. It still
# needs the URL as the chapter source, and implies chapters mode. One file means
# one chapter source, so exactly one URL.
if [[ -n "$SPLIT_FILE" ]]; then
  CHAPTERS=1
  if [[ ${#URLS[@]} -ne 1 ]]; then
    echo "Error: --split-file splits one file, so it takes exactly one URL" >&2
    echo "       (the video whose chapters mark the split points)." >&2
    exit 1
  fi
  if [[ ! -f "$SPLIT_FILE" ]]; then
    echo "Error: --split-file: no such file: $SPLIT_FILE" >&2
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# Dependency resolution. Required: yt-dlp, ffmpeg (+ python3 for --chapters).
# Missing tools are auto-installed via the platform's package manager, unless
# --no-install is given.
# ---------------------------------------------------------------------------
have() { command -v "$1" >/dev/null 2>&1; }
OS_NAME="$(uname -s 2>/dev/null || echo unknown)"

pkg_manager() {
  case "$OS_NAME" in
    Darwin) have brew && { echo brew; return; } ;;
    Linux)  for m in apt-get dnf yum pacman zypper; do
              have "$m" && { echo "$m"; return; }; done ;;
    MINGW*|MSYS*|CYGWIN*) for m in winget choco scoop; do
              have "$m" && { echo "$m"; return; }; done ;;
  esac
  echo ""
}

# Prefix privileged installs with sudo on Linux when not already root.
maybe_sudo() { if [[ "${EUID:-$(id -u)}" -ne 0 ]] && have sudo; then echo sudo; fi; }

manual_hint() {
  case "$1" in
    yt-dlp)  echo "python3 -m pip install -U --pre \"yt-dlp[default]\"  (or see https://github.com/yt-dlp/yt-dlp#installation)" ;;
    ffmpeg)  echo "install ffmpeg from your package manager, or https://ffmpeg.org/download.html" ;;
    python3) echo "install python3 from your package manager, or https://www.python.org/downloads/" ;;
  esac
}

install_dep() {
  local dep="$1" mgr pkg SUDO
  mgr="$(pkg_manager)"; SUDO="$(maybe_sudo)"
  # yt-dlp installs most reliably — and newest — via pip when Python is present.
  if [[ "$dep" == "yt-dlp" ]] && have python3; then
    echo "  -> python3 -m pip install -U --pre \"yt-dlp[default]\"" >&2
    python3 -m pip install -U --pre "yt-dlp[default]" && return 0
    echo "  (system pip refused; retrying with --user)" >&2
    python3 -m pip install --user -U --pre "yt-dlp[default]" && return 0
  fi
  [[ -z "$mgr" ]] && return 1
  # Map the generic name to this manager's package id.
  case "$dep" in
    python3) case "$mgr" in
               brew) pkg=python ;; winget) pkg="Python.Python.3.12" ;;
               choco|scoop) pkg=python ;; *) pkg=python3 ;; esac ;;
    ffmpeg)  case "$mgr" in winget) pkg="Gyan.FFmpeg" ;; *) pkg=ffmpeg ;; esac ;;
    yt-dlp)  case "$mgr" in winget) pkg="yt-dlp.yt-dlp" ;; *) pkg=yt-dlp ;; esac ;;
  esac
  echo "  -> installing '$dep' via $mgr" >&2
  case "$mgr" in
    brew)    brew install "$pkg" ;;
    apt-get) $SUDO apt-get update && $SUDO apt-get install -y "$pkg" ;;
    dnf)     $SUDO dnf install -y "$pkg" ;;
    yum)     $SUDO yum install -y "$pkg" ;;
    pacman)  $SUDO pacman -S --noconfirm "$pkg" ;;
    zypper)  $SUDO zypper install -y "$pkg" ;;
    winget)  winget install -e --id "$pkg" ;;
    choco)   choco install -y "$pkg" ;;
    scoop)   scoop install "$pkg" ;;
    *)       return 1 ;;
  esac
}

REQUIRED=(yt-dlp ffmpeg)
[[ "$CHAPTERS" -eq 1 ]] && REQUIRED+=(python3)
for cmd in "${REQUIRED[@]}"; do
  have "$cmd" && continue
  if (( NO_INSTALL )); then
    echo "Error: '$cmd' is not installed. Install it with:" >&2
    echo "  $(manual_hint "$cmd")" >&2
    exit 1
  fi
  echo "Dependency '$cmd' is missing — attempting to install it..." >&2
  install_dep "$cmd" || true
  hash -r 2>/dev/null || true   # forget cached command locations after install
  if ! have "$cmd"; then
    echo "Error: could not install '$cmd' automatically." >&2
    echo "  Install it manually, then re-run:" >&2
    echo "    $(manual_hint "$cmd")" >&2
    [[ "$cmd" == "yt-dlp" ]] && echo "  (if pip used --user, add its bin dir to PATH, e.g. \$(python3 -m site --user-base)/bin)" >&2
    exit 1
  fi
  echo "Installed '$cmd'." >&2
done

# --precise and --segments-only only affect chapter splitting.
if [[ "$PRECISE" -eq 1 && "$CHAPTERS" -eq 0 ]]; then
  echo "Error: --precise has no effect without --chapters." >&2
  exit 1
fi
if [[ "$SEGMENTS_ONLY" -eq 1 && "$CHAPTERS" -eq 0 ]]; then
  echo "Error: --segments-only has no effect without --chapters." >&2
  exit 1
fi

# Temp-file bookkeeping: track everything we create and clean it up on exit.
# TMPFILES lasts for the whole run; ITEM_TMPFILES holds one video's scratch
# files and is emptied after each URL, so a long batch doesn't pile them up.
TMPFILES=()
ITEM_TMPFILES=()
# Both must return 0: as an EXIT trap, a non-zero return here would become the
# script's exit status when it falls off the end (e.g. plain downloads, where
# TMPFILES is empty and the guard would otherwise return 1).
cleanup_item() {
  [[ ${#ITEM_TMPFILES[@]} -gt 0 ]] && rm -f "${ITEM_TMPFILES[@]}"
  ITEM_TMPFILES=()
  return 0
}
cleanup() {
  cleanup_item
  [[ ${#TMPFILES[@]} -gt 0 ]] && rm -f "${TMPFILES[@]}"
  return 0
}
trap cleanup EXIT

# --update: update the yt-dlp on PATH, then exit. Installs vary (pip, Homebrew,
# standalone binary), so pick the method that matches how this copy is
# installed rather than assuming one. yt-dlp's own shebang points at the Python
# that owns it when it's a pip install — the common case — which is exactly the
# interpreter whose pip must do the upgrade.
if [[ "$DO_UPDATE" -eq 1 ]]; then
  ytdlp_path="$(command -v yt-dlp)"
  shebang="$(head -n1 "$ytdlp_path" 2>/dev/null || true)"
  echo "Updating yt-dlp at: $ytdlp_path" >&2
  if [[ "$shebang" == '#!'*python* ]]; then
    # pip install: run the interpreter from the shebang (handles both an
    # absolute path and a `/usr/bin/env python3` form) so we update THIS copy.
    interp="${shebang#\#!}"
    echo "Running: $interp -m pip install -U --pre \"yt-dlp[default]\"" >&2
    # shellcheck disable=SC2086
    exec $interp -m pip install -U --pre "yt-dlp[default]"
  elif [[ "$ytdlp_path" == *"/Cellar/"* || "$ytdlp_path" == *"/homebrew/"* ]]; then
    echo "Running: brew upgrade yt-dlp" >&2
    exec brew upgrade yt-dlp
  else
    # Standalone binary: yt-dlp can self-update in place.
    echo "Running: yt-dlp -U --update-to nightly" >&2
    exec yt-dlp -U --update-to nightly
  fi
fi

# Warn if yt-dlp looks stale. YouTube changes its defenses often and breaks
# older yt-dlp builds (typically surfacing as "HTTP Error 403: Forbidden"),
# so a copy more than STALE_DAYS old is worth updating. yt-dlp versions are
# date-stamped (YYYY.MM.DD[...]), so this is a purely offline check.
STALE_DAYS=30
check_ytdlp_freshness() {
  local ver y m d ver_epoch now_epoch age_days
  ver="$(yt-dlp --version 2>/dev/null)" || return 0
  # Extract the first three dot-separated numeric fields (year.month.day).
  IFS='.' read -r y m d _ <<< "$ver"
  [[ "$y" =~ ^[0-9]+$ && "$m" =~ ^[0-9]+$ && "$d" =~ ^[0-9]+$ ]] || return 0
  # Force base-10 so zero-padded fields (e.g. 08, 09) aren't read as octal.
  y=$((10#$y)); m=$((10#$m)); d=$((10#$d))
  # Convert YYYY-MM-DD to epoch, portably: try GNU `date -d`, then BSD `date -j`.
  local nd; nd="$(printf '%04d-%02d-%02d' "$y" "$m" "$d")"
  ver_epoch="$(date -d "$nd" +%s 2>/dev/null \
    || date -j -f "%Y-%m-%d" "$nd" +%s 2>/dev/null)" || return 0
  [[ "$ver_epoch" =~ ^[0-9]+$ ]] || return 0
  now_epoch="$(date +%s)"
  age_days=$(( (now_epoch - ver_epoch) / 86400 ))
  if (( age_days > STALE_DAYS )); then
    local level="Warning"
    (( STRICT )) && level="Error"
    echo "$level: yt-dlp is $age_days days old (version $ver)." >&2
    echo "       YouTube may reject downloads (HTTP 403) with a stale build." >&2
    echo "       Update the copy on your PATH ($(command -v yt-dlp)):" >&2
    echo "         python3 -m pip install -U --pre \"yt-dlp[default]\"" >&2
    echo "       (or run: $0 --update, or: brew upgrade yt-dlp)" >&2
    echo >&2
    # Under --strict, refuse to download until yt-dlp is updated.
    if (( STRICT )); then
      exit 1
    fi
  fi
}
check_ytdlp_freshness

# --list-formats: show what's available for each URL, then exit.
if [[ "$LIST_ONLY" -eq 1 ]]; then
  for url in "${URLS[@]}"; do
    if [[ ${#URLS[@]} -gt 1 ]]; then
      echo >&2
      echo "=== $url" >&2
    fi
    yt-dlp -F --no-playlist "$url"
  done
  exit 0
fi

mkdir -p "$OUTDIR"

# --chapters: a Python helper turns one video's info into a chapter list —
# preferring native chapters, otherwise timestamps parsed out of the
# description. It is written once here and reused for every URL in the batch.
CHAPTERS_TSV=""
pyhelper=""
if [[ "$CHAPTERS" -eq 1 ]]; then
  pyhelper="$(mktemp -t yt-chapters-py.XXXXXX)"; TMPFILES+=("$pyhelper")
  cat > "$pyhelper" <<'PYEOF'
import json, re, sys

def to_secs(ts):
    s = 0
    for p in ts.split(':'):
        s = s * 60 + int(p)
    return s

def sanitize(t):
    t = re.sub(r'[\\/:*?"<>|]+', ' ', t)      # strip filesystem-unsafe chars
    t = re.sub(r'\s+', ' ', t).strip()
    return t or 'Untitled'

data = json.load(sys.stdin)
dur = data.get('duration') or 0
chapters = data.get('chapters') or []
result = []

if chapters:
    for c in chapters:
        st = c.get('start_time')
        if st is None:
            continue
        en = c.get('end_time')
        result.append([float(st),
                       (float(en) if en is not None else None),
                       (c.get('title') or '').strip()])
else:
    # Fallback: scan the description for lines that begin with a timestamp,
    # e.g. "0:00 Intro", "01:23 - Topic", "(1:02:03) Section".
    ts_re = re.compile(
        r'^\s*[\[\(]?\s*((?:\d{1,2}:)?\d{1,2}:\d{2})\s*[\]\)]?\s*[-–—:|.]*\s*(.*?)\s*$')
    marks = []
    for line in (data.get('description') or '').splitlines():
        m = ts_re.match(line)
        if m:
            marks.append([to_secs(m.group(1)), m.group(2).strip()])
    marks.sort(key=lambda x: x[0])
    dedup = []
    for s, t in marks:
        if dedup and dedup[-1][0] == s:   # drop duplicate start times
            continue
        dedup.append([s, t])
    # Require at least two ascending marks to trust the parse.
    if len(dedup) >= 2:
        for i, (s, t) in enumerate(dedup):
            en = dedup[i + 1][0] if i + 1 < len(dedup) else dur
            result.append([float(s), float(en), t])

# Resolve any missing end times, drop empty/zero-length spans, emit TSV.
for i, (s, en, t) in enumerate(result):
    if en is None:
        en = result[i + 1][0] if i + 1 < len(result) else dur
    if not en or en <= s:
        continue
    sys.stdout.write("%.3f\t%.3f\t%s\n" % (s, en, sanitize(t)))
PYEOF
fi

# Derive one URL's chapter list into a fresh CHAPTERS_TSV, before anything is
# downloaded, so a video with nothing to split fails fast. Returns 1 in that
# case (and when the info can't be fetched) so a batch can skip to the next URL.
build_chapters_tsv() {
  local url="$1" nch
  echo "Fetching video info to locate chapters..." >&2
  CHAPTERS_TSV="$(mktemp -t yt-chapters.XXXXXX)"; ITEM_TMPFILES+=("$CHAPTERS_TSV")
  if ! yt-dlp -J --no-playlist "$url" 2>/dev/null | python3 "$pyhelper" > "$CHAPTERS_TSV"; then
    echo "Error: could not fetch or parse video info for chapters." >&2
    return 1
  fi
  if [[ ! -s "$CHAPTERS_TSV" ]]; then
    echo "Error: no chapters found — the video has no native chapters and no" >&2
    echo "       usable timestamps were parsed from its description. Nothing to" >&2
    echo "       split. Re-run without --chapters to download the whole video." >&2
    return 1
  fi
  nch=$(wc -l < "$CHAPTERS_TSV" | tr -d ' ')
  echo "Found $nch chapter(s):" >&2
  nl -w2 -s'. ' <(cut -f3 "$CHAPTERS_TSV") >&2
  echo >&2
}

# Build the format selector.
#
# Every preferred alternative pairs a video-only stream ([acodec=none]) with an
# audio-only stream ([vcodec=none]) — i.e. true adaptive/DASH streams that we
# merge ourselves. This deliberately excludes YouTube's legacy *progressive*
# formats (e.g. format 18, which carries both codecs in one file): those are
# frequently rejected with "HTTP 403: Forbidden", and low -m caps used to fall
# onto them. A bare progressive file ("b") remains only as the last-ditch
# fallback for videos that genuinely offer nothing else.
#
# Optional height filter, applied to every alternative in the selector.
HFILTER=""
[[ -n "$MAXHEIGHT" ]] && HFILTER="[height<=${MAXHEIGHT}]"

VONLY="bv*[acodec=none]"          # adaptive video-only (no progressive)
AONLY="ba[vcodec=none]"           # adaptive audio-only
if [[ "$REMUX_MP4" -eq 1 ]]; then
  # For MP4 output, prefer H.264 (avc1) video + AAC (mp4a) audio so the remux
  # stays lossless (VP9/AV1 would force a re-encode into MP4). Widen in steps:
  # avc1+aac -> avc1+any audio -> any adaptive video+audio, then last-resort
  # progressive.
  FORMAT="${VONLY}[vcodec^=avc1]${HFILTER}+${AONLY}[acodec^=mp4a]/${VONLY}[vcodec^=avc1]${HFILTER}+${AONLY}/${VONLY}${HFILTER}+${AONLY}/b${HFILTER}"
else
  FORMAT="${VONLY}${HFILTER}+${AONLY}/b${HFILTER}"
fi

# Assemble the yt-dlp arguments every URL in the batch shares. --no-playlist
# keeps each item one video: playlist links are expanded into their videos up
# front (see expand_urls), so everything downstream handles a single video.
BASE_ARGS=(
  --format "$FORMAT"
  --output "$OUTDIR/%(title)s [%(id)s].%(ext)s"
  --embed-metadata
  --embed-thumbnail
  --no-playlist
)

# Keep intermediate files (separate streams and the pre-remux merge) if asked.
if [[ "$KEEP" -eq 1 ]]; then
  BASE_ARGS+=(--keep-video)
fi

# --skip-existing: leave a video alone when its output file is already there.
if [[ "$SKIP_EXISTING" -eq 1 ]]; then
  BASE_ARGS+=(--no-overwrites)
fi

if [[ "$REMUX_MP4" -eq 1 ]]; then
  # Merge to MKV (accepts any codec, always succeeds), then remux to MP4
  # without re-encoding. +faststart moves the index to the front so the file
  # streams/seeks smoothly (QuickTime, AirPlay, web).
  BASE_ARGS+=(
    --merge-output-format mkv
    --remux-video mp4
    --postprocessor-args "VideoRemuxer:-movflags +faststart"
  )
else
  BASE_ARGS+=(--merge-output-format "$CONTAINER")
fi

# Run the download. Without --strict, let yt-dlp's own exit status and error
# text pass straight through. With --strict, additionally detect a runtime
# "HTTP 403: Forbidden" (YouTube blocking a stale/blocked build) and turn it
# into a clearly-diagnosed failure pointing at the fix, rather than leaving the
# raw traceback as the last word. Returns yt-dlp's status, or 3 for a strict 403.
run_download() {
  local url="$1" st
  if (( STRICT )); then
    local log; log="$(mktemp -t yt-download.XXXXXX)"; TMPFILES+=("$log")
    # tee keeps yt-dlp's output live on screen while we also capture it to scan.
    set +e
    yt-dlp "${ARGS[@]}" "$url" 2>&1 | tee "$log"
    st=${PIPESTATUS[0]}
    set -e
    if (( st != 0 )) && grep -qiE "HTTP Error 403|403: Forbidden|Forbidden" "$log"; then
      {
        echo
        echo "Error: YouTube returned HTTP 403 (Forbidden) during download."
        echo "       This almost always means yt-dlp is out of date or blocked;"
        echo "       YouTube changes its defenses and stale builds get rejected."
        echo "       Update the copy on your PATH ($(command -v yt-dlp)) and retry:"
        echo "         $0 --update"
        echo "       (or: python3 -m pip install -U --pre \"yt-dlp[default]\")"
      } >&2
      return 3
    fi
    return "$st"
  else
    set +e
    yt-dlp "${ARGS[@]}" "$url"
    st=$?
    set -e
    return "$st"
  fi
}

# Split a file into one file per chapter. Source is either the just-downloaded
# file (path captured in PATHFILE) or, for --split-file, the user-provided file.
split_into_chapters() {
  local full ext base dir total pad idx num start end title seglen outfile
  if [[ -n "$SPLIT_FILE" ]]; then
    full="$SPLIT_FILE"
  else
    full="$(tail -n1 "$PATHFILE" 2>/dev/null)"
  fi
  if [[ -z "$full" || ! -f "$full" ]]; then
    echo "Error: could not determine the file to split." >&2
    return 1
  fi
  ext="${full##*.}"
  base="$(basename "$full")"; base="${base%.*}"
  dir="$(dirname "$full")/$base"
  mkdir -p "$dir"
  total=$(wc -l < "$CHAPTERS_TSV" | tr -d ' ')
  pad=${#total}; (( pad < 2 )) && pad=2
  if (( PRECISE )); then
    echo "Splitting into $total chapter file(s), frame-exact (re-encoding), in: $dir" >&2
  else
    echo "Splitting into $total chapter file(s) in: $dir" >&2
  fi
  idx=0
  while IFS=$'\t' read -r start end title; do
    idx=$((idx + 1))
    printf -v num "%0${pad}d" "$idx"
    outfile="$dir/${num} - ${title}.${ext}"
    seglen="$(awk -v a="$start" -v b="$end" 'BEGIN{printf "%.3f", b-a}')"
    echo "  [$num/$total] $title" >&2
    # -ss before -i seeks quickly; -t (duration) after -i is version-robust.
    # -nostdin/<dev/null keep ffmpeg off the loop's stdin.
    if (( PRECISE )); then
      # Frame-exact: re-encode. With transcoding, input -ss performs an accurate
      # seek (decodes to the exact time), so cuts land on the right frame.
      # -map 0:V:0 takes the real video (excluding any attached cover image);
      # -map 0:a? includes audio if present.
      ffmpeg -nostdin -v error -y -ss "$start" -i "$full" -t "$seglen" \
        -map 0:V:0 -map "0:a?" \
        -c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p \
        -c:a aac -b:a 192k \
        -avoid_negative_ts make_zero "$outfile" </dev/null
    else
      # Fast path: keyframe-aligned stream copy (no re-encode, may drift a bit).
      ffmpeg -nostdin -v error -y -ss "$start" -i "$full" -t "$seglen" \
        -map 0 -c copy -avoid_negative_ts make_zero "$outfile" </dev/null
    fi
  done < "$CHAPTERS_TSV"
  if (( SEGMENTS_ONLY )) && [[ -n "$SPLIT_FILE" ]]; then
    # Never delete a file the user supplied, even if they passed --segments-only.
    echo "Done. Segments written; kept your source file (never deleted): $full" >&2
  elif (( SEGMENTS_ONLY )); then
    rm -f "$full"
    echo "Done. Removed the full-length video (segments only)." >&2
  else
    echo "Done. Kept the full-length video: $full" >&2
  fi
}

# --split-file: no download; split the existing file using chapters from the URL.
if [[ -n "$SPLIT_FILE" ]]; then
  build_chapters_tsv "${URLS[0]}" || exit 1
  split_into_chapters
  exit 0
fi

# Refuse to start a doomed download when the destination disk lacks room for the
# file plus the transient second copy the merge/remux/split needs. The estimate
# comes from yt-dlp (sum of the selected video+audio streams); if it can't be
# determined, the check is skipped rather than guessed.
human() {
  awk -v b="$1" 'BEGIN{ split("B KB MB GB TB PB", u); i=1;
    while (b >= 1024 && i < 6) { b/=1024; i++ } printf "%.1f %s", b, u[i] }'
}
check_disk_space() {
  local url="$1" est need avail_kb avail
  est="$(yt-dlp -f "$FORMAT" --no-playlist --simulate \
        --print "%(filesize,filesize_approx)s" "$url" 2>/dev/null | head -n1)"
  if ! [[ "$est" =~ ^[0-9]+$ ]] || (( est <= 0 )); then
    echo "Note: could not estimate download size; skipping disk-space check." >&2
    return 0
  fi
  need=$(( est * SPACE_SAFETY ))
  # df -Pk gives POSIX output in 1K blocks; column 4 of the data row is Available.
  avail_kb="$(df -Pk "$OUTDIR" 2>/dev/null | awk 'NR==2 {print $4}')"
  [[ "$avail_kb" =~ ^[0-9]+$ ]] || { echo "Note: could not read free space; skipping check." >&2; return 0; }
  avail=$(( avail_kb * 1024 ))
  if (( avail < need )); then
    {
      echo "Error: not enough free disk space for this download."
      echo "       Estimated download: ~$(human "$est")"
      echo "       Needed (x${SPACE_SAFETY} for the merge/remux's temporary copy): ~$(human "$need")"
      echo "       Free on $(cd "$OUTDIR" && pwd -P): ~$(human "$avail")"
      echo "       Free up space, or pass --skip-space-check to override."
    } >&2
    return 4
  fi
}

# ---------------------------------------------------------------------------
# Playlist expansion. A playlist link is replaced by the videos it contains, so
# every item in the batch is a single video and the per-video machinery
# (chapters, space check, output-path capture) keeps working untouched.
# ---------------------------------------------------------------------------

# Links worth expanding: a playlist, or a channel tab that lists videos. A bare
# channel URL is not one — yt-dlp lists its *tabs*, not its videos — so point it
# at the /videos tab instead.
looks_like_playlist() {
  case "$1" in
    *"/playlist"*|*"list="*)       return 0 ;;
    *"/videos"|*"/videos/"|*"/videos?"*) return 0 ;;
    *"/shorts"|*"/shorts/"|*"/shorts?"*) return 0 ;;
    *"/streams"|*"/streams/"|*"/streams?"*) return 0 ;;
  esac
  return 1
}

expand_urls() {
  local url tmp line entry vid n out=()
  if (( ! EXPAND_PLAYLIST )); then
    return 0
  fi
  for url in "${URLS[@]}"; do
    if ! looks_like_playlist "$url"; then
      out+=("$url"); continue
    fi
    # A '...watch?v=...&list=...' link names one video inside a playlist, which
    # is what you get when you copy the address while watching it. Downloading
    # just that video is almost always what was meant.
    if [[ "$url" == *"v="* ]] && (( ! YES_PLAYLIST )); then
      echo "Note: this link names one video inside a playlist, so only that" >&2
      echo "      video will be downloaded. Pass --yes-playlist (or use the" >&2
      echo "      playlist's own URL) to get every video in it." >&2
      out+=("$url"); continue
    fi
    echo "Expanding playlist: $url" >&2
    tmp="$(mktemp -t yt-playlist.XXXXXX)"; TMPFILES+=("$tmp")
    # --flat-playlist lists entries without visiting each video page. Printing
    # the id alongside the URL covers entries that carry no URL of their own.
    if ! yt-dlp --flat-playlist --print "%(url)s|%(id)s" "$url" > "$tmp" 2>/dev/null; then
      echo "Warning: could not read that playlist; keeping the URL as it is." >&2
      out+=("$url"); continue
    fi
    n=0
    while IFS= read -r line; do
      [[ -z "$line" ]] && continue
      entry="${line%%|*}"
      if [[ "$entry" == http* ]]; then
        vid="$entry"
      else
        vid="${line##*|}"
        [[ -z "$vid" || "$vid" == "NA" ]] && continue
        vid="https://www.youtube.com/watch?v=$vid"
      fi
      out+=("$vid")
      n=$((n + 1))
    done < "$tmp"
    if (( n == 0 )); then
      echo "Warning: that playlist listed no videos; keeping the URL as it is." >&2
      out+=("$url")
    else
      echo "  -> $n video(s)." >&2
    fi
  done
  URLS=("${out[@]}")
}

# ---------------------------------------------------------------------------
# Batch loop: every URL is downloaded (and optionally split) on its own.
# ---------------------------------------------------------------------------

# Everything one video needs, from chapter discovery through splitting. Returns
# the status of whichever step failed, so the caller can record it and move on.
process_one() {
  local url="$1" st
  ARGS=("${BASE_ARGS[@]}")
  PATHFILE=""
  CHAPTERS_TSV=""
  if (( CHAPTERS )); then
    build_chapters_tsv "$url" || return 1
    # Capture the final output path so we know which file to split.
    PATHFILE="$(mktemp -t yt-download-path.XXXXXX)"; ITEM_TMPFILES+=("$PATHFILE")
    ARGS+=(--print-to-file "after_move:filepath" "$PATHFILE")
  fi
  if (( ! SKIP_SPACE_CHECK )); then
    check_disk_space "$url" || return $?
  fi
  # `|| st=$?` is how the status is captured: errexit exempts the left-hand
  # side of ||, so no set +e dance is needed (and toggling errexit inside a
  # called function would defeat one anyway).
  st=0
  run_download "$url" || st=$?
  if (( st != 0 )); then
    return "$st"
  fi
  if (( CHAPTERS )); then
    # With --skip-existing, a video already on disk is never moved into place,
    # so no path is printed. There is nothing new to split; say so and move on
    # rather than treating it as a failure.
    if [[ ! -s "$PATHFILE" ]]; then
      echo "Already downloaded; leaving its existing chapter files alone." >&2
      return 0
    fi
    split_into_chapters || return 1
  fi
  return 0
}

expand_urls

total=${#URLS[@]}
failed_urls=()
failed_codes=()
done_count=0
last_status=0
idx=0

for url in "${URLS[@]}"; do
  idx=$((idx + 1))
  # This marker is what the GUI reads to show overall batch progress; keep the
  # "[n/m]" shape if you change it.
  if (( total > 1 )); then
    echo >&2
    echo "=== [$idx/$total] $url" >&2
  fi
  status=0
  process_one "$url" || status=$?
  cleanup_item
  if (( status == 0 )); then
    done_count=$((done_count + 1))
  else
    last_status=$status
    failed_urls+=("$url")
    failed_codes+=("$status")
    if (( STOP_ON_ERROR )); then
      if (( total > 1 )); then
        echo "Stopping here: [$idx/$total] failed (exit $status) and --stop-on-error is set." >&2
      fi
      break
    fi
    if (( total > 1 )); then
      echo "That one failed (exit $status); carrying on with the next URL." >&2
    fi
  fi
done

# A single URL keeps its exact exit status, exactly as before batch mode.
if (( total == 1 )); then
  exit "$last_status"
fi

echo >&2
echo "Batch summary: $done_count of $total succeeded, ${#failed_urls[@]} failed." >&2
if (( ${#failed_urls[@]} > 0 )); then
  i=0
  while (( i < ${#failed_urls[@]} )); do
    echo "  failed (exit ${failed_codes[$i]}): ${failed_urls[$i]}" >&2
    i=$((i + 1))
  done
  exit 1
fi
exit 0
