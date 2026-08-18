"""Resolve best available OHLC series for indicators."""

from __future__ import annotations

from dataclasses import dataclass

from gold_feed import (
    Bar,
    fetch_em_bars,
    fetch_em_trend_bars,
    fetch_gc_bars,
    fetch_sina_min_bars,
    fetch_spot,
    shift_bars,
)
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


def _short_err(exc: Exception) -> str:
    text = str(exc).replace("\n", " ")
    if len(text) > 70:
        text = text[:70] + "…"
    return text


def _pack_if_enough(bars: list[Bar], source: str, note: str) -> BarPack | None:
    if len(bars) >= 20:
        return BarPack(bars, source, note)
    if len(bars) >= 5:
        return BarPack(bars, f"{source} ({len(bars)}根)", "K线偏少，指标仅供参考")
    return None


def get_indicator_bars() -> BarPack:
    """Never raises — always returns a BarPack (bars may be empty)."""
    history = get_history()
    spot = _reference_spot()
    errors: list[str] = []

    # 1) Eastmoney 1-minute trends → M15（国内最稳）
    try:
        pack = _pack_if_enough(
            fetch_em_trend_bars(15),
            "东财分时→M15",
            "由东方财富分钟线合成，与 MT5 可能差几美元",
        )
        if pack:
            return pack
    except Exception as exc:
        errors.append(f"东财分时 {_short_err(exc)}")

    # 2) Sina 1-minute line → M15
    try:
        pack = _pack_if_enough(
            fetch_sina_min_bars(15),
            "新浪分时→M15",
            "由新浪黄金分钟线合成，与 MT5 可能差几美元",
        )
        if pack:
            return pack
    except Exception as exc:
        errors.append(f"新浪分时 {_short_err(exc)}")

    # 3) Eastmoney official kline (often empty for XAU)
    try:
        pack = _pack_if_enough(
            fetch_em_bars(klt=15),
            "东财 M15",
            "K线来自东方财富，与 MT5 可能差几美元",
        )
        if pack:
            return pack
    except Exception as exc:
        errors.append(f"东财K线 {_short_err(exc)}")

    # 4) Yahoo GC=F M15
    if spot is not None:
        try:
            raw, fut_last = fetch_gc_bars("15m", "5d")
            pack = _pack_if_enough(
                shift_bars(raw, spot - fut_last),
                "雅虎 M15",
                "K线来自 Yahoo，与 MT5 可能差几美元",
            )
            if pack:
                return pack
        except Exception as exc:
            errors.append(f"雅虎 {_short_err(exc)}")
    else:
        errors.append("现货不可用，跳过雅虎")

    # 5) Local M15 from spot ticks
    m15 = history.m15_bars()
    if len(m15) >= 20:
        return BarPack(m15, f"本地 M15 ({len(m15)}根)", "在线 K 线不可用，用本程序积累的现货采样")
    if len(m15) >= 5:
        need = max(0, 20 - len(m15))
        return BarPack(
            m15,
            f"本地 M15 ({len(m15)}根)",
            f"继续运行约 {need * 15} 分钟可算 ADX；M15收盘/RSI 已可用",
        )

    m5 = history.m5_bars()
    if len(m5) >= 15:
        return BarPack(
            m5,
            f"本地 M5 ({len(m5)}根)",
            "M15 尚在积累，暂用 M5 近似算 RSI/ADX（标签会注明）",
        )

    note = "正在积累现货采样。"
    if errors:
        note += " 在线K线：" + "；".join(errors[:2])
    note += f" 已采样 {history.tick_count} 次。"
    if spot is None and history.tick_count == 0:
        note += " 网络全断时请填 MT5 现价并点「应用现价」。"
    return BarPack(m15 or m5, "暂无K线", note)
