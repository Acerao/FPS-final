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
from gold_feed import aggregate_bars

# 画线最低 K 根数：2 点连线只需近期少量K，不必硬等 20 根
MIN_LINE_BARS = 8


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
        "asia_box_lines": "画线策略·大熊式",
        "asia_box_lines_h1": "画线策略·小时级",
        "asia_box_dual_lines_hwr": "双策略·画线 + 高胜率",
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


# ---------- 大熊式画线辅助函数 ----------

def _major_swings(bars: list[object], kind: str, lookback: int = 80) -> list[tuple[int, float]]:
    """找最近几个明显的摆动高/低点（大熊手画风格：只取最显眼的2-4个）。"""
    n = len(bars)
    pts: list[tuple[int, float]] = []
    for i in range(max(2, n - lookback), n - 2):
        val = float(getattr(bars[i], kind))
        l2 = [float(getattr(bars[j], kind)) for j in range(max(0, i - 2), i)]
        r2 = [float(getattr(bars[j], kind)) for j in range(i + 1, min(n, i + 3))]
        if not l2 or not r2:
            continue
        if kind == "high":
            if val >= max(l2) and val >= max(r2) and (val >= bars[i - 1].high + 0.5 or val >= bars[i + 1].high + 0.5):
                pts.append((i, val))
        else:
            if val <= min(l2) and val <= min(r2) and (val <= bars[i - 1].low - 0.5 or val <= bars[i + 1].low - 0.5):
                pts.append((i, val))
    merged: list[tuple[int, float]] = []
    for p in pts:
        if merged and abs(p[0] - merged[-1][0]) <= 5:
            if (kind == "high" and p[1] > merged[-1][1]) or (kind == "low" and p[1] < merged[-1][1]):
                merged[-1] = p
        else:
            merged.append(p)
    return merged[-4:]


def _two_pt_line(p1: tuple[int, float], p2: tuple[int, float]) -> tuple[float, float]:
    x1, y1 = p1; x2, y2 = p2
    a = (y2 - y1) / (x2 - x1) if x2 != x1 else 0.0
    return a, y1 - a * x1


def _line_y(line: tuple[float, float] | None, x: int, fallback: float) -> float:
    if not line:
        return fallback
    return line[0] * x + line[1]


def _fit_line(points: list[tuple[int, float]]) -> tuple[float, float] | None:
    """过首尾两点的直线（供图表渲染用）。"""
    if len(points) < 2:
        return None
    return _two_pt_line(points[0], points[-1])


# 等回踩状态（按周期分桶，进程内持久）
# 注意：绝不能按 UI 刷新次数计时，否则几分钟就过期、多空乱翻。
_pullback_by_tf: dict[str, dict] = {}
_last_closed_ts_by_tf: dict[str, object] = {}
_LINE_LOCK_BARS = 16  # 锁定方向：M15≈4小时 / H1≈16小时
_BREAK_PAD = 0.8  # 收盘要明确越过线，过滤影线假破
_EXTEND_PAD = 2.0  # 破位后至少离开线一段，才算有“第一波”，才允许回踩


def _pullback_bucket(tf: str) -> dict:
    key = (tf or "M15").upper()
    if key not in _pullback_by_tf:
        _pullback_by_tf[key] = {}
    return _pullback_by_tf[key]


# 兼容旧自测：默认指 M15 桶
_pullback_state = _pullback_bucket("M15")
_pullback_clock = [0]  # 仅兼容自测读写；真实超时看 since_ts + K 根数


def _bar_ts(bar: object, fallback_i: int):
    ts = getattr(bar, "ts", None)
    return ts if ts is not None else fallback_i


def _closed_bars_since(bars: list[object], closed_i: int, since_ts) -> int:
    """从锁定时刻起，已经收盘过几根（不含锁定当根）。"""
    if since_ts is None:
        return 0
    n = 0
    for i in range(0, closed_i + 1):
        ts = _bar_ts(bars[i], i)
        try:
            if ts > since_ts:
                n += 1
        except TypeError:
            if i > int(since_ts):
                n += 1
    return n


def _line_sl_tp_from_levels(
    side: str,
    entry: float,
    up_now: float,
    dn_now: float,
    box_low: float,
    box_high: float,
    hi_pts: list[tuple[int, float]],
    lo_pts: list[tuple[int, float]],
) -> tuple[float, float, str] | None:
    """
    用“突破位/压力支撑位”给大熊画线单定 SL/TP。
    空间不够（第一目标太近）则返回 None，表示这单不像大熊该做的。
    """
    channel_w = max(abs(up_now - dn_now), 6.0)
    pad = 0.6
    min_room = 6.0

    if side == "long":
        support_candidates = [dn_now, box_low] + [p for _, p in lo_pts[-4:] if p < entry]
        support = min(support_candidates) if support_candidates else entry - channel_w * 0.5
        sl = min(entry - 3.0, support - pad)

        resist_above = [p for _, p in hi_pts[-6:] if p > entry + min_room]
        if resist_above:
            resist_zone = min(resist_above)
            tp = resist_zone - pad
        else:
            tp = entry + max((entry - sl) * 1.2, min_room)
            resist_zone = tp
        if tp - entry < min_room:
            return None
        reason = f"SL参考支撑 {support:.1f} 下方；TP参考压力 {resist_zone:.1f}"
        return sl, tp, reason

    resist_candidates = [up_now, box_high] + [p for _, p in hi_pts[-4:] if p > entry]
    resistance = max(resist_candidates) if resist_candidates else entry + channel_w * 0.5
    sl = max(entry + 3.0, resistance + pad)

    # 第一目标取“下方最近、但仍有足够空间”的支撑，而不是直接打更远的 RR 目标
    support_below = [p for _, p in lo_pts[-6:] if p < entry - min_room]
    if box_low < entry - min_room:
        support_below.append(box_low)
    if dn_now < entry - min_room:
        support_below.append(dn_now)
    if support_below:
        support_zone = max(support_below)
        tp = support_zone + pad
    else:
        tp = entry - max((sl - entry) * 1.0, min_room)
        support_zone = tp
    if entry - tp < min_room:
        return None
    reason = f"SL参考压力 {resistance:.1f} 上方；TP参考支撑 {support_zone:.1f}"
    return sl, tp, reason


def _line_mode_signal(
    price: float,
    bars: list[object],
    lot: float,
    tf: str = "M15",
) -> tuple[Signal, dict]:
    """
    大熊式信号（稳住版，对齐 PLAYBOOK_LINES）：
    1. 只连最近2个明显高/低点（下降通道）
    2. 至少 2 根收盘越过线才算真突破（影线不算）
    3. 破位后锁定单方向，等回踩/反抽；中途不反向翻多空
    4. 必须先离开线一段（第一波），才允许回踩触发
    5. 回抽失败（收盘重新穿越旧线）或超时（按K根，不按刷新）→ 作废
    """
    tf_key = (tf or "M15").upper()
    n = len(bars)
    # 用已收盘的两根判突破，当前形成中的最后一根只用于现价距离
    if n >= 3:
        closed_i, prev_i = n - 2, n - 3
    else:
        closed_i, prev_i = n - 1, max(0, n - 2)
    close = float(getattr(bars[closed_i], "close"))
    prev_close = float(getattr(bars[prev_i], "close"))
    live = float(price) if price else float(getattr(bars[-1], "close"))
    closed_ts = _bar_ts(bars[closed_i], closed_i)

    hi_pts = _major_swings(bars, "high")
    lo_pts = _major_swings(bars, "low")

    up_line = _two_pt_line(hi_pts[-2], hi_pts[-1]) if len(hi_pts) >= 2 else None
    dn_line = _two_pt_line(lo_pts[-2], lo_pts[-1]) if len(lo_pts) >= 2 else None
    if up_line is None:
        hi_i = max(range(n), key=lambda i: float(getattr(bars[i], "high")))
        hi_j = closed_i
        if hi_i == hi_j and n >= 3:
            hi_i = max(0, closed_i - 3)
        up_line = _two_pt_line((hi_i, float(getattr(bars[hi_i], "high"))), (hi_j, float(getattr(bars[hi_j], "high"))))
    if dn_line is None:
        lo_i = min(range(n), key=lambda i: float(getattr(bars[i], "low")))
        lo_j = closed_i
        if lo_i == lo_j and n >= 3:
            lo_i = max(0, closed_i - 3)
        dn_line = _two_pt_line((lo_i, float(getattr(bars[lo_i], "low"))), (lo_j, float(getattr(bars[lo_j], "low"))))

    up_now = _line_y(up_line, closed_i, max(float(getattr(b, "high")) for b in bars[-20:]))
    dn_now = _line_y(dn_line, closed_i, min(float(getattr(b, "low")) for b in bars[-20:]))
    up_prev = _line_y(up_line, prev_i, up_now)
    dn_prev = _line_y(dn_line, prev_i, dn_now)

    box_low = min(float(getattr(b, "low")) for b in bars[-24:])
    box_high = box_low + (max(float(getattr(b, "high")) for b in bars[-24:]) - box_low) * 0.35

    # 下降通道：最近高点在降；低点也宜在降（更贴近大熊通道）
    descending = len(hi_pts) >= 2 and hi_pts[-1][1] < hi_pts[-2][1]
    lows_descend = len(lo_pts) < 2 or lo_pts[-1][1] <= lo_pts[-2][1] + 1.0
    channel_ok = descending and lows_descend

    # 真突破：连续两根收盘越过线（大熊：影线刺破不算）
    broke_up = (
        channel_ok
        and close > up_now + _BREAK_PAD
        and prev_close > up_prev + _BREAK_PAD
    )
    broke_dn = (
        channel_ok
        and close < dn_now - _BREAK_PAD
        and prev_close < dn_prev - _BREAK_PAD
    )

    pb = _pullback_bucket(tf_key)
    # 兼容自测：同步默认桶引用计数（不参与真实超时）
    if _last_closed_ts_by_tf.get(tf_key) != closed_ts:
        _last_closed_ts_by_tf[tf_key] = closed_ts
        if tf_key == "M15":
            _pullback_clock[0] += 1

    # 失效：回抽失败 / 反向穿越旧线 → 清空；同一次评估不再立刻翻向
    invalidated = False
    if pb.get("side") == "short" and close > float(pb["entry"]) + 4.0:
        pb.clear()
        invalidated = True
    elif pb.get("side") == "long" and close < float(pb["entry"]) - 4.0:
        pb.clear()
        invalidated = True
    elif pb.get("since_ts") is not None:
        wait_bars = _closed_bars_since(bars, closed_i, pb["since_ts"])
        if wait_bars > _LINE_LOCK_BARS:
            pb.clear()
            invalidated = True
    elif pb.get("since") is not None and tf_key == "M15":
        # 旧字段兜底（自测）
        if _pullback_clock[0] - int(pb["since"]) > _LINE_LOCK_BARS:
            pb.clear()
            invalidated = True

    # 未锁定时才接受新破位；已锁定绝不中途翻多空；刚失效当根不立刻开反向
    if not pb.get("side") and not invalidated:
        if broke_up:
            calc = _line_sl_tp_from_levels("long", up_now, up_now, dn_now, box_low, box_high, hi_pts, lo_pts)
            if calc:
                sl, tp, reason = calc
                pb.update(
                    {
                        "side": "long",
                        "entry": round(up_now, 1),
                        "sl": sl,
                        "tp": tp,
                        "reason": reason,
                        "since_ts": closed_ts,
                        "since": _pullback_clock[0],
                        "extended": False,
                        "tf": tf_key,
                    }
                )
        elif broke_dn:
            calc = _line_sl_tp_from_levels("short", dn_now, up_now, dn_now, box_low, box_high, hi_pts, lo_pts)
            if calc:
                sl, tp, reason = calc
                pb.update(
                    {
                        "side": "short",
                        "entry": round(dn_now, 1),
                        "sl": sl,
                        "tp": tp,
                        "reason": reason,
                        "since_ts": closed_ts,
                        "since": _pullback_clock[0],
                        "extended": False,
                        "tf": tf_key,
                    }
                )

    plan: dict | None = None
    tol_pullback = 3.0
    tol_market = 1.5

    if pb.get("side"):
        entry = float(pb["entry"])
        sl = float(pb["sl"])
        tp = float(pb["tp"])
        pb_side = pb["side"]
        reason = pb.get("reason", "")
        wait_bars = _closed_bars_since(bars, closed_i, pb.get("since_ts"))
        # 必须先走出第一波，才允许回踩/反抽触发（大熊：不追第一波）
        if pb_side == "long" and live >= entry + _EXTEND_PAD:
            pb["extended"] = True
        elif pb_side == "short" and live <= entry - _EXTEND_PAD:
            pb["extended"] = True
        extended = bool(pb.get("extended"))

        if pb_side == "long":
            near_line = (entry - tol_pullback) <= live <= (entry + 1.0)
        else:
            near_line = (entry - 1.0) <= live <= (entry + tol_pullback)
        retest = extended and near_line

        if retest:
            if pb_side == "long":
                market_ok = abs(live - entry) <= tol_market and live <= entry + 0.8
                msg = (
                    f"压力变支撑，价格回踩到 {entry:.1f}，现价 {live:.1f}，差 ${abs(live - entry):.1f}\n"
                    + (
                        f"可直接市价做多。SL {sl:.1f}，TP {tp:.1f}，手数 {lot:.2f}。"
                        if market_ok
                        else f"挂 Buy Limit {entry:.1f}，SL {sl:.1f}，TP {tp:.1f}，手数 {lot:.2f}。还没回到线上不要追。"
                    )
                    + (f"\n{reason}" if reason else "")
                )
                sig = Signal("line_long_call", "LINES", "画线做多：回踩到位", msg, True)
                bias = "偏多·回踩触发"
            else:
                market_ok = abs(live - entry) <= tol_market and live >= entry - 0.8
                msg = (
                    f"支撑变压力，价格反抽到 {entry:.1f}，现价 {live:.1f}，差 ${abs(live - entry):.1f}\n"
                    + (
                        f"可直接市价做空。SL {sl:.1f}，TP {tp:.1f}，手数 {lot:.2f}。"
                        if market_ok
                        else f"挂 Sell Limit {entry:.1f}，SL {sl:.1f}，TP {tp:.1f}，手数 {lot:.2f}。还没反抽到线上不要追空。"
                    )
                    + (f"\n{reason}" if reason else "")
                )
                sig = Signal("line_short_call", "LINES", "画线做空：反抽到位", msg, True)
                bias = "偏空·反抽触发"
            plan = {"side": pb_side, "entry": entry, "sl": sl, "tp": tp}
        else:
            if not extended:
                extra = f"先等第一波离开线至少 ${_EXTEND_PAD:.0f}，再等回到线上。"
            else:
                extra = f"已走出第一波，等回到 {entry:.1f}。"
            if pb_side == "long":
                sig = Signal(
                    "line_wait",
                    "LINES",
                    f"已破压力，等回踩 {entry:.1f}",
                    f"现价 {live:.1f}（{tf_key}）。大熊：破线后不追第一波，等回踩旧压力（现支撑）{entry:.1f}。"
                    f"方向已锁定做多，不会中途翻空。{extra}已等 {wait_bars} 根。",
                    False,
                )
                bias = "等回踩"
            else:
                sig = Signal(
                    "line_wait",
                    "LINES",
                    f"已破支撑，等反抽 {entry:.1f}",
                    f"现价 {live:.1f}（{tf_key}）。大熊：破线后不追第一波，等反抽旧支撑（现压力）{entry:.1f}。"
                    f"方向已锁定做空，不会中途翻多。{extra}已等 {wait_bars} 根。",
                    False,
                )
                bias = "等反抽"
            plan = {"side": pb_side, "entry": entry, "sl": sl, "tp": tp}
    elif channel_ok:
        near_up = abs(live - up_now) <= 4.0
        near_dn = abs(live - dn_now) <= 4.0 or (box_low <= live <= box_high)
        if near_up:
            sig = Signal(
                "line_wait",
                "LINES",
                "靠近下降压力，勿追多",
                f"压力线 {up_now:.1f}（{tf_key}）。通道内不做追多；等收盘连续站上再等回踩。",
                False,
            )
            bias = "压力观察"
            plan = {"side": "short", "entry": up_now, "sl": up_now + SL_USD, "tp": up_now - 12.0}
        elif near_dn:
            sig = Signal(
                "line_wait",
                "LINES",
                "靠近下降支撑，观察",
                f"支撑 {dn_now:.1f} / 需求区 {box_low:.1f}–{box_high:.1f}（{tf_key}）。通道内先观察，等收盘跌破再等反抽。",
                False,
            )
            bias = "支撑观察"
            plan = {"side": "long", "entry": dn_now, "sl": dn_now - SL_USD, "tp": dn_now + 12.0}
        else:
            mid = (up_now + dn_now) / 2.0
            zone = "上半区" if live >= mid else "下半区"
            sig = Signal(
                "line_wait",
                "LINES",
                f"下降通道{zone}，等靠线",
                f"压力 {up_now:.1f}  支撑 {dn_now:.1f}  当前 {live:.1f}（{tf_key}）。通道未破，不追。",
                False,
            )
            bias = "震荡等待"
    else:
        sig = Signal(
            "line_wait",
            "LINES",
            "通道不明确，观察",
            f"蓝线只是最近两点连线（上=压力 {up_now:.1f}，下=支撑 {dn_now:.1f}，{tf_key}）。"
            f"当前不是有效下降通道，不做突破单。现价 {live:.1f}。",
            False,
        )
        bias = "观察"

    overlay = {
        "upper_fit": up_line,
        "lower_fit": dn_line,
        "up_now": up_now,
        "dn_now": dn_now,
        "descending": channel_ok,
        "broke_up": broke_up,
        "broke_dn": broke_dn,
        "box_low": box_low,
        "box_high": box_high,
        "bias": bias,
        "lot": lot,
        "suggest_tp": 18.0,
        "plan": plan,
        "n_bars": n,
        "tf": tf_key,
    }
    return sig, overlay


# ---------- 其余保持不变 ----------

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


def _line_bars_for_tf(m15_bars: list[object] | None, line_tf: str) -> tuple[list[object], str]:
    """按可选项准备画线用 K：M15 直接用；H1 由 M15 聚合。"""
    tf = (line_tf or "M15").upper()
    if tf == "H1":
        raw = m15_bars[-240:] if m15_bars else []
        return aggregate_bars(raw, 60), "H1"
    bars = m15_bars[-120:] if m15_bars else []
    return bars, "M15"


def _line_signal_from_bars(
    price: float,
    line_bars: list[object],
    lot: float,
    tf: str,
) -> tuple[Signal, dict | None]:
    if len(line_bars) < MIN_LINE_BARS:
        return (
            Signal(
                "line_wait",
                "LINES",
                "画线数据不足",
                f"近期 {tf} 仅 {len(line_bars)} 根，至少 {MIN_LINE_BARS} 根即可用近期画线。",
                False,
            ),
            None,
        )
    return _line_mode_signal(price, line_bars, clamp_lot(lot), tf=tf)


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
    line_tf: str = "M15",
) -> Dashboard:
    now = beijing_now(now)
    news = news or get_news_status(now)
    session = session_status(now)
    zone = price_zone(price, box)
    regime, broken = _regime(box, adx, price, m15_close)
    grid = grid or GridState()
    line_close: float | None = None
    effective_line_tf = "M15"
    if strategy == "scale_grid":
        signal = evaluate_grid(price, grid, adx, now, news)
        line_overlay = None
    elif strategy == "asia_box_lines":
        line_bars, effective_line_tf = _line_bars_for_tf(m15_bars, line_tf)
        if line_bars:
            line_close = float(getattr(line_bars[-1], "close", m15_close or 0)) or m15_close
        signal, line_overlay = _line_signal_from_bars(price, line_bars, clamp_lot(lot), effective_line_tf)
    elif strategy == "asia_box_lines_h1":
        # 兼容旧入口：强制 H1；日常更推荐用 asia_box_lines + 画线周期可选项
        line_bars, effective_line_tf = _line_bars_for_tf(m15_bars, "H1")
        if line_bars:
            line_close = float(getattr(line_bars[-1], "close", None) or 0) or None
        signal, line_overlay = _line_signal_from_bars(price, line_bars, clamp_lot(lot), "H1")
    elif strategy == "asia_box_dual_lines_hwr":
        used_lot = clamp_lot(lot)
        line_bars, effective_line_tf = _line_bars_for_tf(m15_bars, line_tf)
        if line_bars:
            line_close = float(getattr(line_bars[-1], "close", m15_close or 0)) or m15_close
        line_sig, line_overlay = _line_signal_from_bars(price, line_bars, used_lot, effective_line_tf)

        # 2) 高胜率（HWR）
        profile = _profile_for("asia_box_hwr")
        hwr_sig = evaluate(
            price,
            box,
            adx,
            m15_close,
            now=now,
            news=news,
            recent_m15=m15_bars,
            profile=profile,
            lot=used_lot,
        )

        line_is_entry = line_sig.key in ENTRY_KEYS
        hwr_is_entry = hwr_sig.key in ENTRY_KEYS

        # 选一个“真正能提醒入场”的信号作为主提醒
        if line_is_entry and hwr_is_entry:
            # 同时触发：优先返回高胜率 key，但消息里把两者都写清楚
            signal = Signal(
                hwr_sig.key,
                _strategy_label(strategy),
                "双触发：画线 + 高胜率",
                f"[画线/{effective_line_tf}] {line_sig.title}\n{line_sig.message}\n\n[高胜率] {hwr_sig.title}\n{hwr_sig.message}",
                True,
            )
        elif hwr_is_entry:
            signal = Signal(
                hwr_sig.key,
                hwr_sig.mode,
                f"高胜率：{hwr_sig.title}",
                f"[高胜率] {hwr_sig.message}\n\n[画线/{effective_line_tf}观察] {line_sig.title}。两套都在跑，先按高胜率限价，不成交不要追。",
                True,
            )
        elif line_is_entry:
            signal = Signal(
                line_sig.key,
                line_sig.mode,
                f"画线：{line_sig.title}",
                f"[画线/{effective_line_tf}] {line_sig.message}\n\n[高胜率不同意见] {hwr_sig.title}。{hwr_sig.message}\n"
                f"高胜率这时不做。画线那笔也是限价回踩，现价没回到入场位就不要市价追。",
                True,
            )
        else:
            # 都未入场：优先返回高胜率提示（若需要也能看到画线提示）
            signal = Signal(
                "dual_wait",
                _strategy_label(strategy),
                "双策略：等待入场条件",
                f"[画线/{effective_line_tf}] {line_sig.title}\n{line_sig.message}\n\n[高胜率] {hwr_sig.title}\n{hwr_sig.message}",
                False,
            )
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
    line_txt = f"{line_close:.2f}" if line_close is not None else m15_txt
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
    elif strategy == "asia_box_lines" or strategy == "asia_box_lines_h1":
        used_lot = clamp_lot(lot)
        sl_risk = risk_dollars(used_lot, SL_USD)
        indicators_text = (
            f"策略 {_strategy_label(strategy)}  |  时段 {session}  |  位置 {zone}\n"
            f"手数 {used_lot}  单笔止损约 ${sl_risk:.0f}  |  画线周期 {effective_line_tf}  |  2点连线+破位收盘+等回踩\n"
            f"{kline_line}\n"
            f"ADX {adx_txt}  |  RSI(M15) {rsi_txt}  |  {effective_line_tf}收盘 {line_txt}\n"
            f"建议 {'✓ 可提醒' if entry_ok else '✗ 等待'}"
        )
    elif strategy == "asia_box_dual_lines_hwr":
        used_lot = clamp_lot(lot)
        sl_risk = risk_dollars(used_lot, SL_USD)
        indicators_text = (
            f"策略 {_strategy_label(strategy)}  |  时段 {session}  |  位置 {zone}\n"
            f"手数 {used_lot}  单笔止损约 ${sl_risk:.0f}\n"
            f"同时运行：\n"
            f"- 画线（{effective_line_tf}，可选项；大熊式：2根收盘破+方向锁定+等回踩）\n"
            f"- 高胜率（M15 亚盘盒子）\n"
            f"{kline_line}\n"
            f"ADX {adx_txt}  |  RSI(M15) {rsi_txt}  |  画线{effective_line_tf} {line_txt}  |  M15 {m15_txt}\n"
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
