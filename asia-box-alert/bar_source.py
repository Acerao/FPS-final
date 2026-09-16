"""Resolve best available OHLC series for indicators."""

from __future__ import annotations

from dataclasses import dataclass, field

from gold_feed import (
    Bar,
    aggregate_bars,
    fetch_em_bars,
    fetch_em_minute_bars,
    fetch_gc_bars,
    fetch_sina_minute_bars,
    fetch_spot,
    shift_bars,
)
from spot_history import get_history
from strategy import ADX_MIN_BARS


@dataclass
class BarPack:
    bars: list[Bar]
    source: str
    note: str
    adx_bars: list[Bar] = field(default_factory=list)
    adx_tf: str = "M15"


def _reference_spot() -> float | None:
    try:
        price, _ = fetch_spot()
        return price
    except Exception:
        history = get_history()
        last = history.last_tick()
        if last:
            return last[1]
        return None


def _short_err(exc: Exception) -> str:
    text = str(exc).replace("\n", " ")
    if len(text) > 70:
        text = text[:70] + "…"
    return text


def _with_adx_series(m15: list[Bar], m5: list[Bar], source: str, note: str) -> BarPack | None:
    if len(m15) < 5:
        return None
    src = source if len(m15) >= 20 else f"{source} ({len(m15)}根)"
    extra = "" if len(m15) >= 20 else "K线偏少，指标仅供参考。"
    if len(m15) >= ADX_MIN_BARS:
        adx_bars, adx_tf = m15, "M15"
    elif len(m5) >= ADX_MIN_BARS:
        adx_bars, adx_tf = m5, "M5"
        extra += f" ADX暂用M5（M15仅{len(m15)}根，满{ADX_MIN_BARS}根后改回M15）。"
    else:
        adx_bars, adx_tf = m15, "M15"
        extra += f" ADX还需约 {max(0, ADX_MIN_BARS - len(m15))} 根M15。"
    return BarPack(m15, src, (note + extra).strip(), adx_bars, adx_tf)


def _from_minutes(m1: list[Bar], source: str, note: str) -> BarPack | None:
    return _with_adx_series(aggregate_bars(m1, 15), aggregate_bars(m1, 5), source, note)


def get_indicator_bars() -> BarPack:
    """Never raises — always returns a BarPack (bars may be empty)."""
    history = get_history()
    spot = _reference_spot()
    errors: list[str] = []

    try:
        pack = _from_minutes(fetch_em_minute_bars(), "东财分时→M15", "由东方财富分钟线合成，与 MT5 可能差几美元。")
        if pack:
            return pack
    except Exception as exc:
        errors.append(f"东财分时 {_short_err(exc)}")

    try:
        pack = _from_minutes(fetch_sina_minute_bars(), "新浪分时→M15", "由新浪黄金分钟线合成，与 MT5 可能差几美元。")
        if pack:
            return pack
    except Exception as exc:
        errors.append(f"新浪分时 {_short_err(exc)}")

    try:
        pack = _with_adx_series(fetch_em_bars(klt=15), fetch_em_bars(klt=5), "东财 M15", "K线来自东方财富，与 MT5 可能差几美元。")
        if pack:
            return pack
    except Exception as exc:
        errors.append(f"东财K线 {_short_err(exc)}")

    if spot is not None:
        try:
            raw, fut_last = fetch_gc_bars("15m", "5d")
            pack = _with_adx_series(shift_bars(raw, spot - fut_last), [], "雅虎 M15", "K线来自 Yahoo，与 MT5 可能差几美元。")
            if pack:
                return pack
        except Exception as exc:
            errors.append(f"雅虎 {_short_err(exc)}")
    else:
        errors.append("现货不可用，跳过雅虎")

    m15 = history.m15_bars()
    m5 = history.m5_bars()
    pack = _with_adx_series(m15, m5, "本地 M15", "在线K线不足，用本程序积累的现货采样。")
    if pack:
        return pack

    note = "正在积累现货采样。"
    if errors:
        note += " 在线K线：" + "；".join(errors[:2])
    note += f" 已采样 {history.tick_count} 次。"
    if spot is None and history.tick_count == 0:
        note += " 网络全断时请填 MT5 现价并点「应用现价」。"
    return BarPack(m15 or m5, "暂无K线", note, m15 or m5, "M15")
