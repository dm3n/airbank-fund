"""Background quote board for the terminal: streams live prices on a thread
so the dashboard never blocks on the network (contract assertion 38)."""
import threading
import time

from . import config, data

REFRESH_S = 15


class QuoteBoard:
    def __init__(self):
        self.symbols = ([(s, "crypto") for s in config.CRYPTO_UNIVERSE]
                        + [(s, "equity") for s in config.EQUITY_UNIVERSE])
        self.quotes = {}     # symbol -> {price, prev_close, change_pct, spark}
        self.lock = threading.Lock()
        self.error = ""
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        # seed previous closes once (change_pct baseline)
        for symbol, asset_class in self.symbols:
            if self._stop.is_set():
                return
            try:
                _, closes = data.daily_closes(symbol, asset_class, days=40)
                # baseline = prior session close, so change_pct reads as the
                # day move; seed the spark with history so TREND shows at once
                prev = closes[-2] if len(closes) >= 2 else closes[-1]
                with self.lock:
                    self.quotes[symbol] = {"prev_close": prev, "price": None,
                                           "change_pct": 0.0,
                                           "spark": closes[-24:]}
            except Exception:
                continue
        while not self._stop.is_set():
            for symbol, asset_class in self.symbols:
                if self._stop.is_set():
                    return
                try:
                    price, _ = data.latest_price(symbol, asset_class)
                except Exception as exc:
                    self.error = f"{symbol}: {str(exc)[:40]}"
                    continue
                with self.lock:
                    quote = self.quotes.setdefault(
                        symbol, {"prev_close": price, "price": None,
                                 "change_pct": 0.0, "spark": []})
                    quote["price"] = price
                    prev = quote["prev_close"] or price
                    quote["change_pct"] = (price / prev - 1) * 100 if prev else 0.0
                    quote["spark"].append(price)
                    del quote["spark"][:-24]
            self._stop.wait(REFRESH_S)

    def snapshot(self):
        with self.lock:
            return {s: dict(q) for s, q in self.quotes.items()}
