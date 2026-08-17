"""Live XAU spot + 15m bars (COMEX shifted to spot)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
import json
import ssl
import time as time_mod
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from tzutil import BEIJING

UA = {
    "User-Agent": "Mozilla/5.0 AsiaBoxAlert/1.1",
    "Accept": "application/json",
}


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


def _ssl_contexts():
    yield ssl.create_default_context()
    yield ssl._create_unverified_context()


def _get_json(url: str, timeout: float = 8) -> Any:
    last: Exception | None = None
    for ctx in _ssl_contexts():
        for _ in range(2):
            try:
                req = Request(url, headers=UA)
                with urlopen(req, timeout=timeout, context=ctx) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as exc:
                last = exc
                time_mod.sleep(0.3)
    raise RuntimeError(f"network error: {last}") from last


def fetch_spot() -> tuple[float, str]:
    data = _get_json("https://api.gold-api.com/price/XAU")
    price = float(data["price"])
    if price <= 0:
        raise RuntimeError("spot price invalid")
    return price, "gold-api.com XAU"


def fetch_gc_bars(interval: str = "15m", range_: str = "5d") -> tuple[list[Bar], float]:
    urls = [
        "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
        f"?interval={interval}&range={range_}&includePrePost=true",
        "https://query2.finance.yahoo.com/v8/finance/chart/GC=F"
        f"?interval={interval}&range={range_}&includePrePost=true",
    ]
    last_err: Exception | None = None
    data = None
    for url in urls:
        try:
            data = _get_json(url)
            break
        except Exception as exc:
            last_err = exc
    if data is None:
        raise RuntimeError(f"yahoo bars unavailable: {last_err}") from last_err
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


def asia_session_date(now: datetime | None = None):
    """Asia box belongs to the session that started at 08:00, even after midnight."""
    now = now or datetime.now(BEIJING)
    if now.tzinfo is None:
        now = now.replace(tzinfo=BEIJING)
    else:
        now = now.astimezone(BEIJING)
    if now.time() < time(8, 0):
        return (now - timedelta(days=1)).date()
    return now.date()


def asia_high_low(bars: list[Bar], day: datetime | None = None) -> tuple[float, float] | None:
    if not bars:
        return None
    d = asia_session_date(day)
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
        warning = "K线暂时连不上（不影响现货刷新）。请手动填写 ASIA_H / ASIA_L。"
    return Snapshot(price=spot, source=spot_src, bars_15m=bars, futures_last=fut_last, warning=warning)
