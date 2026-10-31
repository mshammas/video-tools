#!/usr/bin/env python3
"""Update checking against the project's GitHub Releases.

The app is shipped as a frozen PyInstaller bundle (dmg / zip / tar.gz) built by
.github/workflows/build.yml, which publishes a GitHub Release per version tag.
So "is there a newer version?" is answered by asking the Releases API for the
latest tag and comparing it with appinfo.APP_VERSION.

We deliberately do NOT replace the running app in place: swapping out a signed
bundle while it executes is fragile and platform-specific. Instead we point the
user at the release page (or the installer for their OS) and let the normal
install path do the work.

Only the standard library is used, so this works in the frozen app too.

    python3 updates.py            # one-off check from the command line
"""

import json
import os
import platform
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import appinfo

API_LATEST = f"https://api.github.com/repos/{appinfo.APP_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{appinfo.APP_REPO}/releases/latest"

# Don't nag: at most one automatic check per day.
AUTO_CHECK_INTERVAL = 24 * 60 * 60

# Which release asset suits the machine we're running on (see packaging/build.py).
ASSET_SUFFIX = {"Darwin": ".dmg", "Windows": ".zip", "Linux": ".tar.gz"}


# --------------------------------------------------------------------------- #
# settings (auto-check preference, last check, skipped version)
# --------------------------------------------------------------------------- #

def config_dir():
    """Per-user config dir, matching the data dir layout in deps.py."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "VideoTools"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "VideoTools"
    else:
        base = (Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
                / "video-tools")
    base.mkdir(parents=True, exist_ok=True)
    return base


SETTINGS_FILE = None            # resolved lazily so importing never touches disk

DEFAULTS = {
    "auto_check": True,         # check once a day on startup
    "last_check": 0,            # epoch seconds of the last automatic check
    "skipped_version": "",      # version the user asked not to be told about
}


def _settings_path():
    global SETTINGS_FILE
    if SETTINGS_FILE is None:
        SETTINGS_FILE = config_dir() / "settings.json"
    return SETTINGS_FILE


def load_settings():
    try:
        with open(_settings_path(), "r", encoding="utf-8") as fh:
            stored = json.load(fh)
    except (OSError, ValueError):
        stored = {}
    settings = dict(DEFAULTS)
    if isinstance(stored, dict):
        settings.update({k: v for k, v in stored.items() if k in DEFAULTS})
    return settings


def save_settings(**changes):
    """Merge `changes` into the stored settings. Never raises: it's a preference."""
    settings = load_settings()
    settings.update({k: v for k, v in changes.items() if k in DEFAULTS})
    try:
        tmp = _settings_path().with_suffix(".json.part")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(settings, fh, indent=2)
        os.replace(tmp, _settings_path())
    except OSError:
        pass
    return settings


# --------------------------------------------------------------------------- #
# version comparison
# --------------------------------------------------------------------------- #

def parse_version(text):
    """'v1.2.3' / '1.2.3-beta' -> (1, 2, 3). Missing parts count as 0."""
    match = re.search(r"\d+(?:\.\d+)*", text or "")
    if not match:
        return ()
    parts = [int(p) for p in match.group(0).split(".")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(candidate, current=None):
    """True when `candidate` is a strictly higher version than `current`."""
    current = appinfo.APP_VERSION if current is None else current
    new, have = parse_version(candidate), parse_version(current)
    if not new or not have:
        return False
    return new > have


# --------------------------------------------------------------------------- #
# the check itself
# --------------------------------------------------------------------------- #

class Release:
    """The latest published release, and what it means for this install."""

    def __init__(self, payload):
        self.version = (payload.get("tag_name") or payload.get("name") or "").lstrip("vV")
        self.page_url = payload.get("html_url") or RELEASES_PAGE
        self.notes = (payload.get("body") or "").strip()
        self.published = (payload.get("published_at") or "")[:10]
        self.assets = [(a.get("name") or "", a.get("browser_download_url") or "")
                       for a in payload.get("assets") or []]

    @property
    def is_update(self):
        return is_newer(self.version)

    def download_url(self):
        """The installer for this OS, falling back to the release page."""
        suffix = ASSET_SUFFIX.get(platform.system())
        if suffix:
            for name, url in self.assets:
                if name.lower().endswith(suffix) and url:
                    return url
        return self.page_url


class CheckError(Exception):
    """The check could not be completed (offline, rate-limited, no releases)."""


def fetch_latest(timeout=10):
    """Ask GitHub for the latest release. Raises CheckError on any failure."""
    request = urllib.request.Request(
        API_LATEST,
        headers={"User-Agent": f"{appinfo.APP_SLUG}/{appinfo.APP_VERSION}",
                 "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise CheckError("No releases have been published yet.") from exc
        if exc.code in (403, 429):
            raise CheckError("GitHub is rate-limiting update checks. Try again later.") from exc
        raise CheckError(f"GitHub returned HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise CheckError(f"Could not reach GitHub ({exc.reason}).") from exc
    except (ValueError, OSError) as exc:
        raise CheckError(f"Unexpected reply from GitHub ({exc}).") from exc

    release = Release(payload)
    if not release.version:
        raise CheckError("The latest release has no version tag.")
    return release


def check_in_background(on_done, timeout=10):
    """Run fetch_latest off the UI thread.

    `on_done(release, error)` is called from the worker thread with exactly one
    of the two set, so callers must marshal back to the UI themselves (Tk:
    widget.after(0, ...)).
    """
    def worker():
        try:
            on_done(fetch_latest(timeout=timeout), None)
        except CheckError as exc:
            on_done(None, str(exc))
        except Exception as exc:                       # never kill the thread silently
            on_done(None, f"Update check failed ({exc}).")

    thread = threading.Thread(target=worker, daemon=True, name="update-check")
    thread.start()
    return thread


def auto_check_due(settings=None):
    """True when an automatic check is enabled and hasn't run in a day."""
    settings = load_settings() if settings is None else settings
    if not settings.get("auto_check", True):
        return False
    return (time.time() - float(settings.get("last_check") or 0)) >= AUTO_CHECK_INTERVAL


def is_skipped(version, settings=None):
    settings = load_settings() if settings is None else settings
    skipped = settings.get("skipped_version") or ""
    return bool(skipped) and parse_version(skipped) >= parse_version(version)


def _main():
    print(f"{appinfo.APP_NAME} {appinfo.APP_VERSION}")
    try:
        release = fetch_latest()
    except CheckError as exc:
        print(f"Update check failed: {exc}", file=sys.stderr)
        return 1
    if release.is_update:
        print(f"Update available: {release.version} ({release.published})")
        print(f"  {release.download_url()}")
    else:
        print(f"Up to date (latest release is {release.version}).")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
