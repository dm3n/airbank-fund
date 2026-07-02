import unittest

import _isolate  # noqa: F401  (must precede airbank imports)

from airbank import ui
from airbank.dashboard import Terminal


class SuggestionTests(unittest.TestCase):
    def test_slash_alone_lists_all(self):
        term = Terminal()
        term.input = "/"
        self.assertEqual(len(term.suggestions()), 7)

    def test_prefix_narrows(self):
        term = Terminal()
        term.input = "/de"
        matches = term.suggestions()
        self.assertEqual([m[0] for m in matches], ["/deploy"])

    def test_deploy_continues_into_analysts(self):
        term = Terminal()
        term.input = "/deploy pre"
        matches = term.suggestions()
        self.assertEqual([m[0] for m in matches], ["/deploy premarket"])

    def test_no_suggestions_for_plain_text(self):
        term = Terminal()
        term.input = "how is the book"
        self.assertEqual(term.suggestions(), [])

    def test_tab_completes_selected(self):
        term = Terminal()
        term.input = "/ba"
        term.handle("\t")
        self.assertEqual(term.input, "/backtest")

    def test_tab_on_deploy_leaves_room_for_analyst(self):
        term = Terminal()
        term.input = "/dep"
        term.handle("\t")
        self.assertEqual(term.input, "/deploy ")
        self.assertTrue(term.suggestions())      # analyst menu now open

    def test_arrows_move_selection(self):
        term = Terminal()
        term.input = "/"
        term.handle("DOWN")
        self.assertEqual(term.sugg_idx, 1)
        term.handle("UP")
        self.assertEqual(term.sugg_idx, 0)

    def test_enter_runs_highlighted_pick(self):
        term = Terminal()
        term.input = "/qu"
        self.assertFalse(term.handle("\r"))      # completes to /quit and runs

    def test_esc_dismisses_menu_before_view_flip(self):
        term = Terminal()
        term.input = "/de"
        term.handle("\x1b")
        self.assertEqual(term.input, "")
        self.assertEqual(term.view, "dash")      # view untouched


class ProfileTests(unittest.TestCase):
    def test_rich_styles_code_and_links(self):
        import sys
        if not sys.stdout.isatty():              # rich() is a no-op when piped
            self.assertEqual(ui.rich("`BTC` at https://x.co"), "`BTC` at https://x.co")
        self.assertIn("breath", dir(ui))
        self.assertEqual(len(ui.BREATH_SHADES), 12)

    def test_breath_cycles_shades(self):
        seen = {ui.BREATH_SHADES[s % len(ui.BREATH_SHADES)] for s in range(24)}
        self.assertGreater(len(seen), 4)

    def test_themes_are_gone(self):
        self.assertFalse(hasattr(ui, "THEMES"))
        self.assertFalse(hasattr(ui, "set_theme"))


if __name__ == "__main__":
    unittest.main()
