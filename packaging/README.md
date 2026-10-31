# Building Video Tools installers

The GUI (`video_tools_gui.py`) is packaged into a native app per OS with
PyInstaller. **There is no cross-compiling** — each OS's installer must be built
on that OS (or on a CI runner of that OS).

## Build locally (on the target OS)

```bash
python3 packaging/build.py
```

Outputs into `dist/`:

| OS | Produces |
|----|----------|
| macOS   | `Video Tools.app` + `video-tools-<ver>-macos.dmg` |
| Windows | `video-tools.exe` + `video-tools-<ver>-windows.zip` |
| Linux   | `video-tools` binary + `video-tools-<ver>-linux.tar.gz` |

`build.py` installs PyInstaller/Pillow if missing and regenerates the icons
from `assets/make_icon.py` first.

## Build all three (GitHub Actions)

`.github/workflows/build.yml` builds macOS, Windows and Linux on their native
runners and uploads each as a downloadable artifact. Trigger it manually
("Run workflow") or by pushing a tag like `v1.0.0`. This needs the repo pushed
to GitHub.

## App metadata & icon

- Edit name/version/identifier in [`appinfo.py`](../appinfo.py).
- Restyle the icon in [`assets/make_icon.py`](../assets/make_icon.py), then
  `python3 assets/make_icon.py` (build.py also does this).

## Known limitations for public distribution

1. **ffmpeg / yt-dlp are not bundled.** The app resolves them at runtime via
   `deps.py` (pip / Homebrew / apt / winget …). Developers have those; a typical
   end user may not. For a frictionless website download, bundle the binaries or
   add a first-run auto-download. *(Not yet done — ask to add it.)*
2. **Code signing / notarization.** The macOS `.app` is only ad-hoc signed and
   the Windows `.exe` is unsigned, so Gatekeeper / SmartScreen will warn users.
   On macOS 15+ there is no right-click → Open bypass. Note that clearing the
   flag on the *installed* app does not work: since macOS 13, App Management
   protects bundles in `/Applications`, so `xattr -dr com.apple.quarantine
   "/Applications/Video Tools.app"` fails with `Operation not permitted` on
   every file, `sudo` included. The user clears it on the download instead —
   `xattr -d com.apple.quarantine ~/Downloads/video-tools-*-macos.dmg` before
   dragging the app across — or uses **Open Anyway** in System Settings →
   Privacy & Security. Shipping without warnings needs an Apple Developer ID
   (notarization) and a Windows code-signing certificate.
3. **Download tab needs bash on Windows.** `yt-download.sh` is a bash script;
   the Download tab requires WSL or Git Bash on Windows. The Clip and Mix tabs
   are pure Python and need no bash.
