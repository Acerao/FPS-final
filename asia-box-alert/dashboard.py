"""Build full dashboard state for the GUI."""

from __future__ import annotations

from dataclasses import dataclass
import math

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
    SL_USD,
    AdxState,
    Box,
    Signal,
    beijing_now,
    clamp_lot,
    compute_adx,
    compute_rsi,
    evaluate,
    price_zone,
    risk_dollars,
    session_status,
)


def _profile_for(strategy: str) -> str:
    if strategy == "asia_box_hwr":
        return "high_winrate"
    if strategy == "asia_box_sprint":
        return "sprint"
    return "classic"


def _strategy_label(strategy: str) -> str:
    return {
        "asia_box": "亚盘盒子",
        "asia_box_hwr": "亚盘盒子·高胜率",
        "asia_box_sprint": "亚盘盒子·冲刺$1k",
        "asia_box_lines": "画线策略·H8风格",
        "scale_grid": "等距网格",
    }.get(strategy, strategy)


ENTRY_KEYS = {
    "a_buy",
    "a_sell",
    "b_long",
    "b_short",
    "grid_add",
    "grid_close_all",
    "grid_stop_all",
    "grid_flatten",
    "line_long_call",
    "line_short_call",
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
    line_overlay: dict | None = None


def _swing_points(bars: list[object], kind: str) -> list[tuple[int, float]]:
    vals: list[tuple[int, float]] = []
    if len(bars) < 5:
        return vals
    for i in range(2, len(bars) - 2):
        cur = bars[i]
        prev = bars[i - 1]
        nxt = bars[i + 1]
        c = float(getattr(cur, kind))
        p = float(getattr(prev, kind))
        n = float(getattr(nxt, kind))
        if kind == "high":
            if c >= p and c >= n:
                vals.append((i, c))
        else:
            if c <= p and c <= n:
                vals.append((i, c))
    return vals


def _fit_line(points: list[tuple[int, float]]) -> tuple[float, float] | None:
    if len(points) < 2:
        return None
    n = float(len(points))
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    sxx = sum(p[0] * p[0] for p in points)
    sxy = sum(p[0] * p[1] for p in points)
    den = n * sxx - sx * sx
    if abs(den) < 1e-9:
        return None
    a = (n * sxy - sx * sy) / den
    b = (sy - a * sx) / n
    return a, b


def _line_y(line: tuple[float, float] | None, x: int, fallback: float) -> float:
    if not line:
        return fallback
    a, b = line
    return a * x + b


def _line_mode_signal(price: float, bars: list[object], lot: float) -> tuple[Signal, dict]:
    n = len(bars)
    last_x = n - 1
    close = float(getattr(bars[-1], "close"))
    prev_close = float(getattr(bars[-2], "close")) if n >= 2 else close
    highs = _swing_points(bars, "high")
    lows = _swing_points(bars, "low")
    up_fit = _fit_line(highs[-6:])
    dn_fit = _fit_line(lows[-6:])
    up_now = _line_y(up_fit, last_x, max(float(getattr(b, "high")) for b in bars[-20:]))
    dn_now = _line_y(dn_fit, last_x, min(float(getattr(b, "low")) for b in bars[-20:]))
    up_prev = _line_y(up_fit, max(0, last_x - 1), up_now)
    dn_prev = _line_y(dn_fit, max(0, last_x - 1), dn_now)

    box_low = min(float(getattr(b, "low")) for b in bars[-24:])
    box_high = box_low + (max(float(getattr(b, "high")) for b in bars[-24:]) - box_low) * 0.35

    pad = max(1.0, (up_now - dn_now) * 0.08 if up_now > dn_now else 1.0)
    break_up = close > up_now + pad and prev_close <= up_prev + pad
    break_down = close < dn_now - pad and prev_close >= dn_prev - pad
    near_up = abs(close - up_now) <= pad
    near_dn = abs(close - dn_now) <= pad or (box_low <= close <= box_high)

    plan: dict | None = None
    tol_now = 1.5  # 允许“现在下市价”的最大偏离（美元）
    if break_up:
        entry = up_now
        sl = entry - SL_USD
        tp = entry + 18.0
        diff = abs(close - entry)
        now_txt = f"当前价 {close:.1f} 与 Entry {entry:.1f} 差 ${diff:.1f}"
        market_ok = diff <= tol_now
        sig = Signal(
            "line_long_call",
            "LINES",
            "画线偏多：上破压力",
            (
                f"H8风格：已上破下降压力。\n{now_txt}\n"
                + (
                    f"满足条件可直接市价做多（不等回踩）。SL {sl:.1f}，TP {tp:.1f}。"
                    if market_ok
                    else f"建议挂 Entry 限价回踩：{entry:.1f}，不破再多。SL {sl:.1f}，TP {tp:.1f}。"
                )
            ),
            True,
        )
        bias = "偏多"
        plan = {"side": "long", "entry": entry, "sl": sl, "tp": tp}
    elif break_down:
        entry = dn_now
        sl = entry + SL_USD
        tp = entry - 18.0
        diff = abs(close - entry)
        now_txt = f"当前价 {close:.1f} 与 Entry {entry:.1f} 差 ${diff:.1f}"
        market_ok = diff <= tol_now
        sig = Signal(
            "line_short_call",
            "LINES",
            "画线偏空：跌破支撑",
            (
                f"H8风格：已跌破下轨/支撑箱。\n{now_txt}\n"
                + (
                    f"满足条件可直接市价做空（不等反抽）。SL {sl:.1f}，TP {tp:.1f}。"
                    if market_ok
                    else f"建议挂 Entry 限价反抽承压：{entry:.1f}。SL {sl:.1f}，TP {tp:.1f}。"
                )
            ),
            True,
        )
        bias = "偏空"
        plan = {"side": "short", "entry": entry, "sl": sl, "tp": tp}
    elif near_up:
        entry = up_now
        sl = entry + 15.0
        tp = entry - 12.0
        sig = Signal(
            "line_wait",
            "LINES",
            "画线压力附近",
            f"价格靠近下降压力 {up_now:.1f}，先防假突破。若出现阴吞噬可轻仓空：SL {sl:.1f} / TP {tp:.1f}。\n（如要市价：等你确认后再自己判断，不由软件强制）",
            False,
        )
        bias = "压力观察"
        plan = {"side": "short", "entry": entry, "sl": sl, "tp": tp}
    elif near_dn:
        entry = max(dn_now, box_low)
        sl = entry - 15.0
        tp = entry + 12.0
        sig = Signal(
            "line_wait",
            "LINES",
            "画线支撑附近",
            f"价格靠近支撑/需求区 {box_low:.1f}-{box_high:.1f}，若出现阳吞噬可轻仓多：SL {sl:.1f} / TP {tp:.1f}。\n（如要市价：等你确认后再自己判断，不由软件强制）",
            False,
        )
        bias = "支撑观察"
        plan = {"side": "long", "entry": entry, "sl": sl, "tp": tp}
    else:
        mid = (up_now + dn_now) / 2.0
        side = "上半区" if close >= mid else "下半区"
        sig = Signal("line_wait", "LINES", "通道内等待", f"当前在通道{side}，先等靠线再决策。", False)
        bias = "震荡等待"

    overlay = {
        "upper_fit": up_fit,
        "lower_fit": dn_fit,
        "box_low": box_low,
        "box_high": box_high,
        "bias": bias,
        "lot": lot,
        "suggest_tp": 18.0 if break_up or break_down else 12.0,
        "plan": plan,
    }
    return sig, overlay


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
    m15_bars: list[object] | None = None,
    lot: float | None = None,
) -> Dashboard:
    now = beijing_now(now)
    news = news or get_news_status(now)
    session = session_status(now)
    zone = price_zone(price, box)
    regime, broken = _regime(box, adx, price, m15_close)
    grid = grid or GridState()
    if strategy == "scale_grid":
        signal = evaluate_grid(price, grid, adx, now, news)
        line_overlay = None
    elif strategy == "asia_box_lines":
        line_bars = m15_bars[-120:] if m15_bars else []
        if len(line_bars) < 20:
            signal = Signal("line_wait", "LINES", "画线数据不足", "K线不足，至少需要约 20 根再画线。", False)
            line_overlay = None
        else:
            signal, line_overlay = _line_mode_signal(price, line_bars, clamp_lot(lot))
    else:
        profile = _profile_for(strategy)
        signal = evaluate(
            price,
            box,
            adx,
            m15_close,
            now=now,
            news=news,
            recent_m15=m15_bars,
            profile=profile,
            lot=lot,
        )
        line_overlay = None

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
    elif strategy == "asia_box_lines":
        used_lot = clamp_lot(lot)
        sl_risk = risk_dollars(used_lot, SL_USD)
        indicators_text = (
            f"策略 {_strategy_label(strategy)}  |  时段 {session}  |  位置 {zone}\n"
            f"手数 {used_lot}  单笔止损约 ${sl_risk:.0f}  |  核心：压力/支撑/破位回踩\n"
            f"{kline_line}\n"
            f"ADX {adx_txt}  |  RSI(M15) {rsi_txt}  |  M15收盘 {m15_txt}\n"
            f"建议 {'✓ 可提醒' if entry_ok else '✗ 等待'}"
        )
    else:
        used_lot = clamp_lot(lot)
        sl_risk = risk_dollars(used_lot, SL_USD)
        tp_usd = 18.0 if strategy == "asia_box_sprint" else 10.0 if strategy == "asia_box_hwr" else 12.0
        tp_gain = tp_usd * 100.0 * used_lot
        lot_line = (
            f"手数 {used_lot}  单笔止损约 ${sl_risk:.0f}  止盈约 ${tp_gain:.0f}  "
            f"两连亏约 ${sl_risk * 2:.0f}"
        )
        if used_lot >= 0.05:
            lot_line += "  |  个人日损请放到 $200 内（官方 High Stakes 日损 $500）"
        strat_label = _strategy_label(strategy)
        indicators_text = (
            f"策略 {strat_label}  |  时段 {session}  |  日型 {regime}  |  位置 {zone}\n"
            f"盒子 {box_txt}\n"
            f"{lot_line}\n"
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
        line_overlay=line_overlay,
    )
