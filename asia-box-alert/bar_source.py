"""Resolve best available OHLC series for indicators."""

from __future__ import annotations

from dataclasses import dataclass

from gold_feed import Bar, fetch_gc_bars, fetch_spot, shift_bars
from spot_history import get_history


@dataclass
class BarPack:
    bars: list[Bar]
    source: str
    note: str


def get_indicator_bars() -> BarPack:
    history = get_history()
    spot, _ = fetch_spot()

    # 1) Yahoo GC=F M15
    try:
        raw, fut_last = fetch_gc_bars("15m", "5d")
        bars = shift_bars(raw, spot - fut_last)
        if len(bars) >= 20:
            return BarPack(bars, "雅虎 M15", "K线来自 Yahoo，与 MT5 可能差几美元")
        if len(bars) >= 5:
            return BarPack(bars, f"雅虎 M15 ({len(bars)}根)", "K线偏少，指标仅供参考")
    except Exception as exc:
        yahoo_err = str(exc)
    else:
        yahoo_err = ""

    # 2) Local M15 from spot ticks
    m15 = history.m15_bars()
    if len(m15) >= 20:
        return BarPack(m15, f"本地 M15 ({len(m15)}根)", "雅虎不可用，用本程序积累的现货采样")
    if len(m15) >= 5:
        need = max(0, 20 - len(m15))
        eta = need * 15
        return BarPack(
            m15,
            f"本地 M15 ({len(m15)}根)",
            f"继续运行约 {eta} 分钟可算 ADX；M15收盘/RSI 已可用",
        )

    # 3) Local M5 fallback (faster bootstrap)
    m5 = history.m5_bars()
    if len(m5) >= 15:
        return BarPack(
            m5,
            f"本地 M5 ({len(m5)}根)",
            "M15 尚在积累，暂用 M5 近似算 RSI/ADX（标签会注明）",
        )

    note = "正在积累现货采样。"
    if yahoo_err:
        note += f" 雅虎失败: {yahoo_err[:60]}"
    note += f" 已采样 {history.tick_count} 次，约每 15 分钟 +1 根 M15。"
    return BarPack(m15 or m5, "暂无K线", note)
