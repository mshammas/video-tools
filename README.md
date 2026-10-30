# tools

- [clip_video.py](#clip_videopy): clip a segment out of a local video or a YouTube video
- [yt-download.sh](#yt-downloadsh): download a whole YouTube video in the best quality, optionally split into chapters
- [mix_videos.py](#mix_videospy): join videos one after another (with optional crossfades), in a grid, or blended together
- [video_tools_gui.py](#video_tools_guipy): a desktop window for all three tools

## clip_video.py

Clip a segment out of a video. The source can be a local file or a YouTube link.

### Requirements

- Python 3.8+
- [ffmpeg](https://ffmpeg.org/) on your PATH: `brew install ffmpeg`
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) on your PATH (only needed for YouTube links): `pip install yt-dlp` or `brew install yt-dlp`

### Usage

```
./clip_video.py SOURCE START END [-o FOLDER] [-n NAME] [--fast]
```

| Argument | Description |
| --- | --- |
| `SOURCE` | Path to a local video file, or a YouTube URL |
| `START` | Start time |
| `END` | End time (must be after `START`) |
| `-o`, `--outdir` | Output folder, created if it doesn't exist. Default: the folder you run the command from |
| `-n`, `--name` | Output file name. Default: generated from the source and the times |
| `--fast` | Skip re-encoding. Much faster, but cuts snap to the nearest keyframe |

Times can be written as `HH:MM:SS`, `MM:SS` or `SS`, each with optional fractions of a second:

| Input | Means |
| --- | --- |
| `45` | 45 seconds |
| `2:30` | 2 minutes 30 seconds |
| `1:02:03` | 1 hour 2 minutes 3 seconds |
| `1:02:03.5` | 1 hour 2 minutes 3.5 seconds |

### Examples

Clip 1:30 to 2:45 from a local file. The output is saved in the current folder as `input_clip_000130000-000245000.mp4`:

```bash
./clip_video.py input.mp4 00:01:30 00:02:45
```

Clip from a YouTube video and choose the output name:

```bash
./clip_video.py "https://www.youtube.com/watch?v=VIDEO_ID" 0:30 1:15 -n chorus.mp4
```

Short `youtu.be` links work too:

```bash
./clip_video.py "https://youtu.be/VIDEO_ID" 10 25 -n intro.mp4
```

Clip using seconds with decimals:

```bash
./clip_video.py talk.mov 12.5 48.25 -n highlight.mov
```

Save the clip into a different folder (created if needed), keeping the generated name:

```bash
./clip_video.py input.mp4 00:10 01:30 -o ~/Clips
```

Choose both the folder and the file name:

```bash
./clip_video.py "https://youtu.be/VIDEO_ID" 1:00 2:00 -o ~/Clips -n best-part.mp4
```

Quickly cut a long section from a large file without re-encoding:

```bash
./clip_video.py movie.mkv 1:02:03 1:15:00 --fast -n scene.mkv
```

### How it works

- **Local files** are cut with ffmpeg. By default the clip is re-encoded (H.264 video, AAC audio) so the cut lands on the exact frame. With `--fast`, the streams are copied as-is.
- **YouTube links** (any `http`/`https` URL) are handed to yt-dlp, which downloads only the requested section instead of the whole video. Other sites that yt-dlp supports should work as well.

### Accurate vs fast cuts

Video is stored as keyframes with partial frames in between, and a clip can only start cleanly on a keyframe.

- **Default:** re-encodes the clip so it starts and ends exactly at the times you give. Slower, and takes longer for long clips.
- **`--fast`:** copies the original streams without re-encoding. Nearly instant and loses no quality, but the start can be off by a second or more.

## yt-download.sh

Download a YouTube video in its highest available resolution. It can also split the video into one file per chapter.

### Requirements

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) and [ffmpeg](https://ffmpeg.org/) on your PATH: `brew install yt-dlp ffmpeg`
- Python 3 (only needed for `--chapters`)

### Usage

```
./yt-download.sh [options] <youtube-url>
```

Files are saved as `<title> [<video id>].<ext>`. Run `./yt-download.sh --help` for the full option list.

| Option | Description |
| --- | --- |
| `-o`, `--outdir DIR` | Output directory (default: current directory) |
| `-m`, `--max HEIGHT` | Cap the resolution, e.g. `1080` or `720` |
| `-p`, `--mp4` | MP4 with H.264 + AAC that plays in QuickTime and on iOS. **This is the default.** Tops out at 1080p |
| `--mkv` | MKV with the best available codec (AV1/VP9/H.264). Use this for 1440p and 4K |
| `-c`, `--container EXT` | Advanced: merge straight into `mkv` or `mp4` with the best codec, skipping the H.264 preference |
| `-k`, `--keep` | Keep the intermediate files (separate video/audio streams and the pre-remux merge) |
| `-C`, `--chapters` | Split the video into one file per chapter (see below) |
| `-P`, `--precise` | With `--chapters`, re-encode so each chapter starts and ends on the exact frame |
| `--segments-only` | With `--chapters`, delete the full-length video after splitting |
| `--split-file PATH` | Split a file you already have into chapters, using the chapter list from the URL. Nothing is downloaded |
| `--skip-space-check` | Skip the free-disk-space check before downloading |
| `-F`, `--list-formats` | List the available formats and exit |
| `-u`, `--update` | Update the yt-dlp on your PATH and exit |
| `-s`, `--strict` | Refuse to run if yt-dlp is more than 30 days old, and explain HTTP 403 errors clearly |
| `-h`, `--help` | Show help and exit |

### Examples

Download in the best quality as a QuickTime-friendly MP4 (up to 1080p):

```bash
./yt-download.sh "https://www.youtube.com/watch?v=VIDEO_ID"
```

Download the highest resolution available, including 4K, as MKV:

```bash
./yt-download.sh --mkv "https://www.youtube.com/watch?v=VIDEO_ID"
```

Cap the resolution at 720p and save into a folder:

```bash
./yt-download.sh -m 720 -o ~/Videos "https://www.youtube.com/watch?v=VIDEO_ID"
```

See which formats and resolutions a video offers, without downloading:

```bash
./yt-download.sh -F "https://www.youtube.com/watch?v=VIDEO_ID"
```

Download and split into one file per chapter:

```bash
./yt-download.sh --chapters "https://www.youtube.com/watch?v=VIDEO_ID"
```

Split into frame-exact chapters and keep only the chapter files:

```bash
./yt-download.sh --chapters --precise --segments-only "https://www.youtube.com/watch?v=VIDEO_ID"
```

Split a video you downloaded earlier, using that video's chapters:

```bash
./yt-download.sh --split-file ~/Videos/"My Talk [VIDEO_ID].mp4" "https://www.youtube.com/watch?v=VIDEO_ID"
```

Update yt-dlp (do this first if downloads fail with HTTP 403):

```bash
./yt-download.sh --update
```

### Chapters

With `--chapters`, the script uses the video's own YouTube chapters. If it has none, it looks for timestamped lines in the description, such as `0:00 Intro` or `(1:02:03) Q&A`, and needs at least two of them. If no chapters can be found, it stops before downloading anything.

Chapter files are written to a subfolder named after the video, numbered like `01 - Intro.mp4`, `02 - Setup.mp4`. The full-length video is kept unless you pass `--segments-only`. With `--split-file`, your own file is never deleted.

By default chapters are cut without re-encoding, which is fast but can be off by a second or so at each boundary. `--precise` re-encodes for exact cuts, at the cost of time and a small loss in quality.

### MP4 vs MKV

YouTube only serves H.264 video up to 1080p. Higher resolutions use VP9 or AV1, which don't fit cleanly in an MP4 that QuickTime and iOS can play.

- **MP4 (default):** plays everywhere, up to 1080p.
- **`--mkv`:** the highest resolution available. Plays in VLC, IINA and most modern players, but not QuickTime.

### Troubleshooting

- **HTTP 403: Forbidden:** your yt-dlp is out of date or YouTube has blocked it. Run `./yt-download.sh --update` and try again. The script warns you when yt-dlp is more than 30 days old.
- **"not enough free disk space":** the script needs room for about twice the download size, because merging the streams briefly makes a second copy. Free up space, or pass `--skip-space-check` if you know the estimate is wrong.

| Exit code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | Bad arguments, a missing dependency, no chapters found, or yt-dlp too old under `--strict` |
| `3` | HTTP 403 during download (with `--strict`) |
| `4` | Not enough free disk space |

Other non-zero codes come straight from yt-dlp.

## mix_videos.py

Combine several videos into one. Inputs can have different resolutions, frame rates and formats. Each one is scaled to fit the output size, with black bars where the shape doesn't match.

### Requirements

- Python 3.8+
- [ffmpeg](https://ffmpeg.org/) on your PATH: `brew install ffmpeg`

### Usage

```
./mix_videos.py {concat,grid,blend} INPUT [INPUT ...] -o OUTPUT [options]
```

| Mode | What it does |
| --- | --- |
| `concat` | Plays the videos one after another, optionally with crossfades |
| `grid` | Plays the videos at the same time, side by side in a grid |
| `blend` | Lays the videos on top of each other and averages the frames |

| Option | Description |
| --- | --- |
| `-o`, `--output` | Output file (required) |
| `--size WxH` | Output resolution. Default: `1280x720` |
| `--fps` | Output frame rate. Default: `30` |
| `--crossfade SECONDS` | `concat` only: how long each clip overlaps the next. Default: `0` (hard cuts) |
| `--transition NAME` | `concat` only: the crossfade effect, e.g. `fade`, `dissolve`, `fadeblack`, `wipeleft`, `slideright`, `circleopen`. Default: `fade` |
| `--cols` | `grid` only: number of columns. Default: as square as possible |
| `--shortest` | `grid` and `blend`: stop when the shortest video ends. Default: run until the longest one ends |
| `--weights` | `blend` only: one weight per input, e.g. `--weights 2 1` |
| `--crf` | Quality, lower is better. Default: `20` |
| `--preset` | x264 speed preset. Default: `medium` |
| `-y`, `--overwrite` | Overwrite the output file if it exists |
| `--dry-run` | Print the ffmpeg command without running it |

### Examples

Join three clips with hard cuts:

```bash
./mix_videos.py concat a.mp4 b.mov c.mkv -o joined.mp4
```

Join clips with a one-second dissolve between each:

```bash
./mix_videos.py concat a.mp4 b.mp4 c.mp4 -o faded.mp4 --crossfade 1 --transition dissolve
```

Show four videos in a 2×2 grid at 1080p:

```bash
./mix_videos.py grid a.mp4 b.mp4 c.mp4 d.mp4 -o grid.mp4 --size 1920x1080
```

Overlay two videos, with the first one three times as strong:

```bash
./mix_videos.py blend a.mp4 b.mp4 -o blended.mp4 --weights 3 1 --shortest
```

### Audio

- **`concat`:** a clip with no audio gets silence for its length, so sound stays in sync. With `--crossfade`, the audio fades between clips along with the picture.
- **`grid` and `blend`:** the audio of every input that has some is mixed together. Inputs without audio are left out.

The crossfade has to be shorter than the shortest clip.

## video_tools_gui.py

A desktop window for `clip_video.py`, `yt-download.sh` and `mix_videos.py`. It has a tab for each tool, builds the same command you would type in the terminal, and shows the output as it runs.

### Requirements

- Python 3.8+ with tkinter (included with the python.org and Homebrew Python installs)
- The same tools the scripts need: ffmpeg, and yt-dlp for YouTube

### Usage

```bash
python3 video_tools_gui.py
```

| Tab | Tool | What you can set |
| --- | --- | --- |
| Clip | `clip_video.py` | Source file or URL, start and end times, output folder, file name, fast cut |
| Download | `yt-download.sh` | URL, output folder, MP4 or MKV, max resolution, chapter splitting, splitting an existing file. Also has buttons to list formats and update yt-dlp |
| Mix | `mix_videos.py` | Videos in order, mode, crossfade and transition, grid columns, blend weights, output size, frame rate, quality and speed |

All tabs share one log pane and a progress bar at the bottom of the window.

- **Stop** ends the running job.
- **Open folder** shows the output in Finder.
- **Show command** (Mix tab) prints the command without running it.

Inputs are checked before anything runs, so mistakes like a bad time, a missing file or a crossfade longer than a clip are reported straight away. Only one job runs at a time.
