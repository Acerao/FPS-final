"""Resolve best available OHLC series for indicators."""

from __future__ import annotations

from dataclasses import dataclass

from gold_feed import Bar, fetch_em_bars, fetch_gc_bars, fetch_spot, shift_bars
from spot_history import get_history


@dataclass
class BarPack:
    bars: list[Bar]
    source: str
    note: str


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


def get_indicator_bars() -> BarPack:
    """Never raises — always returns a BarPack (bars may be empty)."""
    history = get_history()
    spot = _reference_spot()
    yahoo_err = ""

    # 1) Eastmoney M15 (国内常用)
    try:
        em = fetch_em_bars(klt=15)
        if len(em) >= 20:
            return BarPack(em, "东财 M15", "K线来自东方财富，与 MT5 可能差几美元")
        if len(em) >= 5:
            return BarPack(em, f"东财 M15 ({len(em)}根)", "K线偏少，指标仅供参考")
    except Exception as exc:
        yahoo_err = f"东财: {exc}; "

    # 2) Yahoo GC=F M15
    if spot is not None:
        try:
            raw, fut_last = fetch_gc_bars("15m", "5d")
            bars = shift_bars(raw, spot - fut_last)
            if len(bars) >= 20:
                return BarPack(bars, "雅虎 M15", "K线来自 Yahoo，与 MT5 可能差几美元")
            if len(bars) >= 5:
                return BarPack(bars, f"雅虎 M15 ({len(bars)}根)", "K线偏少，指标仅供参考")
        except Exception as exc:
            yahoo_err += str(exc)
    elif not yahoo_err:
        yahoo_err = "现货不可用，跳过雅虎 K 线"

    # 3) Local M15 from spot ticks
    m15 = history.m15_bars()
    if len(m15) >= 20:
        return BarPack(m15, f"本地 M15 ({len(m15)}根)", "在线 K 线不可用，用本程序积累的现货采样")
    if len(m15) >= 5:
        need = max(0, 20 - len(m15))
        eta = need * 15
        return BarPack(
            m15,
            f"本地 M15 ({len(m15)}根)",
            f"继续运行约 {eta} 分钟可算 ADX；M15收盘/RSI 已可用",
        )

    # 4) Local M5 fallback (faster bootstrap)
    m5 = history.m5_bars()
    if len(m5) >= 15:
        return BarPack(
            m5,
            f"本地 M5 ({len(m5)}根)",
            "M15 尚在积累，暂用 M5 近似算 RSI/ADX（标签会注明）",
        )

    note = "正在积累现货采样。"
    if yahoo_err:
        note += f" 在线K线失败: {yahoo_err[:80]}"
    note += f" 已采样 {history.tick_count} 次，约每 15 分钟 +1 根 M15。"
    if spot is None and history.tick_count == 0:
        note += " 网络全断时请填 MT5 现价并点「应用现价」。"
    return BarPack(m15 or m5, "暂无K线", note)
