#!/usr/bin/env python3
"""Cross-platform dependency resolver for the video tools in this folder.

Works on macOS, Linux and Windows. Missing tools are installed with whatever
package manager is available:

  macOS    Homebrew
  Linux    apt / dnf / yum / pacman / zypper  (via sudo when not root)
  Windows  winget / choco / scoop

yt-dlp is installed via pip when Python is present (most portable, and gets the
newest build), falling back to the package manager.

As a library:
    import deps
    missing = deps.ensure(["ffmpeg", "yt-dlp"])   # [] if all present/installed

As a CLI (handy for GUIs that want to stream the install into a log):
    python3 deps.py ffmpeg yt-dlp      # install any that are missing
    python3 deps.py --check ffmpeg     # only check; exit 1 if missing
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

# The executable to look for on PATH, per generic tool name.
PROBE = {"python3": "python3" if os.name != "nt" else "python"}

# Package id per (tool, manager). "default" covers the common case.
PKG_NAMES = {
    "ffmpeg":  {"default": "ffmpeg", "winget": "Gyan.FFmpeg"},
    "ffprobe": {"default": "ffmpeg", "winget": "Gyan.FFmpeg"},   # ships with ffmpeg
    "yt-dlp":  {"default": "yt-dlp", "winget": "yt-dlp.yt-dlp"},
    "python3": {"default": "python3", "brew": "python", "winget": "Python.Python.3.12",
                "choco": "python", "scoop": "python"},
}

HINTS = {
    "ffmpeg":  "install ffmpeg — https://ffmpeg.org/download.html",
    "ffprobe": "install ffmpeg (ffprobe ships with it) — https://ffmpeg.org/download.html",
    "yt-dlp":  'python3 -m pip install -U --pre "yt-dlp[default]"  '
               "— https://github.com/yt-dlp/yt-dlp#installation",
    "python3": "install Python 3 — https://www.python.org/downloads/",
}


# Official prebuilt binaries, for end users with no package manager. yt-dlp ships
# a single standalone binary; ffmpeg has reputable static builds per OS.
from pathlib import Path

YTDLP_URL = {
    "Darwin":  "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos",
    "Linux":   "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp",
    "Windows": "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
}


def managed_bin_dir():
    """Per-user dir where we drop downloaded tool binaries, kept on PATH."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "VideoTools" / "bin"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "VideoTools" / "bin"
    else:
        base = (Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
                / "video-tools" / "bin")
    base.mkdir(parents=True, exist_ok=True)
    return base


def _ensure_on_path():
    """Prepend the managed bin dir to PATH so which() and spawned tools see it."""
    d = str(managed_bin_dir())
    if d not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


_ensure_on_path()


def probe_name(tool):
    return PROBE.get(tool, tool)


def which(tool):
    return shutil.which(probe_name(tool))


def _fetch(url, dest, on_log):
    on_log(f"  -> downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "video-tools"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)


def _install_ytdlp_binary(on_log):
    url = YTDLP_URL.get(platform.system())
    if not url:
        return False
    bindir = managed_bin_dir()
    name = "yt-dlp.exe" if os.name == "nt" else "yt-dlp"
    tmp = bindir / (name + ".part")
    _fetch(url, tmp, on_log)
    tmp.replace(bindir / name)
    if os.name != "nt":
        (bindir / name).chmod(0o755)
    return which("yt-dlp") is not None


def _install_ffmpeg_binary(on_log):
    """Download a static ffmpeg+ffprobe into the managed bin dir, per OS/arch."""
    bindir = managed_bin_dir()
    system = platform.system()
    arch = platform.machine().lower()
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        if system == "Darwin":
            # evermeet.cx: ask its info API for the current zip URL (the old
            # /getrelease/ shortcut now 410s). Builds are x86_64; they run on
            # Apple Silicon under Rosetta 2.
            for tool in ("ffmpeg", "ffprobe"):
                info_url = f"https://evermeet.cx/ffmpeg/info/{tool}/release"
                req = urllib.request.Request(info_url, headers={"User-Agent": "video-tools"})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    zip_url = json.load(resp)["download"]["zip"]["url"]
                zpath = td / f"{tool}.zip"
                _fetch(zip_url, zpath, on_log)
                with zipfile.ZipFile(zpath) as zf:
                    zf.extractall(td)
                src = next(td.rglob(tool), None)
                if src and src.is_file():
                    shutil.move(str(src), str(bindir / tool))
                    (bindir / tool).chmod(0o755)
        elif system == "Linux":
            flavor = "arm64" if ("aarch64" in arch or "arm64" in arch) else "amd64"
            tpath = td / "ffmpeg.tar.xz"
            _fetch(f"https://johnvansickle.com/ffmpeg/releases/"
                   f"ffmpeg-release-{flavor}-static.tar.xz", tpath, on_log)
            with tarfile.open(tpath, "r:xz") as tf:
                tf.extractall(td)
            for tool in ("ffmpeg", "ffprobe"):
                found = next(td.rglob(tool), None)
                if found:
                    shutil.move(str(found), str(bindir / tool))
                    (bindir / tool).chmod(0o755)
        elif system == "Windows":
            zpath = td / "ffmpeg.zip"
            _fetch("https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/"
                   "ffmpeg-master-latest-win64-gpl.zip", zpath, on_log)
            with zipfile.ZipFile(zpath) as zf:
                zf.extractall(td)
            for tool in ("ffmpeg.exe", "ffprobe.exe"):
                found = next(td.rglob(tool), None)
                if found:
                    shutil.move(str(found), str(bindir / tool))
    return which("ffmpeg") is not None


def pkg_manager():
    """Return the name of the first available package manager, or None."""
    system = platform.system()
    if system == "Darwin":
        return "brew" if which("brew") else None
    if system == "Linux":
        for m in ("apt-get", "dnf", "yum", "pacman", "zypper"):
            if shutil.which(m):
                return m
        return None
    if system == "Windows":
        for m in ("winget", "choco", "scoop"):
            if shutil.which(m):
                return m
    return None


def _sudo():
    """['sudo'] on a non-root POSIX system that has sudo, else []."""
    if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() != 0 and shutil.which("sudo"):
        return ["sudo"]
    return []


def _run(cmd, on_log):
    on_log("  -> " + " ".join(cmd))
    try:
        return subprocess.run(cmd).returncode == 0
    except OSError as exc:
        on_log(f"     (could not run: {exc})")
        return False


def _manager_install_cmd(tool, mgr):
    names = PKG_NAMES.get(tool, {})
    pkg = names.get(mgr, names.get("default", tool))
    sudo = _sudo()
    return {
        "brew":    ["brew", "install", pkg],
        "apt-get": sudo + ["apt-get", "install", "-y", pkg],
        "dnf":     sudo + ["dnf", "install", "-y", pkg],
        "yum":     sudo + ["yum", "install", "-y", pkg],
        "pacman":  sudo + ["pacman", "-S", "--noconfirm", pkg],
        "zypper":  sudo + ["zypper", "install", "-y", pkg],
        "winget":  ["winget", "install", "-e", "--id", pkg, "--accept-package-agreements",
                    "--accept-source-agreements"],
        "choco":   ["choco", "install", "-y", pkg],
        "scoop":   ["scoop", "install", pkg],
    }.get(mgr)


def install(tool, on_log=print):
    """Try to install one tool. Return True on apparent success."""
    # yt-dlp: prefer pip (portable + newest) when Python is available.
    if tool == "yt-dlp" and shutil.which(probe_name("python3")):
        py = shutil.which(probe_name("python3"))
        if _run([py, "-m", "pip", "install", "-U", "--pre", "yt-dlp[default]"], on_log):
            return True
        on_log("     (system pip refused; retrying with --user)")
        if _run([py, "-m", "pip", "install", "--user", "-U", "--pre", "yt-dlp[default]"], on_log):
            return True

    mgr = pkg_manager()
    if mgr:
        if mgr == "apt-get":
            _run(_sudo() + ["apt-get", "update"], on_log)
        cmd = _manager_install_cmd(tool, mgr)
        if cmd and _run(cmd, on_log) and which(tool):
            return True

    # No package manager (typical end user), or it failed: fetch a prebuilt
    # binary from the project's official download into the managed bin dir.
    try:
        if tool == "yt-dlp":
            return _install_ytdlp_binary(on_log)
        if tool in ("ffmpeg", "ffprobe"):
            return _install_ffmpeg_binary(on_log)
    except Exception as exc:
        on_log(f"  (prebuilt-binary download failed: {exc})")
    return False


def ensure(tools, auto_install=True, on_log=print):
    """Ensure each tool is on PATH, installing missing ones. Return still-missing list.

    `tools` may be any mix of generic names (ffmpeg, ffprobe, yt-dlp, python3).
    Presence is re-checked per tool, so installing ffmpeg also satisfies ffprobe.
    """
    still = []
    for tool in tools:
        if which(tool):
            continue
        if not auto_install:
            still.append(tool)
            continue
        on_log(f"Dependency '{tool}' is missing — attempting to install it...")
        install(tool, on_log)
        if which(tool):
            on_log(f"Installed '{tool}'.")
        else:
            still.append(tool)
    return still


def hint(tool):
    return HINTS.get(tool, f"install {tool}")


def _main(argv):
    args = argv[1:]
    check_only = "--check" in args
    tools = [a for a in args if not a.startswith("-")]
    if not tools:
        print("usage: deps.py [--check] TOOL [TOOL ...]", file=sys.stderr)
        return 2
    missing = ensure(tools, auto_install=not check_only)
    if missing:
        for tool in missing:
            verb = "not installed" if check_only else "could not be installed automatically"
            print(f"Error: '{tool}' {verb}. {hint(tool)}", file=sys.stderr)
            if tool == "yt-dlp":
                print("  (if pip used --user, add its bin dir to PATH, e.g. "
                      "`python3 -m site --user-base`/bin)", file=sys.stderr)
        return 1
    print("All dependencies are present.")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
