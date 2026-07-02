import unittest
from unittest import mock

import _isolate  # noqa: F401  (must precede airbank imports)

from airbank import analysts, premarket

GOOD_REPORT = """# Pre-Market Watchlist — test
## Market sentiment
risk-on, barely.
## Overnight & pre-market tape
BTC/USD +0.2% overnight.
## Watchlist
NVDA — momentum (ELIGIBLE): trigger 200.00, invalidation 190.00.
## Catalysts & news
none that matter.
## Falsifiers
SPY losing 740 flips me defensive.
""" + "x" * 200

GOOD_REVIEW = """### Where I agree
the tape read.
### Where I disagree
nothing material.
### Errors found
none
### What it missed
nothing."""


class TestBuild(unittest.TestCase):
    def test_merges_both_brains(self):
        prompts = {}

        def runner(p):
            prompts["runner"] = p
            return GOOD_REPORT

        def reviewer(p):
            prompts["reviewer"] = p
            return GOOD_REVIEW

        merged = premarket.build(runner=runner, reviewer=reviewer,
                                 dossier="DOSSIER-SENTINEL")
        self.assertIn("## Market sentiment", merged)
        self.assertIn("## Second brain — injected reviewer", merged)
        self.assertIn("### Where I disagree", merged)
        # both brains saw the same dossier and the trader's rules
        self.assertIn("DOSSIER-SENTINEL", prompts["runner"])
        self.assertIn("DOSSIER-SENTINEL", prompts["reviewer"])
        self.assertIn("Momentum (trend-join long)", prompts["runner"])
        # the reviewer saw brain 1's report, not a summary
        self.assertIn("trigger 200.00", prompts["reviewer"])
        # clean report -> no desk-check warnings
        self.assertNotIn("> desk check:", merged)

    def test_empty_brain1_raises(self):
        with self.assertRaises(RuntimeError):
            premarket.build(runner=lambda p: "  ", reviewer=lambda p: GOOD_REVIEW,
                            dossier="d")

    def test_desk_check_warns_on_malformed_report(self):
        merged = premarket.build(runner=lambda p: "not a real report",
                                 reviewer=lambda p: "meh", dossier="d")
        self.assertIn("> desk check: required section missing: Watchlist", merged)

    def test_rules_file_created_and_read(self):
        premarket.RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
        if premarket.RULES_FILE.exists():
            premarket.RULES_FILE.unlink()
        premarket.rules()
        self.assertTrue(premarket.RULES_FILE.exists())
        premarket.RULES_FILE.write_text("# my custom edge\n")
        seen = {}
        premarket.build(runner=lambda p: seen.update(p=p) or GOOD_REPORT,
                        reviewer=lambda p: GOOD_REVIEW, dossier="d")
        self.assertIn("# my custom edge", seen["p"])
        premarket.RULES_FILE.unlink()


class TestScreen(unittest.TestCase):
    def test_screen_row_uses_fund_strategy_math(self):
        closes = [100 + i for i in range(130)]  # clean uptrend
        row = premarket._screen_row("NVDA", "equity", closes, holding=False)
        self.assertEqual(row["trend"], "up")
        self.assertTrue(any(s.startswith("momentum:buy") for s in row["signals_now"]))
        self.assertNotIn("meanrev:buy", " ".join(row["signals_now"]))

    def test_holding_flips_to_exit_logic(self):
        closes = [100 + i for i in range(130)]
        row = premarket._screen_row("NVDA", "equity", closes, holding=True)
        self.assertFalse(any(":buy" in s for s in row["signals_now"]))  # never re-buys
        self.assertTrue(row["holding"])


class TestDeployRouting(unittest.TestCase):
    def test_premarket_routes_through_pipeline_and_delivers(self):
        with mock.patch.object(premarket, "build",
                               return_value=GOOD_REPORT) as build, \
             mock.patch.object(premarket, "deliver") as deliver:
            path, headline = analysts.deploy("premarket", runner=lambda p: "unused")
        build.assert_called_once()
        deliver.assert_called_once()
        self.assertTrue(path.exists())
        self.assertIn("Pre-Market Watchlist", headline)
        path.unlink()


if __name__ == "__main__":
    unittest.main()
