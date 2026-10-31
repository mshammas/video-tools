#!/usr/bin/env python3
"""Shared application metadata for the Video Tools GUI.

One place for the name/version/identifier used by the window title, the icon
generator, and the packaging/build config so they never drift apart.
"""

APP_NAME = "Video Tools"
APP_SLUG = "video-tools"                     # file- and command-friendly name
APP_VERSION = "1.0.6"
APP_IDENTIFIER = "com.shammas.videotools"    # macOS bundle id / general app id
APP_AUTHOR = "Shammas"
APP_DESCRIPTION = "Clip, download and mix videos."
APP_REPO = "mshammas/video-tools"            # GitHub owner/name, used for update checks
APP_WEBSITE = f"https://github.com/{APP_REPO}"

# Windows version-info wants a 4-part tuple.
def version_tuple():
    parts = [int(p) for p in APP_VERSION.split(".")]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])
