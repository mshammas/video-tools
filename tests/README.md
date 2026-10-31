# tests

Tests for `yt_download.py`, the YouTube downloader. Standard library only
(`unittest`), like the rest of the project.

```bash
python3 -m unittest discover -s tests
```

They take well under a second: nothing is downloaded, and neither yt-dlp nor
ffmpeg has to be installed. `tests/support.py` replaces the `subprocess` module
*inside* `yt_download` and records the command lines it would have run, which is
also why the suite runs unchanged on macOS, Linux and Windows.

## Why the goldens are golden

`yt_download.py` is a port of `yt-download.sh`, an 839-line bash script that was
deleted in the same commit that added it (`051f556`). The files under
`tests/golden/` were captured **from that script**, by running it with fake
`yt-dlp` and `ffmpeg` executables that recorded their own argv:

| Golden | Captured from |
| --- | --- |
| `golden/invocations/*.txt` | the whole script, once per case in `cases.py` — its exit code and every yt-dlp/ffmpeg command line |
| `golden/chapters/*.tsv` | the Python helper the script wrote to a temp file and piped `yt-dlp -J` through |
| `golden/playlist_urls.txt` | the script's `looks_like_playlist()` bash function |

So these tests check the port still does what the script did, rather than merely
that it agrees with itself. That distinction is the point: a test written by
reading the new code would have happily locked in a bug the port introduced.
One such bug was caught this way — `/shorts/<id>`, an ordinary Shorts link, was
being treated as a channel's shorts tab and expanded as a playlist.

**The goldens cannot be regenerated.** The script is gone from the tree, and
while it can be recovered (`git show 051f556^:yt-download.sh`), treat a golden
that no longer matches as a regression in `yt_download.py` until proven
otherwise. If a change to the downloader is deliberate, update the golden in the
same commit and say so in the message, so review sees the behaviour change
rather than just a passing suite.

Adding a case to `cases.py` means writing its expected text by hand, since the
capture path no longer exists. `test_every_golden_has_a_case` keeps the two
lists in step.

## Reading a golden

```
exit 0
yt-dlp ["--version"]
yt-dlp ["-J", "--no-playlist", "https://www.youtube.com/watch?v=..."]
ffmpeg ["-nostdin", "-v", "error", ..., "@OUT@/video [id]/01 - One intro.mp4"]
```

The first line is the process exit code. Each line after it is one invocation:
the tool, then its arguments as JSON.

Real paths are replaced by markers — `@OUT@` (output directory), `@SRC@` (the
file given to `--split-file`), `@BATCH@`, `@MISSING@`, `@NOBATCH@`, `@ROOT@` (the
temp directory holding them all, which catches paths the tool derives itself,
such as a chapter folder named after the video) and `@PATHFILE@` (the temp file
`--print-to-file` writes the output path into). Path separators are normalised
to `/`, so one golden serves all three platforms.

## What is pinned elsewhere

Not everything belongs in a golden:

- **The disk-space margin** (`DiskSpaceTests`) — the goldens only cover
  "obviously fits" and "obviously does not", so the ×2 margin is pinned with
  free space reported rather than measured.
- **`--update`'s method choice** (`UpdateTests`) — pip via the shebang's own
  interpreter, `brew upgrade`, or the binary's self-update, decided the way the
  shell script decided it.
- **The format selector** (`FormatSelectorTests`) — in particular that every
  alternative but the last pairs adaptive video with adaptive audio. YouTube
  often answers progressive formats with HTTP 403, so they are a last resort.
- **Two deliberate departures** from the shell script (`CliTests`): a usage
  error exits **2**, not 1, matching argparse and the other tools here; and
  `--mp4` / `--mkv` / `-c` are mutually exclusive instead of last-one-wins.

## Keeping them honest

The suite was checked by mutation — breaking the downloader on purpose and
confirming the tests notice. Dropping `--embed-thumbnail`, dropping the MP4
remux, reverting the `/shorts` fix, removing title sanitising, altering the
disk-space margin and losing the strict-403 exit code are all caught. Worth
repeating after adding tests here: a test that cannot fail is worse than no
test, because it reads like cover.
