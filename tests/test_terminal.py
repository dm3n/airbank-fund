import unittest

import _isolate  # noqa: F401  (must precede airbank imports)

from airbank import analysts, chat
from airbank.dashboard import Terminal, clip, pad, vlen


class RenderHelperTests(unittest.TestCase):
    def test_vlen_ignores_ansi(self):
        self.assertEqual(vlen("\033[32m+2.4%\033[0m"), 5)

    def test_pad_to_visible_width(self):
        self.assertEqual(vlen(pad("\033[1mAB\033[0m", 6)), 6)

    def test_clip_respects_visible_width(self):
        self.assertLessEqual(vlen(clip("\033[36m" + "X" * 50 + "\033[0m", 10)), 10)


class FrameTests(unittest.TestCase):
    def test_dash_frame_full_and_width_safe(self):
        term = Terminal()
        rows = term.build(100, 30)
        self.assertEqual(len(rows), 30)
        for row in rows:
            self.assertLessEqual(vlen(row), 101)

    def test_chat_frame_full_and_width_safe(self):
        term = Terminal()
        term.view = "chat"
        term.transcript = [("user", "how are we positioned?"),
                           ("assistant", "flat — " + "x" * 300),
                           ("system", "cycle done")]
        term.reveal = 10**6
        rows = term.build(90, 28)
        self.assertEqual(len(rows), 28)
        for row in rows:
            self.assertLessEqual(vlen(row), 91)


class InputTests(unittest.TestCase):
    def test_typing_goes_to_chat_bar(self):
        term = Terminal()
        for ch in "pnl?":
            self.assertTrue(term.handle(ch))
        self.assertEqual(term.input, "pnl?")
        term.handle("\x7f")
        self.assertEqual(term.input, "pnl")

    def test_ctrl_c_quits(self):
        self.assertFalse(Terminal().handle("\x03"))

    def test_esc_flips_views_with_animation(self):
        term = Terminal()
        self.assertTrue(term.handle("\x1b"))
        self.assertEqual(term.view, "hybrid")
        self.assertTrue(term.switch_anim)
        term.switch_anim = False
        term.handle("\x1b")
        self.assertEqual(term.view, "dash")

    def test_slash_quit(self):
        term = Terminal()
        term.input = "/quit"
        self.assertFalse(term.handle("\r"))

    def test_slash_deploy_needs_valid_name(self):
        term = Terminal()
        term.input = "/deploy nobody"
        self.assertTrue(term.handle("\r"))
        self.assertEqual(term.busy, "")

    def test_message_switches_to_chat_view(self):
        term = Terminal()
        term.ask = lambda m: term.transcript.append(("user", m))  # no thread/claude
        term.input = "hello desk"
        term.handle("\r")
        self.assertEqual(term.transcript[-1], ("user", "hello desk"))


class ChatEngineTests(unittest.TestCase):
    def test_action_parsed_and_stripped(self):
        reply, action = chat.respond(
            "deploy premarket", [],
            runner=lambda p: "On it.\nACTION: deploy premarket")
        self.assertEqual(reply, "On it.")
        self.assertEqual(action, "deploy premarket")

    def test_no_action_when_absent(self):
        reply, action = chat.respond("hi", [], runner=lambda p: "We're flat.")
        self.assertEqual(reply, "We're flat.")
        self.assertIsNone(action)

    def test_runner_failure_is_a_reply_not_a_crash(self):
        def boom(prompt):
            raise RuntimeError("no tty")
        reply, action = chat.respond("hi", [], runner=boom)
        self.assertIn("desk link is down", reply)
        self.assertIsNone(action)

    def test_history_flows_into_prompt(self):
        seen = {}
        chat.respond("second", [("user", "first"), ("assistant", "reply one")],
                     runner=lambda p: seen.setdefault("p", p) or "ok")
        self.assertIn("User: first", seen["p"])
        self.assertIn("Assistant: reply one", seen["p"])


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
        finally:
            path.unlink(missing_ok=True)

    def test_deploy_refuses_empty_report(self):
        with self.assertRaises(RuntimeError):
            analysts.deploy("risk", runner=lambda prompt: "   ")


if __name__ == "__main__":
    unittest.main()
