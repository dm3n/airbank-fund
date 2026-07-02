import unittest

import _isolate  # noqa: F401  (must precede airbank imports)

from airbank import analysts
from airbank.dashboard import Terminal, clip, pad, vlen


class RenderHelperTests(unittest.TestCase):
    def test_vlen_ignores_ansi(self):
        self.assertEqual(vlen("\033[32m+2.4%\033[0m"), 5)

    def test_pad_to_visible_width(self):
        padded = pad("\033[1mAB\033[0m", 6)
        self.assertEqual(vlen(padded), 6)

    def test_clip_respects_visible_width(self):
        clipped = clip("\033[36m" + "X" * 50 + "\033[0m", 10)
        self.assertLessEqual(vlen(clipped), 10)

    def test_clip_leaves_short_strings_alone(self):
        self.assertEqual(clip("short", 20), "short")


class FrameTests(unittest.TestCase):
    def test_build_produces_full_frame(self):
        term = Terminal()
        rows = term.build(100, 30)
        self.assertEqual(len(rows), 30)
        for row in rows:
            self.assertLessEqual(vlen(row), 101)

    def test_deploy_mode_footer_lists_roster(self):
        term = Terminal()
        term.mode = "deploy"
        foot = vlen(term.build(120, 30)[-1])
        self.assertGreater(foot, 10)

    def test_quit_key(self):
        term = Terminal()
        self.assertFalse(term.handle("q"))
        self.assertTrue(term.handle("x"))

    def test_deploy_keys(self):
        term = Terminal()
        self.assertTrue(term.handle("d"))
        self.assertEqual(term.mode, "deploy")
        self.assertTrue(term.handle("\x1b"))
        self.assertEqual(term.mode, "main")


class AnalystDeskTests(unittest.TestCase):
    def test_roster_shape(self):
        self.assertIn("premarket", analysts.ROSTER)
        for spec in analysts.ROSTER.values():
            self.assertTrue(spec["title"] and spec["desc"] and spec["brief"])

    def test_deploy_writes_report_with_injected_runner(self):
        path, headline = analysts.deploy(
            "risk", runner=lambda prompt: "# Test Risk Memo\n\nAll clear.")
        try:
            self.assertTrue(path.exists())
            self.assertEqual(headline, "Test Risk Memo")
            self.assertIn("Risk Memo", path.read_text())
        finally:
            path.unlink(missing_ok=True)

    def test_deploy_refuses_empty_report(self):
        with self.assertRaises(RuntimeError):
            analysts.deploy("risk", runner=lambda prompt: "   ")


if __name__ == "__main__":
    unittest.main()
