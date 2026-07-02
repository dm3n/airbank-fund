import unittest

import _isolate  # noqa: F401  (must precede airbank imports)

from airbank import mirror
from airbank.brokers import MockBroker


def price_fn(prices):
    return lambda symbol, asset_class: prices[symbol]


class TargetWeightTests(unittest.TestCase):
    def test_qty_holdings_value_weighted(self):
        holds = [{"symbol": "BTC/USD", "asset_class": "crypto", "qty": 1.0},
                 {"symbol": "ETH/USD", "asset_class": "crypto", "qty": 10.0}]
        weights, _ = mirror.target_weights(
            holds, price_fn({"BTC/USD": 60000.0, "ETH/USD": 2000.0}))
        self.assertAlmostEqual(weights[("BTC/USD", "crypto")], 0.75)
        self.assertAlmostEqual(weights[("ETH/USD", "crypto")], 0.25)

    def test_explicit_weights_win_and_normalize(self):
        holds = [{"symbol": "AAPL", "asset_class": "equity", "weight": 0.8},
                 {"symbol": "SPY", "asset_class": "equity", "weight": 0.6}]
        weights, _ = mirror.target_weights(holds, price_fn({}))
        self.assertAlmostEqual(sum(weights.values()), 1.0)

    def test_mixed_weight_and_qty(self):
        holds = [{"symbol": "AAPL", "asset_class": "equity", "weight": 0.5},
                 {"symbol": "BTC/USD", "asset_class": "crypto", "qty": 1.0}]
        weights, _ = mirror.target_weights(holds, price_fn({"BTC/USD": 60000.0}))
        self.assertAlmostEqual(weights[("AAPL", "equity")], 0.5)
        self.assertAlmostEqual(weights[("BTC/USD", "crypto")], 0.5)


class SyncTests(unittest.TestCase):
    def setUp(self):
        import airbank.config as config
        self._saved = config.ACCOUNT
        config.ACCOUNT = {"type": "mirror",
                          "mirror": {"source": "file"}, "starting_cash": 100000.0}
        mirror.MIRROR_FILE.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import airbank.config as config
        config.ACCOUNT = self._saved
        mirror.MIRROR_FILE.unlink(missing_ok=True)

    def _write(self, holdings):
        import json
        mirror.MIRROR_FILE.write_text(json.dumps({"holdings": holdings}))

    def test_first_sync_builds_the_book(self):
        self._write([{"symbol": "BTC/USD", "asset_class": "crypto", "weight": 0.6},
                     {"symbol": "AAPL", "asset_class": "equity", "weight": 0.4}])
        state, cycle = {}, {}
        broker = MockBroker(state)
        prices = {"BTC/USD": 50000.0, "AAPL": 200.0}
        trades = mirror.sync(broker, cycle, price_fn=price_fn(prices))
        self.assertEqual(trades, 2)
        book = state["mock"]["positions"]
        self.assertAlmostEqual(book["BTCUSD"]["qty"] * 50000.0, 60000.0, delta=1)
        self.assertAlmostEqual(book["AAPL"]["qty"] * 200.0, 40000.0, delta=1)

    def test_second_sync_no_churn_when_balanced(self):
        self._write([{"symbol": "BTC/USD", "asset_class": "crypto", "weight": 1.0}])
        state, cycle = {}, {}
        broker = MockBroker(state)
        fn = price_fn({"BTC/USD": 50000.0})
        mirror.sync(broker, cycle, price_fn=fn)
        self.assertEqual(mirror.sync(broker, {}, price_fn=fn), 0)

    def test_source_exit_closes_position(self):
        self._write([{"symbol": "BTC/USD", "asset_class": "crypto", "weight": 1.0}])
        state = {}
        broker = MockBroker(state)
        fn = price_fn({"BTC/USD": 50000.0, "ETH/USD": 2000.0})
        mirror.sync(broker, {}, price_fn=fn)
        self._write([{"symbol": "ETH/USD", "asset_class": "crypto", "weight": 1.0}])
        mirror.sync(broker, {}, price_fn=fn)
        book = state["mock"]["positions"]
        self.assertNotIn("BTCUSD", book)
        self.assertIn("ETHUSD", book)

    def test_partial_trim_on_weight_cut(self):
        self._write([{"symbol": "BTC/USD", "asset_class": "crypto", "weight": 0.9}])
        state = {}
        broker = MockBroker(state)
        fn = price_fn({"BTC/USD": 50000.0})
        mirror.sync(broker, {}, price_fn=fn)
        self._write([{"symbol": "BTC/USD", "asset_class": "crypto", "weight": 0.3}])
        mirror.sync(broker, {}, price_fn=fn)
        value = state["mock"]["positions"]["BTCUSD"]["qty"] * 50000.0
        self.assertAlmostEqual(value, 30000.0, delta=100)


class ViewTests(unittest.TestCase):
    def test_message_from_dash_lands_in_hybrid(self):
        import time

        from airbank import chat as chat_mod
        from airbank.dashboard import Terminal
        original = chat_mod.respond
        chat_mod.respond = lambda *a, **k: ("ok", None)
        try:
            term = Terminal()
            term.ask("how's the book?")
            self.assertEqual(term.view, "hybrid")
            time.sleep(0.05)   # let the stubbed thread finish
            self.assertEqual(term.transcript[-1], ("assistant", "ok"))
        finally:
            chat_mod.respond = original

    def test_esc_steps_back_toward_dashboard(self):
        from airbank.dashboard import Terminal
        term = Terminal()
        term.view = "chat"
        term.handle("\x1b")
        self.assertEqual(term.view, "hybrid")
        term.handle("\x1b")
        self.assertEqual(term.view, "dash")

    def test_hybrid_frame_full_and_width_safe(self):
        from airbank.dashboard import Terminal, vlen
        term = Terminal()
        term.view = "hybrid"
        term.transcript = [("user", "hi"), ("assistant", "hello from the desk")]
        term.reveal = 10**6
        rows = term.build(100, 32)
        self.assertEqual(len(rows), 32)
        for row in rows:
            self.assertLessEqual(vlen(row), 101)


if __name__ == "__main__":
    unittest.main()
