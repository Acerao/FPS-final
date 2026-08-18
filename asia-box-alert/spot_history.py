"""Persist spot ticks and build OHLC bars when Yahoo K-lines fail."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from gold_feed import Bar
from tzutil import BEIJING

ROOT = Path(__file__).resolve().parent
TICK_FILE = ROOT / "price_ticks.json"
MAX_AGE_HOURS = 72


def _bucket_start(dt: datetime, minutes: int) -> datetime:
    dt = dt.astimezone(BEIJING).replace(second=0, microsecond=0)
    return dt.replace(minute=(dt.minute // minutes) * minutes)


class SpotHistory:
    def __init__(self, path: Path = TICK_FILE) -> None:
        self.path = path
        self.ticks: list[tuple[datetime, float]] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            cutoff = datetime.now(BEIJING) - timedelta(hours=MAX_AGE_HOURS)
            for item in raw:
                ts = datetime.fromisoformat(item["ts"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=BEIJING)
                if ts >= cutoff:
                    self.ticks.append((ts, float(item["price"])))
        except (json.JSONDecodeError, KeyError, ValueError):
            self.ticks = []

    def _save(self) -> None:
        cutoff = datetime.now(BEIJING) - timedelta(hours=MAX_AGE_HOURS)
        self.ticks = [(t, p) for t, p in self.ticks if t >= cutoff]
        payload = [{"ts": t.isoformat(), "price": p} for t, p in self.ticks[-8000:]]
        self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def add(self, price: float, when: datetime | None = None) -> None:
        when = when or datetime.now(BEIJING)
        if when.tzinfo is None:
            when = when.replace(tzinfo=BEIJING)
        else:
            when = when.astimezone(BEIJING)
        if self.ticks and abs(self.ticks[-1][1] - price) < 0.001:
            # still refresh time if >30s
            if (when - self.ticks[-1][0]).total_seconds() < 30:
                return
        self.ticks.append((when, price))
        if len(self.ticks) > 8000:
            self.ticks = self.ticks[-8000:]
        self._save()

    def _aggregate(self, minutes: int) -> list[Bar]:
        buckets: dict[datetime, Bar] = {}
        for ts, price in self.ticks:
            start = _bucket_start(ts, minutes)
            if start not in buckets:
                buckets[start] = Bar(ts=start, open=price, high=price, low=price, close=price)
            else:
                b = buckets[start]
                b.high = max(b.high, price)
                b.low = min(b.low, price)
                b.close = price
        return sorted(buckets.values(), key=lambda b: b.ts)

    def m15_bars(self) -> list[Bar]:
        return self._aggregate(15)

    def m5_bars(self) -> list[Bar]:
        return self._aggregate(5)

    @property
    def tick_count(self) -> int:
        return len(self.ticks)


_history = SpotHistory()


def get_history() -> SpotHistory:
    return _history
