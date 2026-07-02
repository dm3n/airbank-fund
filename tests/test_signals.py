import unittest

from airbank import signals
from airbank.analyst import _parse


def trending_up(n=120):
    return [100 * (1.01 ** i) for i in range(n)]


def trending_down(n=120):
    return [100 * (0.99 ** i) for i in range(n)]


class SignalTests(unittest.TestCase):
    def test_momentum_long_in_uptrend(self):
        sig = signals.momentum_signal(trending_up())
        self.assertIsNotNone(sig)
        self.assertEqual(sig["side"], "buy")

    def test_momentum_flat_in_downtrend(self):
        self.assertIsNone(signals.momentum_signal(trending_down()))

    def test_momentum_no_rebuy_while_holding(self):
        self.assertIsNone(signals.momentum_signal(trending_up(), holding=True))

    def test_momentum_exit_when_trend_breaks(self):
        sig = signals.momentum_signal(trending_down(), holding=True)
        self.assertIsNotNone(sig)
        self.assertEqual(sig["side"], "sell")

    def test_momentum_needs_history(self):
        self.assertIsNone(signals.momentum_signal(trending_up(30)))

    def test_meanrev_entry_on_crash(self):
        closes = [100.0] * 40 + [80.0]  # sharp drop -> deep negative z
        sig = signals.meanrev_signal(closes, holding=False)
        self.assertIsNotNone(sig)
        self.assertEqual(sig["side"], "buy")

    def test_meanrev_exit_on_reversion(self):
        closes = [100.0] * 19 + [101.0]  # z >= 0
        sig = signals.meanrev_signal(closes, holding=True)
        self.assertIsNotNone(sig)
        self.assertEqual(sig["side"], "sell")

    def test_meanrev_no_entry_at_mean(self):
        self.assertIsNone(signals.meanrev_signal([100.0] * 40, holding=False))

    def test_vol_filter_blocks_entries(self):
        calm = [100 + (i % 2) * 0.1 for i in range(300)]
        wild = calm[:-20] + [calm[-21] * (1 + (0.15 if i % 2 else -0.15)) for i in range(20)]
        candidates = signals.generate_candidates("BTC/USD", "crypto", wild)
        self.assertTrue(all(c["side"] != "buy" for c in candidates))


class AnalystParseTests(unittest.TestCase):
    def test_valid_verdict(self):
        v = _parse('{"verdict": "proceed", "conviction": 0.8, "thesis": "x" }')
        self.assertEqual(v["verdict"], "proceed")
        self.assertAlmostEqual(v["conviction"], 0.8)

    def test_conviction_clamped(self):
        v = _parse('{"verdict": "proceed", "conviction": 5, "thesis": "x"}')
        self.assertEqual(v["conviction"], 1.0)

    def test_garbage_dropped(self):
        self.assertIsNone(_parse("the market looks bullish"))
        self.assertIsNone(_parse('{"verdict": "maybe", "conviction": 0.5}'))

    def test_json_in_prose_extracted(self):
        v = _parse('Sure! {"verdict": "veto", "conviction": 0.2, "thesis": "crowded"} done')
        self.assertEqual(v["verdict"], "veto")


if __name__ == "__main__":
    unittest.main()
