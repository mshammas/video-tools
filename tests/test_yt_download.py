"""Tests for yt_download.py, pinned against the bash script it replaced.

The goldens under tests/golden/ were captured from yt-download.sh before it was
deleted, so these tests check the port still behaves the way the script did —
not merely that it behaves consistently with itself. tests/README.md explains
where each golden came from.

Run:  python3 -m unittest discover -s tests
"""

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import support
from cases import CASES, CHAPTER_CASES, PLAYLIST_URLS

import yt_download


def run_case(case):
    """Drive main() for one case with the tools faked out.

    Returns (exit code, normalised invocations, stderr text).
    """
    root = Path(tempfile.mkdtemp())
    try:
        argv, paths, out = support.materialise(case, root)
        fake = support.FakeSubprocess(out, size=case.size, write_path=case.write_path)
        saved = (yt_download.subprocess, yt_download.require, sys.argv)
        yt_download.subprocess = fake
        # Dependency resolution is deps.py's job and would try to install
        # yt-dlp on a machine that lacks it, so it is out of scope here.
        yt_download.require = lambda *a, **k: None
        sys.argv = ["yt_download.py", *argv]
        stderr = io.StringIO()
        try:
            with support.CapturedStdout(), contextlib.redirect_stderr(stderr):
                code = yt_download.main()
        except SystemExit as exc:
            # sys.exit("message") means exit 1 with that text on stderr.
            code = 1 if isinstance(exc.code, str) else exc.code
        finally:
            yt_download.subprocess, yt_download.require, sys.argv = saved
        return (0 if code is None else int(code),
                support.normalise(fake.calls, paths),
                stderr.getvalue())
    finally:
        shutil.rmtree(root, ignore_errors=True)


class InvocationTests(unittest.TestCase):
    """Every yt-dlp and ffmpeg command line, and the exit code, as pinned.

    The disk-space check runs for real against the temp directory: the ordinary
    cases claim a 1 KB download (always fits) and out-of-space claims a petabyte
    (never fits), so no patching of free-space reporting is needed.
    """

    def test_invocations_match_goldens(self):
        golden_dir = support.GOLDEN_DIR / "invocations"
        for case in CASES:
            with self.subTest(case=case.name):
                golden_file = golden_dir / f"{case.name}.txt"
                self.assertTrue(golden_file.is_file(),
                                f"no golden for {case.name}; see tests/README.md")
                code, lines, err = run_case(case)
                actual = support.golden_text(code, lines)
                expected = golden_file.read_text(encoding="utf-8")
                self.assertEqual(expected, actual,
                                 f"\n{case.name} diverged from yt-download.sh."
                                 f"\n--- its stderr ---\n{err}")

    def test_every_golden_has_a_case(self):
        golden_dir = support.GOLDEN_DIR / "invocations"
        on_disk = {p.stem for p in golden_dir.glob("*.txt")}
        named = {c.name for c in CASES}
        self.assertEqual(set(), on_disk - named, "golden with no case in cases.py")
        self.assertEqual(set(), named - on_disk, "case with no golden")


class ChapterTests(unittest.TestCase):
    """Chapter discovery, against the old script's embedded Python helper."""

    @staticmethod
    def as_tsv(chapters):
        return "".join(f"{c.start:.3f}\t{c.end:.3f}\t{c.title}\n" for c in chapters)

    def test_chapters_match_goldens(self):
        for name, info in CHAPTER_CASES.items():
            with self.subTest(case=name):
                golden = support.GOLDEN_DIR / "chapters" / f"{name}.tsv"
                self.assertTrue(golden.is_file(), f"no golden for {name}")
                self.assertEqual(golden.read_text(encoding="utf-8"),
                                 self.as_tsv(yt_download.chapters_from_info(info)))

    def test_titles_are_filesystem_safe(self):
        chapters = yt_download.chapters_from_info(
            CHAPTER_CASES["native-unsafe-title-chars"])
        self.assertEqual(1, len(chapters))
        for bad in '\\/:*?"<>|':
            self.assertNotIn(bad, chapters[0].title)

    def test_blank_title_becomes_untitled(self):
        chapters = yt_download.chapters_from_info(CHAPTER_CASES["native-empty-title"])
        self.assertEqual(["Untitled"], [c.title for c in chapters])


class PlaylistUrlTests(unittest.TestCase):
    """Which links count as a listing to expand, against the old bash function."""

    def test_verdicts_match_goldens(self):
        golden = (support.GOLDEN_DIR / "playlist_urls.txt").read_text(encoding="utf-8")
        expected = {}
        for line in golden.splitlines():
            verdict, _, url = line.partition("\t")
            expected[url] = verdict == "yes"
        self.assertEqual(sorted(expected), sorted(PLAYLIST_URLS),
                         "the golden and PLAYLIST_URLS have drifted apart")
        for url in PLAYLIST_URLS:
            with self.subTest(url=url):
                self.assertEqual(expected[url], yt_download.looks_like_playlist(url))

    def test_a_short_is_not_a_listing(self):
        # Regression: '/shorts/<id>' is one video. Treating it as a channel's
        # shorts tab would expand a single Short into a playlist lookup.
        self.assertFalse(
            yt_download.looks_like_playlist("https://www.youtube.com/shorts/abc123"))
        self.assertTrue(
            yt_download.looks_like_playlist("https://www.youtube.com/@someone/shorts"))


class UpdateTests(unittest.TestCase):
    """--update picks its method from how the yt-dlp on PATH was installed."""

    def setUp(self):
        self.calls = []
        self.tmp = Path(tempfile.mkdtemp())
        self._saved = (yt_download.subprocess, yt_download.shutil.which)

        class Recorder:
            @staticmethod
            def run(cmd, **kwargs):
                self.calls.append(list(cmd))
                return support.FakeCompletedProcess(0)

        yt_download.subprocess = Recorder

    def tearDown(self):
        yt_download.subprocess, yt_download.shutil.which = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def install(self, name, first_bytes):
        path = self.tmp / name
        path.write_bytes(first_bytes)
        yt_download.shutil.which = lambda tool, p=str(path): p
        return str(path)

    def update(self):
        with contextlib.redirect_stderr(io.StringIO()):
            return yt_download.update_ytdlp()

    def test_pip_install_via_env_shebang(self):
        self.install("yt-dlp", b"#!/usr/bin/env python3\nimport sys\n")
        self.update()
        self.assertEqual(
            ["/usr/bin/env", "python3", "-m", "pip", "install", "-U", "--pre",
             "yt-dlp[default]"], self.calls[0])

    def test_pip_install_via_absolute_shebang(self):
        self.install("yt-dlp", b"#!/opt/homebrew/opt/python@3.12/bin/python3.12\n")
        self.update()
        self.assertEqual(
            ["/opt/homebrew/opt/python@3.12/bin/python3.12", "-m", "pip", "install",
             "-U", "--pre", "yt-dlp[default]"], self.calls[0])

    def test_standalone_binary_self_updates(self):
        self.install("yt-dlp", b"\x7fELF\x02\x01\x01\x00")
        self.update()
        self.assertEqual(["yt-dlp", "-U", "--update-to", "nightly"], self.calls[0])

    def test_homebrew_is_chosen_by_path(self):
        yt_download.shutil.which = \
            lambda tool: "/opt/homebrew/Cellar/yt-dlp/2026.1.1/bin/yt-dlp"
        self.update()
        self.assertEqual(["brew", "upgrade", "yt-dlp"], self.calls[0])

    def test_nothing_to_update(self):
        yt_download.shutil.which = lambda tool: None
        self.assertEqual(1, self.update())
        self.assertEqual([], self.calls)


class StalenessTests(unittest.TestCase):
    """The offline freshness check reads yt-dlp's date-stamped version."""

    def test_version_age(self):
        import datetime
        today = datetime.date.today()
        fresh = f"{today.year}.{today.month:02d}.{today.day:02d}"
        self.assertEqual(0, yt_download.version_age_days(fresh))
        self.assertEqual(None, yt_download.version_age_days("not.a.version"))
        self.assertEqual(None, yt_download.version_age_days("2026.13.45"))
        self.assertEqual(None, yt_download.version_age_days("2026.10"))
        # A build id after the date is normal and must not confuse the parse.
        self.assertIsInstance(yt_download.version_age_days("2026.10.29.232701"), int)


class FormatSelectorTests(unittest.TestCase):
    """The format selector avoids YouTube's progressive formats."""

    def test_mp4_prefers_h264_and_aac(self):
        fmt = yt_download.build_format(None, remux_mp4=True)
        self.assertTrue(fmt.startswith("bv*[acodec=none][vcodec^=avc1]+"))
        self.assertIn("[acodec^=mp4a]", fmt)

    def test_height_cap_applies_to_every_alternative(self):
        fmt = yt_download.build_format("720", remux_mp4=True)
        self.assertEqual(len(fmt.split("/")), fmt.count("[height<=720]"))

    def test_progressive_is_only_the_last_resort(self):
        for remux in (True, False):
            fmt = yt_download.build_format("1080", remux_mp4=remux)
            alternatives = fmt.split("/")
            for alt in alternatives[:-1]:
                self.assertIn("acodec=none", alt, "a non-final alternative is progressive")
            self.assertTrue(alternatives[-1].startswith("b["))


class DiskSpaceTests(unittest.TestCase):
    """The pre-download check wants room for the file plus the merge's copy.

    The invocation goldens only cover 'obviously fits' and 'obviously does
    not', so the size of the safety margin is pinned here instead, with free
    space reported rather than measured.
    """

    EST = 1_000_000

    def check(self, free, estimate=None):
        estimate = str(self.EST) if estimate is None else estimate
        usage = type("Usage", (), {"free": free})()

        class Recorder:
            @staticmethod
            def run(cmd, **kwargs):
                return support.FakeCompletedProcess(0, estimate + "\n")

        saved = (yt_download.subprocess, yt_download.shutil.disk_usage)
        yt_download.subprocess = Recorder
        yt_download.shutil.disk_usage = lambda path: usage
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                return yt_download.check_disk_space("url", "fmt", Path("."))
        finally:
            yt_download.subprocess, yt_download.shutil.disk_usage = saved

    def test_room_for_two_copies_passes(self):
        self.assertEqual(0, self.check(free=self.EST * 2))
        self.assertEqual(0, self.check(free=self.EST * 3))

    def test_room_for_only_one_copy_fails(self):
        # The download itself would fit; the merge's second copy would not.
        self.assertEqual(yt_download.EXIT_NO_SPACE, self.check(free=self.EST))
        self.assertEqual(yt_download.EXIT_NO_SPACE,
                         self.check(free=int(self.EST * 1.5)))

    def test_margin_is_exactly_two(self):
        self.assertEqual(2, yt_download.SPACE_SAFETY)
        self.assertEqual(yt_download.EXIT_NO_SPACE, self.check(free=self.EST * 2 - 1))

    def test_unknown_size_skips_the_check(self):
        for estimate in ("", "NA", "0", "not-a-number"):
            with self.subTest(estimate=estimate):
                self.assertEqual(0, self.check(free=1, estimate=estimate))


class CliTests(unittest.TestCase):
    """Argument handling that is not covered by an invocation golden."""

    def test_no_url_is_a_usage_error(self):
        # Deliberately 2, not the old script's 1: argparse's convention, and
        # what clip_video.py / mix_videos.py already do. Noted in the README.
        saved = sys.argv
        sys.argv = ["yt_download.py"]
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    yt_download.main()
        finally:
            sys.argv = saved
        self.assertEqual(2, caught.exception.code)

    def test_container_flags_are_mutually_exclusive(self):
        # Another deliberate change: the old script let the last flag win.
        saved = sys.argv
        sys.argv = ["yt_download.py", "--mkv", "--mp4", "https://youtu.be/x"]
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    yt_download.main()
        finally:
            sys.argv = saved
        self.assertEqual(2, caught.exception.code)

    def test_batch_file_ignores_blanks_and_comments(self):
        root = Path(tempfile.mkdtemp())
        try:
            path = root / "batch.txt"
            path.write_text(support.BATCH_FILE_BODY, encoding="utf-8")
            self.assertEqual(
                ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://youtu.be/abc"],
                yt_download.read_batch_file(path))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_batch_file_tolerates_crlf(self):
        root = Path(tempfile.mkdtemp())
        try:
            path = root / "batch.txt"
            path.write_bytes(b"https://youtu.be/a\r\nhttps://youtu.be/b\r\n")
            self.assertEqual(["https://youtu.be/a", "https://youtu.be/b"],
                             yt_download.read_batch_file(path))
        finally:
            shutil.rmtree(root, ignore_errors=True)


class HumanSizeTests(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual("512.0 B", yt_download.human(512))
        self.assertEqual("1.0 KB", yt_download.human(1024))
        self.assertEqual("1.5 MB", yt_download.human(1024 * 1024 * 1.5))
        self.assertEqual("2.0 GB", yt_download.human(1024 ** 3 * 2))
        self.assertEqual("1.0 PB", yt_download.human(1024 ** 5))


if __name__ == "__main__":
    unittest.main()
