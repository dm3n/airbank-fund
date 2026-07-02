import unittest

from airbank.brokers import ExecutionRefused, MockBroker, WalletBroker


def buy(symbol="BTCUSD", notional=1000.0, price=50000.0):
    return {"symbol": symbol, "side": "buy", "notional_usd": notional,
            "asset_class": "crypto", "price": price}


class MockBrokerTests(unittest.TestCase):
    def setUp(self):
        self.state = {}
        self.broker = MockBroker(self.state)

    def test_buy_creates_position_and_reduces_cash(self):
        start = self.broker.book["cash"]
        self.broker.submit_order(buy(), self.state)
        self.assertAlmostEqual(self.broker.book["cash"], start - 1000.0)
        pos = self.broker.book["positions"]["BTCUSD"]
        self.assertAlmostEqual(pos["qty"], 0.02)
        self.assertAlmostEqual(self.broker.equity(), start)  # flat at fill price

    def test_sell_realizes_pnl(self):
        self.broker.submit_order(buy(price=50000.0), self.state)
        self.broker.submit_order(
            {"symbol": "BTCUSD", "side": "sell", "notional_usd": 0,
             "asset_class": "crypto", "price": 55000.0}, self.state)
        self.assertAlmostEqual(self.broker.book["realized_pnl"], 100.0)  # +10% on $1k
        self.assertEqual(self.broker.book["positions"], {})

    def test_mark_to_market_moves_equity(self):
        self.broker.submit_order(buy(price=50000.0), self.state)
        start = self.broker.book["starting_cash"]
        self.broker.mark("BTCUSD", 60000.0)
        self.assertAlmostEqual(self.broker.equity(), start + 200.0)  # +20% on $1k

    def test_insufficient_cash_refused(self):
        with self.assertRaises(ExecutionRefused):
            self.broker.submit_order(buy(notional=10**9), self.state)

    def test_fill_requires_price(self):
        order = buy()
        order["price"] = 0
        with self.assertRaises(ExecutionRefused):
            self.broker.submit_order(order, self.state)

    def test_sell_without_position_refused(self):
        with self.assertRaises(ExecutionRefused):
            self.broker.submit_order(
                {"symbol": "ETHUSD", "side": "sell", "notional_usd": 0,
                 "asset_class": "crypto", "price": 3000.0}, self.state)

    def test_averaging_into_position(self):
        self.broker.submit_order(buy(notional=1000.0, price=50000.0), self.state)
        self.broker.submit_order(buy(notional=1000.0, price=100000.0), self.state)
        pos = self.broker.book["positions"]["BTCUSD"]
        self.assertAlmostEqual(pos["qty"], 0.03)
        self.assertAlmostEqual(pos["avg_price"], 2000.0 / 0.03)

    def test_view_shape(self):
        self.broker.submit_order(buy(), self.state)
        view = self.broker.view()
        self.assertEqual(view["account"], "mock")
        self.assertEqual(len(view["positions"]), 1)
        self.assertIn("total_pnl_pct", view)

    def test_crash_resume_book_lives_in_state(self):
        self.broker.submit_order(buy(), self.state)
        resumed = MockBroker(self.state)  # same state dict, fresh broker
        self.assertEqual(resumed.held_symbols(), {"BTCUSD"})


class WalletBrokerTests(unittest.TestCase):
    def test_wallet_never_executes(self):
        state = {}
        broker = WalletBroker(state)
        self.assertFalse(broker.can_execute)
        with self.assertRaises(ExecutionRefused):
            broker.submit_order(buy(), state)


if __name__ == "__main__":
    unittest.main()
