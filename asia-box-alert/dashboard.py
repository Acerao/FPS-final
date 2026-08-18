"""Build full dashboard state for the GUI."""

from __future__ import annotations

from dataclasses import dataclass

from news_calendar import NewsStatus, get_news_status
from scale_grid import (
    GRID_LOT,
    GRID_MAX_LAYERS,
    GRID_STEP,
    GridState,
    average_price,
    basket_tp_price,
    evaluate_grid,
    float_pnl_usd,
    layer_prices,
    next_add_price,
    stop_price,
)
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


ENTRY_KEYS = {
    "a_buy",
    "a_sell",
    "b_long",
    "b_short",
    "grid_add",
    "grid_close_all",
    "grid_stop_all",
    "grid_flatten",
}


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
    bar_src: str
    bar_note: str
    adx_tf: str
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
    bar_src: str = "",
    bar_note: str = "",
    adx_tf: str = "M15",
    strategy: str = "asia_box",
    grid: GridState | None = None,
) -> Dashboard:
    now = beijing_now(now)
    news = news or get_news_status(now)
    session = session_status(now)
    zone = price_zone(price, box)
    regime, broken = _regime(box, adx, price, m15_close)
    grid = grid or GridState()
    if strategy == "scale_grid":
        signal = evaluate_grid(price, grid, adx, now, news)
    else:
        signal = evaluate(price, box, adx, m15_close, now=now, news=news)

    entry_ok = signal.key in ENTRY_KEYS and not news.in_blackout

    rsi_txt = f"{rsi:.1f}" if rsi is not None else "--"
    if adx:
        tf = f"{adx_tf}" if adx_tf else "M15"
        adx_txt = f"{adx.adx:.1f}({tf}) (+DI {adx.plus_di:.1f} / -DI {adx.minus_di:.1f})"
    else:
        adx_txt = "--"
    m15_txt = f"{m15_close:.2f}" if m15_close is not None else "--"
    box_txt = (
        f"H {box.high:.1f}  L {box.low:.1f}  RANGE {box.range:.1f}  [{box_src}]"
        if box
        else "未锁定"
    )

    kline_line = f"K线 {bar_src}" if bar_src else "K线 --"
    if bar_note:
        kline_line += f"\n{bar_note}"

    missing: list[str] = []
    if adx is None:
        missing.append("ADX 需约 28 根 M15（或改用 M5）")
    if rsi is None:
        missing.append("RSI 需约 15 根 M15（入场不看 RSI）")
    if m15_close is None:
        missing.append("M15收盘 需至少 2 根 K 线")
    if missing:
        kline_line += f"\n⏳ {' · '.join(missing)}"

    if strategy == "scale_grid":
        avg = average_price(grid)
        tp = basket_tp_price(grid)
        stop = stop_price(grid)
        nxt = next_add_price(grid)
        pnl = float_pnl_usd(price, grid)
        levels = "、".join(f"{p:.1f}" for p in layer_prices(grid)) or "无"
        side_cn = {"long": "多", "short": "空"}.get(grid.side, "未开")
        lines = [
            f"策略 等距网格（回弹全平，禁止翻倍马丁）  |  时段 {session}",
            f"方向 {side_cn}  层数 {grid.layers}/{GRID_MAX_LAYERS}  间距 ${GRID_STEP:.0f}  手数 {GRID_LOT}",
        ]
        if avg and tp and stop:
            nxt_txt = f"下一层 {nxt:.1f}" if nxt else "已到最大层"
            pnl_txt = f"浮盈约 ${pnl:.0f}" if pnl is not None else ""
            lines.append(f"成本 {levels}  均价 {avg:.1f}  全平 {tp:.1f}  硬止损 {stop:.1f}")
            lines.append(f"{nxt_txt}  {pnl_txt}".strip())
        else:
            lines.append(
                f"尚未开始。点「开始本轮」用现价开第1层；每 ${GRID_STEP:.0f} 等量加一层，最多 {GRID_MAX_LAYERS} 层，回弹全平。"
            )
        lines.append(kline_line)
        lines.append(f"ADX {adx_txt}  |  RSI(M15) {rsi_txt}  |  大数据 {news.summary}")
        lines.append("✓ 可提醒" if entry_ok else "观察中")
        indicators_text = "\n".join(lines)
    else:
        indicators_text = (
            f"策略 亚盘盒子  |  时段 {session}  |  日型 {regime}  |  位置 {zone}\n"
            f"盒子 {box_txt}\n"
            f"{kline_line}\n"
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
        bar_src=bar_src,
        bar_note=bar_note,
        adx_tf=adx_tf,
        indicators_text=indicators_text,
    )
