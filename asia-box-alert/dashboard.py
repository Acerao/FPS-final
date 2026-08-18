"""Build full dashboard state for the GUI."""

from __future__ import annotations

from dataclasses import dataclass

from news_calendar import NewsStatus, get_news_status
from strategy import (
    ADX_RANGE_MAX,
    ADX_TREND_MIN,
    AdxState,
    Box,
    Signal,
    beijing_now,
    compute_adx,
    compute_rsi,
    evaluate,
    price_zone,
    session_status,
)


ENTRY_KEYS = {"a_buy", "a_sell", "b_long", "b_short"}


@dataclass
class Dashboard:
    price: float
    session: str
    box: Box | None
    box_src: str
    zone: str
    adx: AdxState | None
    rsi: float | None
    m15_close: float | None
    broken: str
    regime: str
    news: NewsStatus
    signal: Signal
    entry_ok: bool
    indicators_text: str


def _regime(box: Box | None, adx: AdxState | None, price: float, m15_close: float | None) -> tuple[str, str]:
    if box is None:
        return "未知", "无盒子"
    broken_up = (m15_close is not None and m15_close > box.high) or price > box.high + 1
    broken_down = (m15_close is not None and m15_close < box.low) or price < box.low - 1
    if broken_up:
        return "单边向上", "M15 已破上沿 → 策略 B"
    if broken_down:
        return "单边向下", "M15 已破下沿 → 策略 B"
    if adx is None:
        return "待K线", "ADX 暂无"
    if adx.adx >= ADX_TREND_MIN:
        if adx.plus_di > adx.minus_di:
            return "趋势偏多", f"ADX {adx.adx:.1f} ≥ {ADX_TREND_MIN}"
        return "趋势偏空", f"ADX {adx.adx:.1f} ≥ {ADX_TREND_MIN}"
    if adx.adx < ADX_RANGE_MAX:
        return "震荡", f"ADX {adx.adx:.1f} < {ADX_RANGE_MAX} → 策略 A"
    return "模糊", f"ADX {adx.adx:.1f} 在 22–28"


def build_dashboard(
    price: float,
    box: Box | None,
    box_src: str,
    adx: AdxState | None,
    rsi: float | None,
    m15_close: float | None,
    news: NewsStatus | None = None,
    now=None,
) -> Dashboard:
    now = beijing_now(now)
    news = news or get_news_status(now)
    session = session_status(now)
    zone = price_zone(price, box)
    regime, broken = _regime(box, adx, price, m15_close)
    signal = evaluate(price, box, adx, m15_close, now=now, news=news)

    entry_ok = signal.key in ENTRY_KEYS and not news.in_blackout

    rsi_txt = f"{rsi:.1f}" if rsi is not None else "--"
    adx_txt = f"{adx.adx:.1f} (+DI {adx.plus_di:.1f} / -DI {adx.minus_di:.1f})" if adx else "--"
    m15_txt = f"{m15_close:.2f}" if m15_close is not None else "--"
    box_txt = (
        f"H {box.high:.1f}  L {box.low:.1f}  RANGE {box.range:.1f}  [{box_src}]"
        if box
        else "未锁定"
    )

    indicators_text = (
        f"时段 {session}  |  日型 {regime}  |  位置 {zone}\n"
        f"盒子 {box_txt}\n"
        f"ADX {adx_txt}  |  RSI(M15) {rsi_txt}  |  M15收盘 {m15_txt}\n"
        f"结构 {broken}  |  大数据 {news.summary}\n"
        f"入场 {'✓ 可提醒' if entry_ok else '✗ 不适合'}"
    )

    return Dashboard(
        price=price,
        session=session,
        box=box,
        box_src=box_src,
        zone=zone,
        adx=adx,
        rsi=rsi,
        m15_close=m15_close,
        broken=broken,
        regime=regime,
        news=news,
        signal=signal,
        entry_ok=entry_ok,
        indicators_text=indicators_text,
    )
