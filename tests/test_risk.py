import unittest

from airbank import risk
from airbank.config import CAPS


def order(**kw):
    base = {"symbol": "BTCUSD", "side": "buy", "notional_usd": 100.0,
            "asset_class": "crypto", "data_age_min": 1.0}
    base.update(kw)
    return base


def state(**kw):
    base = {"halt": False, "trades_today": 0, "day_start_equity": 1000.0}
    base.update(kw)
    return base


class RiskCapTests(unittest.TestCase):
    def test_ok_order_passes(self):
        ok, reason = risk.check_order(order(), state(), 0.0)
        self.assertTrue(ok, reason)

    def test_halted_state_refuses(self):
        ok, reason = risk.check_order(order(), state(halt=True), 0.0)
        self.assertFalse(ok)
        self.assertIn("halted", reason)

    def test_max_position_cap(self):
        big = order(notional_usd=CAPS["max_position_usd"] + 1)
        ok, reason = risk.check_order(big, state(), 0.0)
        self.assertFalse(ok)
        self.assertIn("position", reason)

    def test_gross_exposure_cap(self):
        ok, reason = risk.check_order(order(), state(), CAPS["max_gross_usd"] - 50)
        self.assertFalse(ok)
        self.assertIn("gross", reason)

    def test_trades_per_day_cap(self):
        s = state(trades_today=CAPS["max_trades_per_day"])
        ok, reason = risk.check_order(order(), s, 0.0)
        self.assertFalse(ok)
        self.assertIn("trades per day", reason)

    def test_stale_data_refused(self):
        stale = order(data_age_min=CAPS["stale_data_min"]["crypto"] + 5)
        ok, reason = risk.check_order(stale, state(), 0.0)
        self.assertFalse(ok)
        self.assertIn("stale", reason)

    def test_stale_data_allows_exits(self):
        stale_sell = order(side="sell", data_age_min=999)
        ok, _ = risk.check_order(stale_sell, state(), 0.0)
        self.assertTrue(ok)

    def test_kill_switch(self):
        s = state(day_start_equity=1000.0)
        self.assertFalse(risk.kill_switch_breached(s, 980.0))   # -2%
        self.assertTrue(risk.kill_switch_breached(s, 965.0))    # -3.5%

    def test_live_not_armed_without_ack(self):
        self.assertFalse(risk.live_trading_armed(state(live_ack=True)))  # paper mode


if __name__ == "__main__":
    unittest.main()
