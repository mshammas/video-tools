#!/usr/bin/env python3
"""Build a desktop installer for the CURRENT operating system with PyInstaller.

There is no cross-compiling: run this on macOS to get the .dmg, on Windows to
get the .exe/.zip, on Linux to get the tarball. The GitHub Actions workflow in
.github/workflows/build.yml runs it on all three and uploads the results.

    python3 packaging/build.py

Outputs land in  dist/ :
    macOS    <app>.app        and  <slug>-<version>-macos.dmg
    Windows  <slug>.exe       and  <slug>-<version>-windows.zip
    Linux    <slug>           and  <slug>-<version>-linux.tar.gz
"""

import importlib
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import appinfo  # noqa: E402

SYSTEM = platform.system()           # 'Darwin', 'Linux' or 'Windows'
DIST = ROOT / "dist"
BUNDLED = ["clip_video.py", "mix_videos.py", "deps.py", "appinfo.py", "yt-download.sh"]


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def ensure_build_deps():
    for module, package in [("PyInstaller", "pyinstaller"), ("PIL", "pillow")]:
        try:
            importlib.import_module(module)
        except ImportError:
            run([sys.executable, "-m", "pip", "install", package])


def add_data_args():
    """--add-data uses ';' as the path separator on Windows, ':' elsewhere."""
    sep = ";" if SYSTEM == "Windows" else ":"
    args = []
    for name in BUNDLED:
        args += ["--add-data", f"{ROOT / name}{sep}."]
    args += ["--add-data", f"{ROOT / 'assets' / 'icon.png'}{sep}assets"]
    return args


def icon_path():
    if SYSTEM == "Darwin":
        return ROOT / "assets" / "icon.icns"
    if SYSTEM == "Windows":
        return ROOT / "assets" / "icon.ico"
    return None   # PyInstaller has no exe-icon concept on Linux; icon ships beside it


def build_with_pyinstaller():
    run([sys.executable, str(ROOT / "assets" / "make_icon.py")])   # ensure icons exist
    name = appinfo.APP_NAME if SYSTEM == "Darwin" else appinfo.APP_SLUG
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--windowed", "--name", name]
    cmd += ["--onedir"] if SYSTEM == "Darwin" else ["--onefile"]
    icon = icon_path()
    if icon:
        cmd += ["--icon", str(icon)]
    for hidden in ("clip_video", "mix_videos", "deps", "appinfo"):
        cmd += ["--hidden-import", hidden]
    cmd += add_data_args()
    if SYSTEM == "Darwin":
        cmd += ["--osx-bundle-identifier", appinfo.APP_IDENTIFIER]
    cmd += [str(ROOT / "video_tools_gui.py")]
    run(cmd, cwd=str(ROOT))


def package_macos():
    app = DIST / f"{appinfo.APP_NAME}.app"
    plist = app / "Contents" / "Info.plist"
    # Stamp the version so Finder/Get-Info shows it.
    if shutil.which("/usr/libexec/PlistBuddy"):
        for key in ("CFBundleShortVersionString", "CFBundleVersion"):
            subprocess.run(["/usr/libexec/PlistBuddy", "-c",
                            f"Set :{key} {appinfo.APP_VERSION}", str(plist)],
                           capture_output=True)
    # Editing Info.plist invalidates the ad-hoc signature PyInstaller applied,
    # and macOS then reports the app as "damaged and can't be opened". Re-sign
    # so the bundle verifies again; it is still only ad-hoc, so Gatekeeper
    # falls back to the milder "Apple could not verify" prompt. That prompt is
    # cleared by removing the quarantine attribute or by Open Anyway in System
    # Settings — on macOS 15+ right-click > Open no longer offers a bypass.
    run(["codesign", "--force", "--deep", "--sign", "-", str(app)])
    run(["codesign", "--verify", "--deep", "--strict", str(app)])
    # Lay the disk image out as a drag-and-drop installer: the app next to a
    # symlink to /Applications, so opening the dmg shows both and the app is
    # installed by dragging one onto the other. ditto rather than copytree,
    # because it keeps the extended attributes the signature is stored in.
    stage = DIST / "dmg"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    run(["ditto", str(app), str(stage / app.name)])
    (stage / "Applications").symlink_to("/Applications")
    # The copy is what ships, so verify that one rather than the original.
    run(["codesign", "--verify", "--deep", "--strict", str(stage / app.name)])

    dmg = DIST / f"{appinfo.APP_SLUG}-{appinfo.APP_VERSION}-macos.dmg"
    if dmg.exists():
        dmg.unlink()
    run(["hdiutil", "create", "-volname", appinfo.APP_NAME, "-srcfolder", str(stage),
         "-ov", "-format", "UDZO", str(dmg)])
    shutil.rmtree(stage)
    return dmg


def package_windows():
    exe = DIST / f"{appinfo.APP_SLUG}.exe"
    base = DIST / f"{appinfo.APP_SLUG}-{appinfo.APP_VERSION}-windows"
    staging = DIST / "_win"
    staging.mkdir(parents=True, exist_ok=True)
    shutil.copy2(exe, staging / exe.name)
    archive = shutil.make_archive(str(base), "zip", str(staging))
    shutil.rmtree(staging)
    return Path(archive)


def package_linux():
    binary = DIST / appinfo.APP_SLUG
    binary.chmod(0o755)
    stage = DIST / f"{appinfo.APP_SLUG}-{appinfo.APP_VERSION}-linux"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    shutil.copy2(binary, stage / appinfo.APP_SLUG)
    shutil.copy2(ROOT / "packaging" / "linux" / "video-tools.desktop", stage)
    shutil.copy2(ROOT / "assets" / "icon_256.png", stage / f"{appinfo.APP_SLUG}.png")
    (stage / "INSTALL.txt").write_text(
        f"{appinfo.APP_NAME} {appinfo.APP_VERSION}\n\n"
        f"Run ./{appinfo.APP_SLUG} directly, or install for the menu:\n"
        f"  install -Dm755 {appinfo.APP_SLUG} ~/.local/bin/{appinfo.APP_SLUG}\n"
        f"  install -Dm644 {appinfo.APP_SLUG}.png "
        f"~/.local/share/icons/hicolor/256x256/apps/{appinfo.APP_SLUG}.png\n"
        f"  install -Dm644 video-tools.desktop "
        f"~/.local/share/applications/video-tools.desktop\n")
    archive = shutil.make_archive(str(stage), "gztar", str(DIST), stage.name)
    shutil.rmtree(stage)
    return Path(archive)


def main():
    ensure_build_deps()
    build_with_pyinstaller()
    artifact = {"Darwin": package_macos, "Windows": package_windows,
                "Linux": package_linux}.get(SYSTEM, lambda: None)()
    print("\nBuilt installer:", artifact or "(unknown OS — see dist/)")


if __name__ == "__main__":
    main()
