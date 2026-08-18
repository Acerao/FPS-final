"""Live XAU spot + 15m bars (COMEX shifted to spot)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
import json
import ssl
import time as time_mod
from pathlib import Path
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import Request, urlopen

from tzutil import BEIJING

ROOT = Path(__file__).resolve().parent
CACHE_FILE = ROOT / "last_spot.json"

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 AsiaBoxAlert/1.3",
    "Accept": "application/json,text/plain,*/*",
}

CN_REFERER = {
    "Referer": "https://finance.sina.com.cn/",
}

EM_REFERER = {
    "Referer": "https://quote.eastmoney.com/",
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


def _decode_body(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _loads_payload(text: str) -> Any:
    """JSON or JSONP, including UTF-8 BOM from some CN CDNs."""
    text = text.strip().lstrip("\ufeff")
    if text.startswith("/*"):
        text = text.split("*/", 1)[-1].strip()
    if text[:1] not in "{[" and "=" in text[:80]:
        text = text.split("=", 1)[1].strip()
    text = text.rstrip(";").strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    return json.loads(text)


def _get_json(url: str, timeout: float = 8, extra_headers: dict | None = None) -> Any:
    last: Exception | None = None
    headers = {**UA, **(extra_headers or {})}
    for ctx in _ssl_contexts():
        for _ in range(2):
            try:
                req = Request(url, headers=headers)
                with urlopen(req, timeout=timeout, context=ctx) as resp:
                    return _loads_payload(_decode_body(resp.read()))
            except Exception as exc:
                last = exc
                time_mod.sleep(0.3)
    raise RuntimeError(f"network error: {last}") from last


def _get_text(url: str, timeout: float = 8, extra_headers: dict | None = None, encoding: str = "utf-8") -> str:
    last: Exception | None = None
    headers = {**UA, **(extra_headers or {})}
    for ctx in _ssl_contexts():
        for _ in range(2):
            try:
                req = Request(url, headers=headers)
                with urlopen(req, timeout=timeout, context=ctx) as resp:
                    raw = resp.read()
                    for enc in (encoding, "gbk", "utf-8"):
                        try:
                            return raw.decode(enc)
                        except UnicodeDecodeError:
                            continue
                    return raw.decode("utf-8", errors="replace")
            except Exception as exc:
                last = exc
                time_mod.sleep(0.3)
    raise RuntimeError(f"network error: {last}") from last


def _parse_js_quote(text: str) -> float:
    """Parse sina/tencent var quote: ...=\"price,...\" """
    inner = text.split('"')[1]
    price = float(inner.split(",")[0])
    if price <= 0:
        raise RuntimeError("quote price invalid")
    return price


def _em_scale(raw: float) -> float:
    """Eastmoney int prices are usually x100 for XAU."""
    if raw > 10000:
        return raw / 100.0
    return raw


def save_spot_cache(price: float, source: str, when: datetime | None = None) -> None:
    when = when or datetime.now(BEIJING)
    if when.tzinfo is None:
        when = when.replace(tzinfo=BEIJING)
    try:
        CACHE_FILE.write_text(
            json.dumps(
                {"price": price, "source": source, "ts": when.isoformat()},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def load_spot_cache(max_age_hours: float = 72) -> tuple[float, str, datetime] | None:
    if not CACHE_FILE.exists():
        return None
    try:
        raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(raw["ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=BEIJING)
        if datetime.now(BEIJING) - ts > timedelta(hours=max_age_hours):
            return None
        return float(raw["price"]), str(raw["source"]), ts
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None


def _yahoo_spot(symbol: str) -> float:
    urls = [
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=1d",
        f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=1d",
    ]
    last: Exception | None = None
    for url in urls:
        try:
            data = _get_json(url)
            meta = data["chart"]["result"][0]["meta"]
            price = float(meta["regularMarketPrice"])
            if price > 0:
                return price
        except Exception as exc:
            last = exc
    raise RuntimeError(f"yahoo {symbol}: {last}") from last


def _spot_sina() -> float:
    text = _get_text("https://hq.sinajs.cn/?list=hf_XAU", extra_headers=CN_REFERER, encoding="gbk")
    return _parse_js_quote(text)


def _spot_tencent() -> float:
    text = _get_text("https://qt.gtimg.cn/q=hf_XAU", extra_headers=CN_REFERER, encoding="gbk")
    return _parse_js_quote(text)


def _spot_eastmoney() -> float:
    hosts = ("push2delay.eastmoney.com", "48.push2.eastmoney.com", "push2.eastmoney.com")
    last: Exception | None = None
    for host in hosts:
        try:
            url = f"https://{host}/api/qt/stock/get?secid=122.XAU&fields=f43,f57"
            data = _get_json(url, extra_headers=EM_REFERER)
            raw = float(data["data"]["f43"])
            price = _em_scale(raw)
            if price > 500:
                return price
        except Exception as exc:
            last = exc
    raise RuntimeError(f"eastmoney spot: {last}") from last


def _spot_dwo() -> float:
    data = _get_json("https://openapi.dwo.cc/api/jinjia")
    futures = data.get("data", {}).get("futures") or []
    for item in futures:
        name = str(item.get("name", ""))
        if "黄金" in name and "白银" not in name:
            price = float(item["trade_price"])
            if price > 500:
                return price
    raise RuntimeError("dwo: no gold futures row")


def _spot_gold_api() -> float:
    data = _get_json("https://api.gold-api.com/price/XAU")
    price = float(data["price"])
    if price <= 0:
        raise RuntimeError("spot price invalid")
    return price


def _spot_xaus() -> float:
    data = _get_json("https://xaus.com/api/v1/spot?currency=USD&unit=oz")
    price = float(data["xau"]["price"])
    if price <= 0:
        raise RuntimeError("xaus price invalid")
    return price


def _spot_minted() -> float:
    data = _get_json("https://mintedmetal.com/api/prices.json")
    price = float(data["metals"]["gold"]["price"])
    if price <= 0:
        raise RuntimeError("minted price invalid")
    return price


def _spot_goldprice_dev() -> float:
    data = _get_json("https://goldprice.dev/v1/prices?symbol=XAU-USD-SPOT")
    price = float(data["price"])
    if price <= 0:
        raise RuntimeError("goldprice.dev invalid")
    return price


SPOT_FETCHERS: list[tuple[str, Callable[[], float]]] = [
    # 国内源优先（通常比 Yahoo/gold-api 更稳）
    ("新浪 hf_XAU", _spot_sina),
    ("腾讯 hf_XAU", _spot_tencent),
    ("东方财富 XAU", _spot_eastmoney),
    ("小渡 openapi", _spot_dwo),
    # 国外备用
    ("gold-api.com", _spot_gold_api),
    ("xaus.com", _spot_xaus),
    ("goldprice.dev", _spot_goldprice_dev),
    ("mintedmetal.com", _spot_minted),
    ("yahoo XAUUSD=X", lambda: _yahoo_spot("XAUUSD=X")),
    ("yahoo GC=F", lambda: _yahoo_spot("GC=F")),
]


def fetch_spot() -> tuple[float, str]:
    """Try multiple public spot sources, then fall back to disk cache."""
    errors: list[str] = []
    for name, fn in SPOT_FETCHERS:
        try:
            price = fn()
            save_spot_cache(price, name)
            return price, name
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    cached = load_spot_cache()
    if cached:
        price, src, ts = cached
        age_min = max(0, int((datetime.now(BEIJING) - ts).total_seconds() // 60))
        return price, f"缓存·{src}({age_min}分钟前)"
    brief = errors[0] if errors else "unknown"
    if len(brief) > 100:
        brief = brief[:100] + "…"
    raise RuntimeError(f"所有报价源不可用 ({brief})")


def resolve_spot(manual: float | None = None) -> tuple[float, str]:
    """Network spot with manual MT5 override when online sources fail."""
    if manual is not None and manual > 500:
        save_spot_cache(manual, "MT5手动")
        return manual, "MT5手动"
    return fetch_spot()


def aggregate_bars(bars: list[Bar], minutes: int) -> list[Bar]:
    buckets: dict[datetime, Bar] = {}
    for b in bars:
        ts = b.ts.astimezone(BEIJING).replace(second=0, microsecond=0)
        start = ts.replace(minute=(ts.minute // minutes) * minutes)
        if start not in buckets:
            buckets[start] = Bar(ts=start, open=b.open, high=b.high, low=b.low, close=b.close)
        else:
            cur = buckets[start]
            cur.high = max(cur.high, b.high)
            cur.low = min(cur.low, b.low)
            cur.close = b.close
    return sorted(buckets.values(), key=lambda x: x.ts)


def fetch_em_trend_bars(minutes: int = 15) -> list[Bar]:
    """Build M15 from Eastmoney 1-minute trends (kline history is often empty for XAU)."""
    path = (
        "/api/qt/stock/trends2/get?secid=122.XAU"
        "&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=5"
    )
    hosts = ("push2delay.eastmoney.com", "48.push2.eastmoney.com", "push2.eastmoney.com")
    last_err: Exception | None = None
    for host in hosts:
        try:
            data = _get_json(f"https://{host}{path}", extra_headers=EM_REFERER, timeout=10)
            lines = (data.get("data") or {}).get("trends") or []
            m1: list[Bar] = []
            for line in lines:
                parts = str(line).split(",")
                if len(parts) < 5:
                    continue
                ts = datetime.strptime(parts[0].strip(), "%Y-%m-%d %H:%M").replace(tzinfo=BEIJING)
                o, c, h, l = (float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4]))
                high, low = max(o, c, h, l), min(o, c, h, l)
                m1.append(Bar(ts=ts, open=o, high=high, low=low, close=c))
            bars = aggregate_bars(m1, minutes)
            if len(bars) >= 5:
                return bars
            last_err = RuntimeError(f"too few bars: {len(bars)}")
        except Exception as exc:
            last_err = exc
    raise RuntimeError(f"eastmoney trends: {last_err}") from last_err


def fetch_sina_min_bars(minutes: int = 15) -> list[Bar]:
    """Build M15 from Sina XAU 1-minute line (国内可连)."""
    url = (
        "https://stock2.finance.sina.com.cn/futures/api/openapi.php/"
        "GlobalFuturesService.getGlobalFuturesMinLine?symbol=XAU"
    )
    data = _get_json(url, extra_headers=CN_REFERER, timeout=10)
    rows = (((data.get("result") or {}).get("data") or {}).get("minLine_1d")) or []
    m1: list[Bar] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        try:
            ts = datetime.strptime(str(row[-1]), "%Y-%m-%d %H:%M:%S").replace(tzinfo=BEIJING)
            price = float(row[5]) if len(row) >= 10 else float(row[1])
        except (TypeError, ValueError):
            continue
        m1.append(Bar(ts=ts, open=price, high=price, low=price, close=price))
    bars = aggregate_bars(m1, minutes)
    if len(bars) < 5:
        raise RuntimeError(f"sina min too few: {len(bars)}")
    return bars


def fetch_em_bars(klt: int = 15, days: int = 5) -> list[Bar]:
    """M15/M5 bars from Eastmoney kline API (often empty for XAU)."""
    beg = (datetime.now(BEIJING) - timedelta(days=days)).strftime("%Y%m%d")
    end = datetime.now(BEIJING).strftime("%Y%m%d")
    path = (
        "/api/qt/stock/kline/get?secid=122.XAU"
        "&ut=fa5fd1943c7b386f172d6893dbfba10b"
        f"&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55&fqt=0&klt={klt}"
        f"&beg={beg}&end={end}&lmt=200"
    )
    hosts = (
        "push2delay.eastmoney.com",
        "push2his.eastmoney.com",
        "48.push2his.eastmoney.com",
        "push2hisdelay.eastmoney.com",
    )
    last_err: Exception | None = None
    for host in hosts:
        try:
            data = _get_json(f"https://{host}{path}", extra_headers=EM_REFERER, timeout=10)
            lines = (data.get("data") or {}).get("klines") or []
            bars: list[Bar] = []
            for line in lines:
                parts = str(line).split(",")
                if len(parts) < 5:
                    continue
                ts_raw = parts[0].strip()
                try:
                    ts = datetime.strptime(ts_raw, "%Y-%m-%d %H:%M").replace(tzinfo=BEIJING)
                except ValueError:
                    ts = datetime.strptime(ts_raw, "%Y-%m-%d").replace(tzinfo=BEIJING)
                o, c, h, l = (_em_scale(float(x)) for x in parts[1:5])
                high, low = max(o, c, h, l), min(o, c, h, l)
                bars.append(Bar(ts=ts, open=o, high=high, low=low, close=c))
            if len(bars) >= 5:
                return bars
            last_err = RuntimeError(f"empty klines from {host}")
        except Exception as exc:
            last_err = exc
    raise RuntimeError(f"eastmoney kline: {last_err}") from last_err


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
    except Exception:
        bars = []
        fut_last = None
        warning = "K线暂时连不上（不影响现货刷新）。请手动填写 ASIA_H / ASIA_L。"
    return Snapshot(price=spot, source=spot_src, bars_15m=bars, futures_last=fut_last, warning=warning)
