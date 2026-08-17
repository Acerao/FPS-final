"""Live XAU spot + 15m bars (COMEX shifted to spot)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

BEIJING = ZoneInfo("Asia/Shanghai")
UA = {"User-Agent": "AsiaBoxAlert/1.0"}


@dataclass
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class Snapshot:
    price: float
    source: str
    bars_15m: list[Bar]
    futures_last: float | None
    warning: str | None = None


def _get_json(url: str, timeout: float = 12) -> Any:
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_spot() -> tuple[float, str]:
    data = _get_json("https://api.gold-api.com/price/XAU")
    price = float(data["price"])
    if price <= 0:
        raise RuntimeError("spot price invalid")
    return price, "gold-api.com XAU"


def fetch_gc_bars(interval: str = "15m", range_: str = "5d") -> tuple[list[Bar], float]:
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
        f"?interval={interval}&range={range_}&includePrePost=true"
    )
    data = _get_json(url)
    result = data["chart"]["result"][0]
    ts_list = result.get("timestamp") or []
    quote = result["indicators"]["quote"][0]
    opens, highs, lows, closes = quote["open"], quote["high"], quote["low"], quote["close"]
    bars: list[Bar] = []
    last_close = None
    for i, ts in enumerate(ts_list):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        if None in (o, h, l, c):
            continue
        last_close = float(c)
        bars.append(
            Bar(
                ts=datetime.fromtimestamp(ts, tz=BEIJING),
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
            )
        )
    if not bars or last_close is None:
        raise RuntimeError("no GC=F bars")
    return bars, last_close


def shift_bars(bars: list[Bar], offset: float) -> list[Bar]:
    return [
        Bar(ts=b.ts, open=b.open + offset, high=b.high + offset, low=b.low + offset, close=b.close + offset)
        for b in bars
    ]


def asia_high_low(bars: list[Bar], day: datetime | None = None) -> tuple[float, float] | None:
    if not bars:
        return None
    day = day or datetime.now(BEIJING)
    d = day.date()
    highs: list[float] = []
    lows: list[float] = []
    for b in bars:
        local = b.ts.astimezone(BEIJING)
        if local.date() != d:
            continue
        t = local.time()
        if t.hour > 14 or (t.hour == 14 and t.minute > 30) or t.hour < 8:
            continue
        highs.append(b.high)
        lows.append(b.low)
    if not highs:
        return None
    return max(highs), min(lows)


def last_closed_m15(bars: list[Bar]) -> Bar | None:
    if len(bars) < 2:
        return bars[-1] if bars else None
    # last bar may still be forming
    return bars[-2]


def fetch_snapshot() -> Snapshot:
    warning = None
    spot, spot_src = fetch_spot()
    try:
        raw_bars, fut_last = fetch_gc_bars()
        offset = spot - fut_last
        bars = shift_bars(raw_bars, offset)
        if abs(offset) > 80:
            warning = f"现货与期货差 {offset:.1f} 美元，盒子可能略有偏差，建议对照 MT5 手动改 H/L。"
    except Exception as exc:
        bars = []
        fut_last = None
        warning = f"15 分钟K线暂不可用（{exc}），仅有现货价；请手动填写 ASIA_H / ASIA_L。"
    return Snapshot(price=spot, source=spot_src, bars_15m=bars, futures_last=fut_last, warning=warning)
