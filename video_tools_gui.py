#!/usr/bin/env python3
"""Desktop GUI for the video tools in this folder.

Tabs:
  Clip      clip_video.py   - cut a segment from a local file or YouTube link
  Download  yt_download.py  - download YouTube videos (one, many or a playlist),
                              optionally split by chapter
  Mix       mix_videos.py   - concat (with crossfades), grid or blend videos

Each tab builds the same command you would type in a terminal and runs it,
streaming the output into the log pane. Only the Python standard library is
needed (tkinter). The underlying tools need ffmpeg / yt-dlp, which the commands
auto-install when missing (see deps.py); the output streams into the log.

The Help menu checks GitHub Releases for a newer version (see updates.py); the
daily automatic check can be switched off there.

Runs on macOS, Linux and Windows, with no shell required: every tab is pure
Python and needs only ffmpeg / yt-dlp.

Run:  python3 video_tools_gui.py
"""

import os
import queue
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

# When frozen by PyInstaller, bundled files live in sys._MEIPASS and sys.executable
# is this app (not a Python interpreter). Resolve paths from the right base either way.
if getattr(sys, "frozen", False):
    HERE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _run_subtool_and_exit():
    """`<app> --run-tool NAME ...` runs a bundled sub-tool instead of the GUI.

    The GUI shells out to sys.executable to run clip_video / yt_download /
    mix_videos / deps.
    When frozen, sys.executable IS this app, so it must be able to act as those
    tools. We dispatch here, before importing tkinter, and never return.
    """
    if len(sys.argv) >= 3 and sys.argv[1] == "--run-tool":
        name = sys.argv[2]
        sys.argv = [name, *sys.argv[3:]]          # argparse in each tool reads sys.argv
        if name == "clip_video":
            import clip_video
            sys.exit(clip_video.main())
        if name == "yt_download":
            import yt_download
            sys.exit(yt_download.main())
        if name == "mix_videos":
            import mix_videos
            sys.exit(mix_videos.main())
        if name == "deps":
            import deps
            sys.exit(deps._main(sys.argv))
        sys.exit(f"unknown --run-tool target: {name!r}")


_run_subtool_and_exit()

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError as exc:  # tkinter ships separately from Python on many Linux distros
    sys.stderr.write(
        "Error: Python's tkinter GUI toolkit is not available (%s).\n"
        "  Linux:   sudo apt-get install python3-tk   (Fedora: sudo dnf install python3-tkinter)\n"
        "  Windows: reinstall Python from python.org with the 'tcl/tk and IDLE' option ticked\n"
        "  macOS:   brew install python-tk            (only if you use Homebrew's Python)\n" % exc
    )
    sys.exit(1)

import appinfo  # noqa: E402
import clip_video  # noqa: E402
import deps  # noqa: E402  (importing it also puts the managed tool-bin dir on PATH)
import mix_videos  # noqa: E402
import updates  # noqa: E402

ICON_PNG = HERE / "assets" / "icon.png"

DEFAULT_OUTDIR = str(Path.home() / "Movies" if (Path.home() / "Movies").is_dir() else Path.home())
VIDEO_TYPES = [("Video files", "*.mp4 *.mov *.mkv *.m4v *.webm *.avi"), ("All files", "*")]
TRANSITIONS = ["fade", "dissolve", "fadeblack", "fadewhite", "wipeleft", "wiperight", "wipeup",
               "wipedown", "slideleft", "slideright", "slideup", "slidedown", "circleopen",
               "circleclose", "radial", "pixelize", "distance", "smoothleft", "smoothright"]
PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower",
           "veryslow"]

FFMPEG_TIME_RE = re.compile(r"time=\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
PERCENT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)%")
# yt_download.py prints "=== [3/7] <url>" before each video of a batch, which is
# how we turn one video's progress into progress over the whole batch.
BATCH_RE = re.compile(r"^=== \[(\d+)/(\d+)\]", re.M)


def tool_cmd(name, *args):
    """Command to run a bundled Python sub-tool (clip_video / yt_download /
    mix_videos / deps).

    From source: run the .py with this interpreter. When frozen: re-invoke this
    app with --run-tool, which the dispatcher turns into that tool (PYTHONUNBUFFERED
    in tool_env keeps the output live without a -u flag)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-tool", name, *args]
    return [sys.executable, "-u", str(HERE / f"{name}.py"), *args]


def tool_env():
    """Make sure the tools are findable: apps launched from a GUI often inherit a
    minimal PATH that omits Homebrew, pip's user bin, or the Python Scripts dir."""
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    extra = [str(Path(sys.executable).parent)]
    try:  # pip --user scripts dir (e.g. ~/.local/bin, or %APPDATA%\Python\...\Scripts)
        import sysconfig
        extra.append(sysconfig.get_path("scripts", f"{os.name}_user"))
    except Exception:
        pass
    if os.name == "nt":
        extra.append(str(Path(sys.executable).parent / "Scripts"))
    else:
        extra += ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
                  str(Path.home() / ".local" / "bin")]
    parts = env.get("PATH", "").split(os.pathsep)
    env["PATH"] = os.pathsep.join(parts + [p for p in extra if p and p not in parts])
    return env


def _decode(chunk):
    """Child output as text. Some Windows programs (the WSL launcher, a few
    installers) answer in UTF-16, which read as UTF-8 becomes text riddled with
    NULs — the log would swallow it and the failure would look silent."""
    if b"\x00" in chunk:
        text = chunk.decode("utf-16-le", errors="replace")
    else:
        text = chunk.decode("utf-8", errors="replace")
    return text.replace("\x00", "")


class Runner:
    """Runs one command at a time and streams its output into a Text widget."""

    def __init__(self, root, log, progress, status):
        self.root, self.log, self.progress, self.status = root, log, progress, status
        self.proc = None
        self.queue = queue.Queue()
        self.total_seconds = None
        self.on_finish = None
        self.batch_index = 0
        self.batch_total = 0
        self.run_buttons = []
        self.stop_button = None
        self.log.mark_set("linestart", "end-1c")
        self.log.mark_gravity("linestart", "left")
        self.root.after(50, self._drain)

    @property
    def busy(self):
        return self.proc is not None

    def start(self, cmd, cwd=None, total_seconds=None, on_finish=None):
        if self.busy:
            messagebox.showinfo("Busy", "A job is already running. Stop it or wait for it to finish.")
            return
        self.total_seconds = total_seconds
        self.on_finish = on_finish
        self.batch_index = self.batch_total = 0
        self._write(f"\n$ {shlex.join(cmd)}\n", "cmd")
        popen_kw = dict(cwd=cwd, env=tool_env(), stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        # Put the child in its own group so Stop can terminate the whole tree
        # (ffmpeg/yt-dlp children included), portably across POSIX and Windows.
        if os.name == "nt":
            popen_kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kw["start_new_session"] = True
        try:
            self.proc = subprocess.Popen(cmd, **popen_kw)
        except OSError as exc:
            self._write(f"Could not start: {exc}\n", "err")
            return
        self._set_running(True)
        self.progress.configure(mode="determinate", value=0)
        if total_seconds is None:
            self.progress.configure(mode="indeterminate")
            self.progress.start(15)
        self.status.set("Running…")
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                if os.name == "nt":
                    # No killpg on Windows; taskkill /T ends the whole tree.
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                                   capture_output=True)
                else:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            self.status.set("Stopping…")

    def _reader(self, proc):
        while True:
            chunk = os.read(proc.stdout.fileno(), 4096)
            if not chunk:
                break
            self.queue.put(("out", _decode(chunk)))
        self.queue.put(("done", proc.wait()))

    def _drain(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "out":
                    self._write(payload)
                    self._update_progress(payload)
                else:
                    self._finished(payload)
        except queue.Empty:
            pass
        self.root.after(50, self._drain)

    def _write(self, text, tag=None):
        """Append text, treating a bare \\r as 'overwrite the current line' like a terminal."""
        text = text.replace("\r\n", "\n")
        for piece in re.split(r"(\r|\n)", text):
            if piece == "\n":
                self.log.insert("end-1c", "\n", tag)
                self.log.mark_set("linestart", "end-1c")
            elif piece == "\r":
                self.log.delete("linestart", "end-1c")
            elif piece:
                self.log.insert("end-1c", piece, tag)
        self.log.see("end")

    def note(self, text):
        """Write a line of our own into the log, as context for what follows."""
        self._write(text if text.endswith("\n") else text + "\n", "cmd")

    def _determinate(self):
        if str(self.progress.cget("mode")) != "determinate":
            self.progress.stop()
            self.progress.configure(mode="determinate")

    def _update_progress(self, text):
        markers = BATCH_RE.findall(text)
        if markers:
            self.batch_index, self.batch_total = (int(n) for n in markers[-1])
            self._determinate()
            self.progress.configure(value=100 * (self.batch_index - 1) / self.batch_total)
            self.status.set(f"Video {self.batch_index} of {self.batch_total}…")
        percent = None
        if self.total_seconds:
            times = FFMPEG_TIME_RE.findall(text)
            if times:
                h, m, s = times[-1]
                percent = 100 * (int(h) * 3600 + int(m) * 60 + float(s)) / self.total_seconds
        if percent is None and "[download]" in text:
            found = PERCENT_RE.findall(text)
            if found:
                percent = float(found[-1])
        if percent is None:
            return
        percent = max(0, min(100, percent))
        self._determinate()
        if self.batch_total > 1:
            # One video's own progress fills only its slice of the batch.
            done = self.batch_index - 1 + percent / 100
            self.progress.configure(value=100 * done / self.batch_total)
            self.status.set(
                f"Video {self.batch_index} of {self.batch_total} — {percent:.0f}%")
        else:
            self.progress.configure(value=percent)

    def _finished(self, code):
        self.proc = None
        self.progress.stop()
        # A failed batch usually still got most of the way, so leave the bar
        # where it stopped rather than implying nothing happened.
        if code != 0 and self.batch_total > 1:
            self.progress.configure(mode="determinate")
        else:
            self.progress.configure(mode="determinate", value=100 if code == 0 else 0)
        self._set_running(False)
        if code == 0:
            self.status.set("Done.")
            self._write("✓ Finished.\n", "ok")
        elif code < 0:
            self.status.set("Stopped.")
            self._write("■ Stopped.\n", "err")
        else:
            self.status.set(f"Failed (exit code {code}).")
            self._write(f"✗ Failed with exit code {code}.\n", "err")
        if self.on_finish:
            self.on_finish(code)

    def _set_running(self, running):
        for button in self.run_buttons:
            button.configure(state="disabled" if running else "normal")
        if self.stop_button:
            self.stop_button.configure(state="normal" if running else "disabled")


def browse_dir(var):
    path = filedialog.askdirectory(initialdir=var.get() or DEFAULT_OUTDIR)
    if path:
        var.set(path)


def open_in_finder(path):
    if not path or not Path(path).exists():
        return
    opener = {"darwin": ["open"], "win32": ["explorer"]}.get(sys.platform, ["xdg-open"])
    subprocess.Popen(opener + [path])


def ensure_ready(runner, tools):
    """Return True if every tool is available. Otherwise offer to fetch the
    missing ones (deps.py downloads prebuilt binaries for users with no package
    manager), run that in the log, and return False so the caller re-tries after."""
    missing = [t for t in tools if deps.which(t) is None]
    if not missing:
        return True
    if runner.busy:
        messagebox.showinfo("Busy", "Finish the current job first, then try again.")
        return False
    if messagebox.askyesno(
            "Set up tools",
            "These tools are needed and will be downloaded now:\n    "
            + ", ".join(missing)
            + "\n\nThis is a one-time setup. Progress shows in the log below — "
              "click the button again once it finishes."):
        runner.start(tool_cmd("deps", *missing), cwd=str(HERE))
    return False


def row_label(parent, text, row):
    ttk.Label(parent, text=text).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)


class ClipTab(ttk.Frame):
    def __init__(self, parent, runner):
        super().__init__(parent, padding=12)
        self.runner = runner
        self.source = tk.StringVar()
        self.start = tk.StringVar(value="0:00")
        self.end = tk.StringVar()
        self.outdir = tk.StringVar(value=DEFAULT_OUTDIR)
        self.name = tk.StringVar()
        self.fast = tk.BooleanVar()
        self.columnconfigure(1, weight=1)

        row_label(self, "Source (file or URL)", 0)
        ttk.Entry(self, textvariable=self.source).grid(row=0, column=1, sticky="ew")
        ttk.Button(self, text="Browse…", command=self.pick_source).grid(row=0, column=2, padx=(6, 0))

        times = ttk.Frame(self)
        times.grid(row=1, column=1, sticky="w")
        row_label(self, "Start / End", 1)
        ttk.Entry(times, textvariable=self.start, width=12).pack(side="left")
        ttk.Label(times, text=" to ").pack(side="left")
        ttk.Entry(times, textvariable=self.end, width=12).pack(side="left")
        ttk.Label(times, text="   HH:MM:SS, MM:SS or seconds", foreground="gray").pack(side="left")

        row_label(self, "Output folder", 2)
        ttk.Entry(self, textvariable=self.outdir).grid(row=2, column=1, sticky="ew")
        ttk.Button(self, text="Browse…", command=lambda: browse_dir(self.outdir)).grid(
            row=2, column=2, padx=(6, 0))

        row_label(self, "File name (optional)", 3)
        ttk.Entry(self, textvariable=self.name).grid(row=3, column=1, sticky="ew")

        ttk.Checkbutton(self, text="Fast cut (no re-encode; snaps to nearest keyframe)",
                        variable=self.fast).grid(row=4, column=1, sticky="w", pady=6)

        buttons = ttk.Frame(self)
        buttons.grid(row=5, column=0, columnspan=3, sticky="e", pady=(8, 0))
        ttk.Button(buttons, text="Open folder", command=lambda: open_in_finder(self.outdir.get())
                   ).pack(side="left", padx=(0, 6))
        run = ttk.Button(buttons, text="Clip", command=self.run)
        run.pack(side="left")
        runner.run_buttons.append(run)

    def pick_source(self):
        path = filedialog.askopenfilename(filetypes=VIDEO_TYPES)
        if path:
            self.source.set(path)

    def run(self):
        source = self.source.get().strip()
        if not source:
            return messagebox.showerror("Clip", "Choose a source file or paste a URL.")
        try:
            start = clip_video.parse_time(self.start.get())
            end = clip_video.parse_time(self.end.get())
        except Exception as exc:
            return messagebox.showerror("Clip", str(exc))
        if end <= start:
            return messagebox.showerror("Clip", "End time must be after the start time.")
        if not clip_video.is_url(source) and not Path(source).is_file():
            return messagebox.showerror("Clip", f"File not found:\n{source}")

        tools = ["ffmpeg"] + (["yt-dlp"] if clip_video.is_url(source) else [])
        if not ensure_ready(self.runner, tools):
            return

        cmd = tool_cmd("clip_video", source,
                       self.start.get().strip(), self.end.get().strip())
        outdir = self.outdir.get().strip() or DEFAULT_OUTDIR
        cmd += ["-o", outdir]
        if self.name.get().strip():
            cmd += ["-n", self.name.get().strip()]
        if self.fast.get():
            cmd.append("--fast")
        self.runner.start(cmd, cwd=str(HERE), total_seconds=end - start)


class DownloadTab(ttk.Frame):
    def __init__(self, parent, runner):
        super().__init__(parent, padding=12)
        self.runner = runner
        self.outdir = tk.StringVar(value=DEFAULT_OUTDIR)
        self.fmt = tk.StringVar(value="mp4")
        self.max_height = tk.StringVar(value="Best")
        self.chapters = tk.BooleanVar()
        self.precise = tk.BooleanVar()
        self.segments_only = tk.BooleanVar()
        self.keep = tk.BooleanVar()
        self.split_file = tk.StringVar()
        self.expand_playlists = tk.BooleanVar(value=True)
        self.skip_existing = tk.BooleanVar(value=True)
        self.stop_on_error = tk.BooleanVar()
        self.count = tk.StringVar()
        self.columnconfigure(1, weight=1)

        ttk.Label(self, text="YouTube URLs").grid(row=0, column=0, sticky="nw",
                                                  padx=(0, 8), pady=3)
        urls = ttk.Frame(self)
        urls.grid(row=0, column=1, columnspan=2, sticky="ew")
        urls.columnconfigure(0, weight=1)
        self.urls_text = tk.Text(urls, height=4, wrap="none", undo=True)
        self.urls_text.grid(row=0, column=0, sticky="ew")
        bar = ttk.Scrollbar(urls, orient="vertical", command=self.urls_text.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.urls_text.configure(yscrollcommand=bar.set)

        hint = ttk.Frame(self)
        hint.grid(row=1, column=1, columnspan=2, sticky="ew")
        ttk.Label(hint, text="One per line — they download one after another. With "
                             "“Expand playlists” on, a playlist link downloads every "
                             "video in it; untick it for just the one video.",
                  foreground="gray").pack(side="left")
        ttk.Label(hint, textvariable=self.count, foreground="gray").pack(side="right")

        row_label(self, "Output folder", 2)
        ttk.Entry(self, textvariable=self.outdir).grid(row=2, column=1, sticky="ew")
        ttk.Button(self, text="Browse…", command=lambda: browse_dir(self.outdir)).grid(
            row=2, column=2, padx=(6, 0))

        row_label(self, "Format", 3)
        fmt = ttk.Frame(self)
        fmt.grid(row=3, column=1, sticky="w")
        ttk.Radiobutton(fmt, text="MP4 (plays everywhere, up to 1080p)", value="mp4",
                        variable=self.fmt).pack(side="left")
        ttk.Radiobutton(fmt, text="MKV (highest quality, 4K)", value="mkv",
                        variable=self.fmt).pack(side="left", padx=(12, 0))

        row_label(self, "Max resolution", 4)
        ttk.Combobox(self, textvariable=self.max_height, width=8, state="readonly",
                     values=["Best", "2160", "1440", "1080", "720", "480", "360"]).grid(
            row=4, column=1, sticky="w")

        row_label(self, "Chapters", 5)
        chap = ttk.Frame(self)
        chap.grid(row=5, column=1, columnspan=2, sticky="w")
        ttk.Checkbutton(chap, text="Split into chapters", variable=self.chapters,
                        command=self.update_state).pack(side="left")
        self.precise_cb = ttk.Checkbutton(chap, text="Frame-exact (re-encode)",
                                          variable=self.precise)
        self.precise_cb.pack(side="left", padx=(12, 0))
        self.segments_cb = ttk.Checkbutton(chap, text="Keep only chapter files",
                                           variable=self.segments_only)
        self.segments_cb.pack(side="left", padx=(12, 0))

        row_label(self, "Several videos", 6)
        batch = ttk.Frame(self)
        batch.grid(row=6, column=1, columnspan=2, sticky="w")
        ttk.Checkbutton(batch, text="Expand playlists", variable=self.expand_playlists
                        ).pack(side="left")
        ttk.Checkbutton(batch, text="Skip ones already downloaded",
                        variable=self.skip_existing).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(batch, text="Stop at the first failure",
                        variable=self.stop_on_error).pack(side="left", padx=(12, 0))

        row_label(self, "Split existing file", 7)
        self.split_entry = ttk.Entry(self, textvariable=self.split_file)
        self.split_entry.grid(row=7, column=1, sticky="ew")
        self.split_browse = ttk.Button(self, text="Browse…", command=self.pick_split_file)
        self.split_browse.grid(row=7, column=2, padx=(6, 0))
        ttk.Label(self, text="Optional: split a file you already have using the URL's chapters "
                             "(nothing is downloaded). One URL only.",
                  foreground="gray").grid(row=8, column=1, columnspan=2, sticky="w")

        ttk.Checkbutton(self, text="Keep intermediate files", variable=self.keep).grid(
            row=9, column=1, sticky="w", pady=6)

        buttons = ttk.Frame(self)
        buttons.grid(row=10, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        for text, command in [("Update yt-dlp", self.update_ytdlp),
                              ("List formats", self.list_formats)]:
            b = ttk.Button(buttons, text=text, command=command)
            b.pack(side="left", padx=(0, 6))
            runner.run_buttons.append(b)
        run = ttk.Button(buttons, text="Download", command=self.run)
        run.pack(side="right")
        runner.run_buttons.append(run)
        ttk.Button(buttons, text="Open folder", command=lambda: open_in_finder(self.outdir.get())
                   ).pack(side="right", padx=(0, 6))
        self.split_file.trace_add("write", lambda *_: self.update_state())
        # A Text has no trace; <<Modified>> fires once, so re-arm it each time.
        self.urls_text.bind("<<Modified>>", self.on_urls_modified)
        self.update_state()

    def on_urls_modified(self, _event=None):
        self.urls_text.edit_modified(False)
        self.update_state()

    def urls(self):
        """Every non-blank line of the URL box, in order, duplicates dropped."""
        seen, out = set(), []
        for line in self.urls_text.get("1.0", "end").splitlines():
            line = line.strip()
            if line and line not in seen:
                seen.add(line)
                out.append(line)
        return out

    def update_state(self):
        urls = self.urls()
        self.count.set(f"{len(urls)} URL{'s' if len(urls) != 1 else ''}" if urls else "")
        split_file = bool(self.split_file.get().strip())
        on = self.chapters.get() or split_file
        self.precise_cb.configure(state="normal" if on else "disabled")
        self.segments_cb.configure(
            state="normal" if self.chapters.get() and not split_file else "disabled")
        # Splitting a local file takes its chapters from one video, so it is
        # only offered while the box holds a single URL.
        single = len(urls) <= 1
        self.split_entry.configure(state="normal" if single else "disabled")
        self.split_browse.configure(state="normal" if single else "disabled")

    def pick_split_file(self):
        path = filedialog.askopenfilename(filetypes=VIDEO_TYPES)
        if path:
            self.split_file.set(path)

    def script(self):
        """Command prefix that runs the downloader (see tool_cmd)."""
        return tool_cmd("yt_download")

    def need_urls(self):
        urls = self.urls()
        if not urls:
            messagebox.showerror("Download", "Paste at least one YouTube URL first.")
        return urls

    def update_ytdlp(self):
        self.runner.start(self.script() + ["--update"], cwd=str(HERE))

    def list_formats(self):
        urls = self.need_urls()
        if not urls or not ensure_ready(self.runner, ["yt-dlp"]):
            return
        script = self.script()
        if len(urls) > 1:
            self.runner.note(f"Listing formats for the first URL only: {urls[0]}")
        self.runner.start(script + ["-F", urls[0]], cwd=str(HERE))

    def run(self):
        urls = self.need_urls()
        if not urls:
            return
        if not ensure_ready(self.runner, ["ffmpeg", "yt-dlp"]):
            return
        cmd = self.script()
        split_file = self.split_file.get().strip()
        if split_file:
            if len(urls) > 1:
                return messagebox.showerror(
                    "Download",
                    "Splitting an existing file uses one video's chapters, so leave "
                    "a single URL in the box (or clear the file field to download).")
            if not Path(split_file).is_file():
                return messagebox.showerror("Download", f"File not found:\n{split_file}")
            cmd += ["--split-file", split_file]
        else:
            outdir = self.outdir.get().strip() or DEFAULT_OUTDIR
            cmd += ["-o", outdir]
            cmd.append("--mkv" if self.fmt.get() == "mkv" else "--mp4")
            if self.max_height.get() != "Best":
                cmd += ["-m", self.max_height.get()]
            if self.keep.get():
                cmd.append("--keep")
            if self.chapters.get():
                cmd.append("--chapters")
                if self.segments_only.get():
                    cmd.append("--segments-only")
            if self.expand_playlists.get():
                # A '...watch?v=...&list=...' link (copied while watching) names
                # one video inside a playlist; the box being ticked says the
                # whole playlist was meant.
                cmd.append("--yes-playlist")
            else:
                cmd.append("--no-playlist-expand")
            if self.skip_existing.get():
                cmd.append("--skip-existing")
            if self.stop_on_error.get():
                cmd.append("--stop-on-error")
        if self.precise.get() and (split_file or self.chapters.get()):
            cmd.append("--precise")
        cmd += urls
        self.runner.start(cmd, cwd=str(HERE))


class MixTab(ttk.Frame):
    def __init__(self, parent, runner):
        super().__init__(parent, padding=12)
        self.runner = runner
        self.mode = tk.StringVar(value="concat")
        self.output = tk.StringVar(value=str(Path(DEFAULT_OUTDIR) / "mixed.mp4"))
        self.size = tk.StringVar(value="1280x720")
        self.fps = tk.StringVar(value="30")
        self.crossfade = tk.StringVar(value="0")
        self.transition = tk.StringVar(value="fade")
        self.cols = tk.StringVar(value="auto")
        self.shortest = tk.BooleanVar()
        self.weights = tk.StringVar()
        self.crf = tk.IntVar(value=20)
        self.preset = tk.StringVar(value="medium")
        self.overwrite = tk.BooleanVar(value=True)
        self.columnconfigure(0, weight=1)

        files = ttk.LabelFrame(self, text="Input videos (in order)", padding=8)
        files.grid(row=0, column=0, sticky="nsew")
        files.columnconfigure(0, weight=1)
        self.listbox = tk.Listbox(files, height=6, selectmode="extended", activestyle="none")
        self.listbox.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(files, orient="vertical", command=self.listbox.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=scroll.set)
        side = ttk.Frame(files)
        side.grid(row=0, column=2, sticky="n", padx=(8, 0))
        for text, command in [("Add…", self.add_files), ("Remove", self.remove_selected),
                              ("Move up", lambda: self.move(-1)),
                              ("Move down", lambda: self.move(1)), ("Clear", self.clear)]:
            ttk.Button(side, text=text, command=command, width=10).pack(pady=1)

        mode = ttk.Frame(self)
        mode.grid(row=1, column=0, sticky="w", pady=(10, 4))
        ttk.Label(mode, text="Mode:").pack(side="left", padx=(0, 8))
        for value, text in [("concat", "Concat (one after another)"),
                            ("grid", "Grid (side by side)"),
                            ("blend", "Blend (overlay)")]:
            ttk.Radiobutton(mode, text=text, value=value, variable=self.mode,
                            command=self.show_mode_options).pack(side="left", padx=(0, 12))

        self.mode_frames = {}
        concat = ttk.Frame(self)
        ttk.Label(concat, text="Crossfade (seconds):").pack(side="left")
        ttk.Spinbox(concat, textvariable=self.crossfade, from_=0, to=10, increment=0.25,
                    width=6).pack(side="left", padx=(4, 16))
        ttk.Label(concat, text="Transition:").pack(side="left")
        ttk.Combobox(concat, textvariable=self.transition, values=TRANSITIONS, width=12,
                     state="readonly").pack(side="left", padx=4)
        self.mode_frames["concat"] = concat

        grid = ttk.Frame(self)
        ttk.Label(grid, text="Columns:").pack(side="left")
        ttk.Combobox(grid, textvariable=self.cols, width=6,
                     values=["auto", "1", "2", "3", "4", "5", "6"]).pack(side="left", padx=(4, 16))
        ttk.Checkbutton(grid, text="Stop when the shortest video ends",
                        variable=self.shortest).pack(side="left")
        self.mode_frames["grid"] = grid

        blend = ttk.Frame(self)
        ttk.Label(blend, text="Weights:").pack(side="left")
        ttk.Entry(blend, textvariable=self.weights, width=14).pack(side="left", padx=(4, 4))
        ttk.Label(blend, text="e.g. 2 1 (one per video, blank = equal)",
                  foreground="gray").pack(side="left", padx=(0, 16))
        ttk.Checkbutton(blend, text="Stop at shortest", variable=self.shortest).pack(side="left")
        self.mode_frames["blend"] = blend

        common = ttk.LabelFrame(self, text="Output", padding=8)
        common.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        common.columnconfigure(1, weight=1)
        ttk.Label(common, text="Save as").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(common, textvariable=self.output).grid(row=0, column=1, sticky="ew")
        ttk.Button(common, text="Browse…", command=self.pick_output).grid(row=0, column=2,
                                                                         padx=(6, 0))
        opts = ttk.Frame(common)
        opts.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(opts, text="Size").pack(side="left")
        ttk.Combobox(opts, textvariable=self.size, width=10,
                     values=["3840x2160", "1920x1080", "1280x720", "1080x1920", "1080x1080",
                             "854x480"]).pack(side="left", padx=(4, 12))
        ttk.Label(opts, text="FPS").pack(side="left")
        ttk.Combobox(opts, textvariable=self.fps, width=5,
                     values=["24", "25", "30", "50", "60"]).pack(side="left", padx=(4, 12))
        ttk.Label(opts, text="Quality (CRF)").pack(side="left")
        ttk.Spinbox(opts, textvariable=self.crf, from_=0, to=51, width=4).pack(
            side="left", padx=(4, 12))
        ttk.Label(opts, text="Speed").pack(side="left")
        ttk.Combobox(opts, textvariable=self.preset, values=PRESETS, width=9,
                     state="readonly").pack(side="left", padx=(4, 12))
        ttk.Checkbutton(opts, text="Overwrite", variable=self.overwrite).pack(side="left")

        buttons = ttk.Frame(self)
        buttons.grid(row=4, column=0, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Show command", command=self.show_command).pack(
            side="left", padx=(0, 6))
        ttk.Button(buttons, text="Open folder",
                   command=lambda: open_in_finder(str(Path(self.output.get()).parent))).pack(
            side="left", padx=(0, 6))
        run = ttk.Button(buttons, text="Mix", command=self.run)
        run.pack(side="left")
        runner.run_buttons.append(run)
        self.show_mode_options()

    # File list -----------------------------------------------------------------
    def add_files(self):
        for path in filedialog.askopenfilenames(filetypes=VIDEO_TYPES):
            self.listbox.insert("end", path)

    def remove_selected(self):
        for index in reversed(self.listbox.curselection()):
            self.listbox.delete(index)

    def clear(self):
        self.listbox.delete(0, "end")

    def move(self, step):
        selected = list(self.listbox.curselection())
        if not selected:
            return
        size = self.listbox.size()
        if (step < 0 and selected[0] == 0) or (step > 0 and selected[-1] == size - 1):
            return
        for index in (selected if step < 0 else reversed(selected)):
            text = self.listbox.get(index)
            self.listbox.delete(index)
            self.listbox.insert(index + step, text)
        self.listbox.selection_clear(0, "end")
        for index in selected:
            self.listbox.selection_set(index + step)

    def pick_output(self):
        current = Path(self.output.get() or DEFAULT_OUTDIR)
        path = filedialog.asksaveasfilename(
            initialdir=str(current.parent), initialfile=current.name, defaultextension=".mp4",
            filetypes=[("MP4", "*.mp4"), ("MOV", "*.mov"), ("MKV", "*.mkv")])
        if path:
            self.output.set(path)

    def show_mode_options(self):
        for frame in self.mode_frames.values():
            frame.grid_forget()
        self.mode_frames[self.mode.get()].grid(row=2, column=0, sticky="w", pady=4)

    # Command -------------------------------------------------------------------
    def build(self):
        """Return (cmd, inputs, mode, crossfade) or raise ValueError with a message for the user."""
        inputs = list(self.listbox.get(0, "end"))
        mode = self.mode.get()
        if not inputs:
            raise ValueError("Add at least one video.")
        if mode != "concat" and len(inputs) < 2:
            raise ValueError(f"{mode.title()} mode needs at least two videos.")
        missing = [p for p in inputs if not Path(p).is_file()]
        if missing:
            raise ValueError("File not found:\n" + "\n".join(missing))
        output = self.output.get().strip()
        if not output:
            raise ValueError("Choose where to save the result.")
        if Path(output).resolve() in {Path(p).resolve() for p in inputs}:
            raise ValueError("The output file can't be one of the inputs.")
        try:
            mix_videos.parse_size(self.size.get())
        except Exception:
            raise ValueError("Size must look like 1280x720.")
        try:
            fps = float(self.fps.get())
            crossfade = float(self.crossfade.get() or 0)
            crf = int(self.crf.get())
        except (ValueError, tk.TclError):
            raise ValueError("FPS, crossfade and quality must be numbers.")
        if fps <= 0 or crossfade < 0 or not 0 <= crf <= 51:
            raise ValueError("FPS must be positive, crossfade non-negative, quality 0–51.")

        cmd = tool_cmd("mix_videos", mode, *inputs,
                       "-o", output, "--size", self.size.get(), "--fps", self.fps.get(),
                       "--crf", str(crf), "--preset", self.preset.get())
        if self.overwrite.get():
            cmd.append("-y")
        if mode == "concat" and crossfade > 0:
            cmd += ["--crossfade", str(crossfade), "--transition", self.transition.get()]
        if mode == "grid" and self.cols.get() not in ("", "auto"):
            if not self.cols.get().isdigit() or int(self.cols.get()) < 1:
                raise ValueError("Columns must be a whole number or 'auto'.")
            cmd += ["--cols", self.cols.get()]
        if mode in ("grid", "blend") and self.shortest.get():
            cmd.append("--shortest")
        if mode == "blend" and self.weights.get().strip():
            weights = self.weights.get().split()
            if len(weights) != len(inputs):
                raise ValueError(f"Give one weight per video ({len(inputs)}), or leave it blank.")
            try:
                [float(w) for w in weights]
            except ValueError:
                raise ValueError("Weights must be numbers, e.g. 2 1.")
            cmd += ["--weights", *weights]
        return cmd, inputs, mode, crossfade

    def expected_seconds(self, inputs, mode, crossfade):
        """Estimate output length so the progress bar can show a percentage."""
        try:
            durations = [mix_videos.probe(p)[0] for p in inputs]
        except SystemExit:  # probe() exits on unreadable files; let the real run report it
            return None
        if not all(durations):
            return None
        if mode == "concat":
            if crossfade and crossfade >= min(durations) and len(durations) > 1:
                raise ValueError(f"Crossfade must be shorter than the shortest clip "
                                 f"({min(durations):.2f}s).")
            return sum(durations) - crossfade * (len(durations) - 1)
        return min(durations) if self.shortest.get() else max(durations)

    def show_command(self):
        try:
            cmd = self.build()[0]
        except ValueError as exc:
            return messagebox.showerror("Mix", str(exc))
        self.runner._write(shlex.join(cmd) + "\n", "cmd")

    def run(self):
        try:
            cmd, inputs, mode, crossfade = self.build()
            total = self.expected_seconds(inputs, mode, crossfade)
        except ValueError as exc:
            return messagebox.showerror("Mix", str(exc))
        if not self.overwrite.get() and Path(self.output.get()).exists():
            return messagebox.showerror("Mix", "The output file already exists. Tick Overwrite "
                                               "or choose another name.")
        if not ensure_ready(self.runner, ["ffmpeg", "ffprobe"]):
            return
        self.runner.start(cmd, cwd=str(HERE), total_seconds=total)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(appinfo.APP_NAME)
        self.geometry("860x720")
        self.minsize(720, 560)
        try:  # window/dock/taskbar icon (frozen app bundles also set it via the installer)
            if ICON_PNG.is_file():
                self._icon = tk.PhotoImage(file=str(ICON_PNG))
                self.iconphoto(True, self._icon)
        except Exception:
            pass

        self._update_settings = updates.load_settings()
        self._update_busy = False

        paned = ttk.PanedWindow(self, orient="vertical")
        paned.pack(fill="both", expand=True, padx=8, pady=8)
        notebook = ttk.Notebook(paned)

        bottom = ttk.Frame(paned)
        bar = ttk.Frame(bottom)
        bar.pack(fill="x", pady=(4, 4))
        status = tk.StringVar(value="Ready.")
        progress = ttk.Progressbar(bar, length=260, maximum=100)
        progress.pack(side="left")
        ttk.Label(bar, textvariable=status).pack(side="left", padx=8)
        stop = ttk.Button(bar, text="Stop", state="disabled")
        stop.pack(side="right")
        clear = ttk.Button(bar, text="Clear log")
        clear.pack(side="right", padx=6)

        log_frame = ttk.Frame(bottom)
        log_frame.pack(fill="both", expand=True)
        log = tk.Text(log_frame, height=12, wrap="word", font=("Menlo", 11),
                      background="#1e1e1e", foreground="#d4d4d4", insertbackground="#d4d4d4")
        log.tag_configure("cmd", foreground="#4fc1ff")
        log.tag_configure("ok", foreground="#6a9955")
        log.tag_configure("err", foreground="#f48771")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=log.yview)
        log.configure(yscrollcommand=scroll.set)
        log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.runner = Runner(self, log, progress, status)
        self.runner.stop_button = stop
        stop.configure(command=self.runner.stop)
        clear.configure(command=lambda: (log.delete("1.0", "end"),
                                         log.mark_set("linestart", "end-1c")))

        notebook.add(ClipTab(notebook, self.runner), text="  Clip  ")
        notebook.add(DownloadTab(notebook, self.runner), text="  Download  ")
        notebook.add(MixTab(notebook, self.runner), text="  Mix  ")
        paned.add(notebook, weight=3)
        paned.add(bottom, weight=2)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.build_menu()
        # Let the window draw first; the check itself runs off the UI thread.
        self.after(1200, self.auto_check_updates)

    # ------------------------------------------------------------- updates --
    def build_menu(self):
        """Menu bar holding the update controls and About (which shows the version)."""
        self.auto_update = tk.BooleanVar(value=self._update_settings.get("auto_check", True))
        menubar = tk.Menu(self)

        if sys.platform == "darwin":
            # On macOS the menu named "apple" IS the application menu, which is
            # where About and Check for Updates belong.
            app_menu = tk.Menu(menubar, name="apple")
            menubar.add_cascade(menu=app_menu)
            app_menu.add_command(label=f"About {appinfo.APP_NAME}", command=self.show_about)
            app_menu.add_separator()
            app_menu.add_command(label="Check for Updates\u2026", command=self.check_for_updates)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="Check for Updates\u2026", command=self.check_for_updates)
        help_menu.add_checkbutton(label="Check for updates automatically",
                                  variable=self.auto_update, command=self.toggle_auto_update)
        help_menu.add_separator()
        help_menu.add_command(label="Release notes",
                              command=lambda: webbrowser.open(updates.RELEASES_PAGE))
        if sys.platform != "darwin":
            help_menu.add_command(label=f"About {appinfo.APP_NAME}", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.configure(menu=menubar)

    def toggle_auto_update(self):
        on = bool(self.auto_update.get())
        self._update_settings = updates.save_settings(auto_check=on)
        self.runner.note("Automatic update checks are %s." % ("on" if on else "off"))

    def show_about(self):
        messagebox.showinfo(
            f"About {appinfo.APP_NAME}",
            f"{appinfo.APP_NAME} {appinfo.APP_VERSION}\n\n"
            f"{appinfo.APP_DESCRIPTION}\n\n{appinfo.APP_WEBSITE}",
        )

    def check_for_updates(self):
        """Menu command: always report the outcome, 'up to date' included."""
        if self._update_busy:
            return
        self._update_busy = True
        self.runner.note(f"Checking for updates (this is version {appinfo.APP_VERSION})\u2026")
        updates.check_in_background(
            lambda release, error: self.after(0, self.on_update_result, release, error, False))

    def auto_check_updates(self):
        """Startup check: at most once a day, and silent unless there is news."""
        if not updates.auto_check_due(self._update_settings):
            return
        self._update_settings = updates.save_settings(last_check=time.time())
        updates.check_in_background(
            lambda release, error: self.after(0, self.on_update_result, release, error, True))

    def on_update_result(self, release, error, silent):
        """Back on the UI thread with the result of a check."""
        self._update_busy = False
        if error:
            self.runner.note(f"Update check: {error}")
            if not silent:
                messagebox.showwarning("Check for Updates", error)
            return
        if not release.is_update:
            self.runner.note(f"Up to date \u2014 {release.version} is the latest release.")
            if not silent:
                messagebox.showinfo(
                    "Check for Updates",
                    f"{appinfo.APP_NAME} {appinfo.APP_VERSION} is the latest version.")
            return
        if silent and updates.is_skipped(release.version, self._update_settings):
            return
        self.offer_update(release, silent)

    def offer_update(self, release, silent):
        """Show what's new and let the user download it, skip it, or wait."""
        self.runner.note(f"Version {release.version} is available "
                         f"(you have {appinfo.APP_VERSION}).")
        win = tk.Toplevel(self)
        win.title("Update Available")
        win.transient(self)
        frame = ttk.Frame(win, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=f"{appinfo.APP_NAME} {release.version} is available.",
                  font=("TkDefaultFont", 13, "bold")).pack(anchor="w")
        subtitle = f"You have {appinfo.APP_VERSION}."
        if release.published:
            subtitle += f"   Released {release.published}."
        ttk.Label(frame, text=subtitle).pack(anchor="w", pady=(2, 10))

        if release.notes:
            ttk.Label(frame, text="What's new").pack(anchor="w")
            notes_frame = ttk.Frame(frame)
            notes_frame.pack(fill="both", expand=True, pady=(2, 12))
            notes = tk.Text(notes_frame, width=62, height=10, wrap="word")
            notes_scroll = ttk.Scrollbar(notes_frame, orient="vertical", command=notes.yview)
            notes.configure(yscrollcommand=notes_scroll.set)
            notes.insert("1.0", release.notes)
            notes.configure(state="disabled")
            notes.pack(side="left", fill="both", expand=True)
            notes_scroll.pack(side="right", fill="y")

        ttk.Label(frame, wraplength=440, foreground="#666666",
                  text="Download opens the installer for this platform in your browser. "
                       "Install it over this copy to update.").pack(anchor="w", pady=(0, 12))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")

        def download():
            webbrowser.open(release.download_url())
            self.runner.note(f"Opened the download for version {release.version}.")
            win.destroy()

        def skip():
            self._update_settings = updates.save_settings(skipped_version=release.version)
            self.runner.note(f"Skipping version {release.version}.")
            win.destroy()

        ttk.Button(buttons, text="Download", command=download).pack(side="right")
        ttk.Button(buttons, text="Later", command=win.destroy).pack(side="right", padx=6)
        if silent:   # only the unprompted check offers to stop mentioning this version
            ttk.Button(buttons, text="Skip This Version", command=skip).pack(side="left")

        win.bind("<Escape>", lambda _event: win.destroy())
        win.update_idletasks()
        x = self.winfo_rootx() + max((self.winfo_width() - win.winfo_width()) // 2, 0)
        win.geometry(f"+{max(x, 0)}+{max(self.winfo_rooty() + 80, 0)}")
        win.grab_set()

    def on_close(self):
        if self.runner.busy:
            if not messagebox.askyesno("Quit", "A job is still running. Stop it and quit?"):
                return
            self.runner.stop()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
