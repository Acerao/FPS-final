"""Long-sample Asia Box backtest: classic vs high-winrate.

Uses Yahoo GC=F H1 (~2y) because public M15 only goes ~60 days.
H1 is a coarser proxy (box/ADX/确认K 都按小时，不是软件里的 M15)。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from gold_feed import fetch_gc_bars
from strategy import (
    ADX_RANGE_MAX,
    ADX_TREND_MIN,
    HWR_TP_USD,
    SL_USD,
    SPRINT_TP_USD,
    TP_USD,
    _m15_confirmation,
    clamp_lot,
    compute_adx,
)
from tzutil import BEIJING

ASIA_START = time(8, 0)
ASIA_END = time(14, 30)
ENTRY_START = time(15, 0)
ENTRY_END = time(1, 0)
FLAT_END = time(1, 45)
A_OFFSET = 5.0
LOT = 0.02
BE_USD = 8.0
MAX_DAY_TRADES = 2
MAX_DAY_LOSSES = 2
MAX_DAY_LOSS_USD = 100.0


def session_date(ts: datetime):
    ts = ts.astimezone(BEIJING)
    if ts.time() < ASIA_START:
        return (ts - timedelta(days=1)).date()
    return ts.date()


def in_asia_box(t: time) -> bool:
    return ASIA_START <= t <= ASIA_END


def in_entry(t: time) -> bool:
    return t >= ENTRY_START or t < ENTRY_END


def in_manage(t: time) -> bool:
    return t >= ASIA_END or t < FLAT_END


def hit_level(bar, level: float) -> bool:
    return bar.low <= level <= bar.high


def mfe(bar, side: str, entry: float) -> float:
    if side == "long":
        return bar.high - entry
    return entry - bar.low


def exit_open(bar, side: str, sl: float, tp: float, be_sl: float | None) -> tuple[float, str] | None:
    stop = be_sl if be_sl is not None else sl
    if side == "long":
        hit_sl = bar.low <= stop
        hit_tp = bar.high >= tp
        if hit_sl and hit_tp:
            return stop, "SL优先(同根)"
        if hit_sl:
            return stop, "SL"
        if hit_tp:
            return tp, "TP"
    else:
        hit_sl = bar.high >= stop
        hit_tp = bar.low <= tp
        if hit_sl and hit_tp:
            return stop, "SL优先(同根)"
        if hit_sl:
            return stop, "SL"
        if hit_tp:
            return tp, "TP"
    return None


@dataclass
class Trade:
    day: object
    kind: str
    side: str
    entry: float
    sl: float
    tp: float
    entry_ts: datetime
    exit_ts: datetime
    exit: float
    reason: str
    profile: str
    lot: float = 0.02

    @property
    def pnl_usd_price(self) -> float:
        if self.side == "long":
            return self.exit - self.entry
        return self.entry - self.exit

    @property
    def pnl_account(self) -> float:
        return self.pnl_usd_price * 100.0 * self.lot

    @property
    def win(self) -> bool:
        return self.pnl_usd_price > 0.05

    @property
    def year(self) -> int:
        return self.entry_ts.astimezone(BEIJING).year


def _confirm(hist: list, side: str, need: bool) -> bool:
    if not need:
        return True
    ok, _ = _m15_confirmation(hist, side)
    return ok


def simulate(bars: list, profile: str, lot: float = LOT) -> list[Trade]:
    lot = clamp_lot(lot)
    need_confirm = profile in {"high_winrate", "sprint"}
    skip_a = profile == "sprint"
    if profile == "sprint":
        tp_usd = SPRINT_TP_USD
    elif need_confirm:
        tp_usd = HWR_TP_USD
    else:
        tp_usd = TP_USD
    by_day: dict = defaultdict(list)
    for b in bars:
        by_day[session_date(b.ts)].append(b)
    days = sorted(by_day)
    trades: list[Trade] = []
    all_so_far: list = []

    for i, day in enumerate(days):
        day_bars = sorted(by_day[day], key=lambda x: x.ts)
        asia = [b for b in day_bars if in_asia_box(b.ts.astimezone(BEIJING).time())]
        if len(asia) < 4:
            all_so_far.extend(day_bars)
            continue
        box_h = max(b.high for b in asia)
        box_l = min(b.low for b in asia)
        rng = box_h - box_l
        if rng < 8:
            all_so_far.extend(day_bars)
            continue

        hist = all_so_far + asia
        adx = compute_adx([b.high for b in hist], [b.low for b in hist], [b.close for b in hist])
        adx_val = adx.adx if adx else None
        last_asia_close = asia[-1].close
        already_break = last_asia_close > box_h or last_asia_close < box_l
        allow_a = (not skip_a) and (adx_val is None or adx_val < ADX_RANGE_MAX) and not already_break
        trend_day = adx_val is not None and adx_val >= ADX_TREND_MIN

        after = [b for b in day_bars if b.ts > asia[-1].ts]
        if i + 1 < len(days):
            nxt = sorted(by_day[days[i + 1]], key=lambda x: x.ts)
            after += [b for b in nxt if b.ts.astimezone(BEIJING).time() < ASIA_START]

        buy_lim = box_l + A_OFFSET
        sell_lim = box_h - A_OFFSET
        pending_a_buy = allow_a
        pending_a_sell = allow_a
        pending_b = None
        open_tr = None
        day_trade_n = 0
        day_loss_n = 0
        day_pnl = 0.0
        mode = "B" if (trend_day or already_break) else "A"
        if already_break or trend_day:
            pending_a_buy = pending_a_sell = False
            if last_asia_close > box_h or (adx and adx.plus_di > adx.minus_di and trend_day):
                pending_b = ("long", box_h)
            elif last_asia_close < box_l or (adx and adx.minus_di > adx.plus_di and trend_day):
                pending_b = ("short", box_l)
        b_used = False
        a_stopped = False
        walked = list(hist)

        for b in after:
            t = b.ts.astimezone(BEIJING).time()
            walked.append(b)
            if not in_manage(t) and t >= ASIA_START:
                break
            if t >= FLAT_END and t < ASIA_START:
                if open_tr:
                    side, entry, sl, tp, ets, mfe_max, be = open_tr
                    trades.append(Trade(day, mode, side, entry, sl, tp, ets, b.ts, b.open, "01:45平", profile, lot))
                    open_tr = None
                break

            if open_tr:
                side, entry, sl, tp, ets, mfe_max, be = open_tr
                mfe_max = max(mfe_max, mfe(b, side, entry))
                if mfe_max >= BE_USD:
                    be = entry
                held_min = (b.ts - ets).total_seconds() / 60
                if held_min >= 90 and mfe_max < 5:
                    tr = Trade(day, mode, side, entry, sl, tp, ets, b.ts, b.close, "90分钟没动", profile, lot)
                    trades.append(tr)
                    day_pnl += tr.pnl_account
                    if tr.pnl_usd_price < 0:
                        day_loss_n += 1
                    open_tr = None
                    day_trade_n += 1
                    pending_a_buy = pending_a_sell = False
                    if day_loss_n >= MAX_DAY_LOSSES or day_trade_n >= MAX_DAY_TRADES or day_pnl <= -MAX_DAY_LOSS_USD:
                        break
                    continue
                hit = exit_open(b, side, sl, tp, be)
                if hit:
                    px, why = hit
                    tr = Trade(day, mode, side, entry, sl, tp, ets, b.ts, px, why, profile, lot)
                    trades.append(tr)
                    day_pnl += tr.pnl_account
                    if tr.pnl_usd_price < 0:
                        day_loss_n += 1
                        a_stopped = mode == "A"
                    open_tr = None
                    day_trade_n += 1
                    pending_a_buy = pending_a_sell = False
                    if day_loss_n >= MAX_DAY_LOSSES or day_trade_n >= MAX_DAY_TRADES or day_pnl <= -MAX_DAY_LOSS_USD:
                        break
                else:
                    open_tr = (side, entry, sl, tp, ets, mfe_max, be)
                continue

            if t >= ENTRY_END and t < FLAT_END:
                pending_a_buy = pending_a_sell = False
                pending_b = None
                continue
            if not in_entry(t):
                continue

            if b.close > box_h:
                pending_a_buy = pending_a_sell = False
                mode = "B"
                if not b_used and pending_b is None:
                    pending_b = ("long", box_h)
            elif b.close < box_l:
                pending_a_buy = pending_a_sell = False
                mode = "B"
                if not b_used and pending_b is None:
                    pending_b = ("short", box_l)

            if day_trade_n >= MAX_DAY_TRADES or day_loss_n >= MAX_DAY_LOSSES or day_pnl <= -MAX_DAY_LOSS_USD:
                break
            if a_stopped and mode != "B":
                break

            filled = None
            if pending_b and not b_used:
                side, lim = pending_b
                if hit_level(b, lim) and _confirm(walked, side, need_confirm):
                    filled = (side, lim, "B")
                    pending_b = None
                    b_used = True
            if filled is None and mode == "A":
                buy_hit = pending_a_buy and hit_level(b, buy_lim) and _confirm(walked, "long", need_confirm)
                sell_hit = pending_a_sell and hit_level(b, sell_lim) and _confirm(walked, "short", need_confirm)
                if buy_hit and sell_hit:
                    continue
                if buy_hit:
                    filled = ("long", buy_lim, "A")
                    pending_a_buy = pending_a_sell = False
                elif sell_hit:
                    filled = ("short", sell_lim, "A")
                    pending_a_buy = pending_a_sell = False

            if filled:
                side, entry, kind = filled
                mode = kind
                sl = entry - SL_USD if side == "long" else entry + SL_USD
                tp = entry + tp_usd if side == "long" else entry - tp_usd
                open_tr = (side, entry, sl, tp, b.ts, mfe(b, side, entry), None)

        if open_tr:
            side, entry, sl, tp, ets, mfe_max, be = open_tr
            last = after[-1] if after else asia[-1]
            trades.append(Trade(day, mode, side, entry, sl, tp, ets, last.ts, last.close, "数据结束平", profile, lot))
        all_so_far.extend(day_bars)
    return trades


def summarize(name: str, trades: list[Trade], bars: list) -> None:
    n = len(trades)
    wins = [t for t in trades if t.win]
    losses = [t for t in trades if not t.win and abs(t.pnl_usd_price) > 0.05]
    flats = [t for t in trades if abs(t.pnl_usd_price) <= 0.05]
    wr = 100 * len(wins) / n if n else 0
    decisive_n = len(wins) + len(losses)
    wr_dec = 100 * len(wins) / decisive_n if decisive_n else 0
    avg_w = sum(t.pnl_usd_price for t in wins) / len(wins) if wins else 0
    avg_l = sum(t.pnl_usd_price for t in losses) / len(losses) if losses else 0
    gross_w = sum(t.pnl_usd_price for t in wins)
    gross_l = abs(sum(t.pnl_usd_price for t in losses))
    pf = gross_w / gross_l if gross_l else float("inf")
    acc = sum(t.pnl_account for t in trades)

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        equity += t.pnl_account
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)

    print(f"\n=== {name} ===")
    print(f"数据: Yahoo GC=F H1  {bars[0].ts:%Y-%m-%d} → {bars[-1].ts:%Y-%m-%d}  共{len(bars)}根")
    print(f"总笔数 {n}  盈利 {len(wins)}  亏损 {len(losses)}  持平 {len(flats)}")
    print(f"含持平胜率 {wr:.1f}%  剔持平(只计盈亏) {wr_dec:.1f}%")
    print(f"平均盈利 ${avg_w:.2f}/盎司  平均亏损 ${avg_l:.2f}/盎司")
    used_lot = trades[0].lot if trades else LOT
    print(f"利润因子 {pf:.2f}  账户粗算(手数 {used_lot}) ${acc:.0f}")
    print(f"最大回撤(账户) ${max_dd:.0f}  有成交天数 {len({t.day for t in trades})}")

    by_year: dict[int, list[Trade]] = defaultdict(list)
    for t in trades:
        by_year[t.year].append(t)
    y24 = sum(t.pnl_account for t in by_year.get(2024, []))
    y25 = sum(t.pnl_account for t in by_year.get(2025, []))
    print(f"前年+去年(2024+2025) 净 ${y24 + y25:.0f}  （2024 ${y24:.0f} + 2025 ${y25:.0f}）")
    print("分年:")
    for y in sorted(by_year):
        ts = by_year[y]
        w = sum(1 for x in ts if x.win)
        pnl = sum(x.pnl_account for x in ts)
        print(f"  {y}: {len(ts)}笔  胜率 {100*w/len(ts):.0f}%  净 ${pnl:.0f}")

    by_kind = defaultdict(list)
    by_why = defaultdict(int)
    for t in trades:
        by_kind[t.kind].append(t)
        by_why[t.reason] += 1
    for k, ts in by_kind.items():
        w = sum(1 for x in ts if x.win)
        print(f"  {k}: {len(ts)}笔 胜率 {100*w/len(ts):.0f}%  净 ${sum(x.pnl_account for x in ts):.0f}")
    print("平仓原因:", dict(by_why))

    # monthly for context
    by_ym: dict[str, float] = defaultdict(float)
    for t in trades:
        key = t.entry_ts.astimezone(BEIJING).strftime("%Y-%m")
        by_ym[key] += t.pnl_account
    pos_m = sum(1 for v in by_ym.values() if v > 0)
    neg_m = sum(1 for v in by_ym.values() if v < 0)
    print(f"月份: {len(by_ym)}  正收益月 {pos_m}  负收益月 {neg_m}")
    worst = min(by_ym.items(), key=lambda kv: kv[1]) if by_ym else ("", 0)
    best = max(by_ym.items(), key=lambda kv: kv[1]) if by_ym else ("", 0)
    print(f"最好月 {best[0]} ${best[1]:.0f}  最差月 {worst[0]} ${worst[1]:.0f}")


def buy_hold(bars: list) -> None:
    first = next((b.close for b in bars if b.close), None)
    last = next((b.close for b in reversed(bars) if b.close), None)
    if not first or not last:
        return
    move = last - first
    print("\n=== 同期黄金本身 ===")
    print(f"GC=F 从 {first:.1f} 到 {last:.1f}，涨了 ${move:.0f}（约 {100*move/first:.0f}%）")
    print("这是单边牛市背景：盒子均值回归（A空）会更难，顺势回踩（B多）相对容易。")


def _fit_line_bt(points: list[tuple[int, float]]) -> tuple[float, float] | None:
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


def _swing_hi(bars, i):
    if i < 2 or i >= len(bars) - 2:
        return False
    return bars[i].high >= bars[i-1].high and bars[i].high >= bars[i+1].high


def _swing_lo(bars, i):
    if i < 2 or i >= len(bars) - 2:
        return False
    return bars[i].low <= bars[i-1].low and bars[i].low <= bars[i+1].low


def _two_point_line(p1: tuple[int, float], p2: tuple[int, float]) -> tuple[float, float]:
    """斜率截距：过两点的直线（大熊手画只连2-3个明显高/低点）。"""
    x1, y1 = p1
    x2, y2 = p2
    a = (y2 - y1) / (x2 - x1) if x2 != x1 else 0.0
    b = y1 - a * x1
    return a, b


def _find_major_swings(bars: list, kind: str, lookback: int = 80) -> list[tuple[int, float]]:
    """找最近几个明显的摆动高/低点（大熊式：只取最显眼的2-4个）。"""
    pts: list[tuple[int, float]] = []
    n = len(bars)
    # 用比较宽的左右窗口找真正明显的高低点（避免噪音）
    for i in range(max(2, n - lookback), n - 2):
        left = bars[i - 2: i]
        right = bars[i + 1: i + 3]
        if not left or not right:
            continue
        val = getattr(bars[i], kind)
        lvals = [getattr(b, kind) for b in left]
        rvals = [getattr(b, kind) for b in right]
        if kind == "high":
            if val >= max(lvals) and val >= max(rvals):
                # 额外要求比前后各1根都明显高（过滤微小波动）
                if i >= 1 and i < n - 1:
                    if val >= bars[i-1].high + 0.5 or val >= bars[i+1].high + 0.5:
                        pts.append((i, float(val)))
        else:
            if val <= min(lvals) and val <= min(rvals):
                if i >= 1 and i < n - 1:
                    if val <= bars[i-1].low - 0.5 or val <= bars[i+1].low - 0.5:
                        pts.append((i, float(val)))
    # 去重：相邻5根内只取最极端的一个
    merged: list[tuple[int, float]] = []
    for p in pts:
        if merged and abs(p[0] - merged[-1][0]) <= 5:
            if kind == "high":
                merged[-1] = p if p[1] > merged[-1][1] else merged[-1]
            else:
                merged[-1] = p if p[1] < merged[-1][1] else merged[-1]
        else:
            merged.append(p)
    return merged[-4:]  # 只取最近4个


def simulate_lines_v2(bars: list, lot: float = 0.05) -> list[Trade]:
    """
    正确还原大熊式画线回测：
    1. 只连最近2-3个明显高点（下降压力线）/低点（下降支撑线）
    2. H8 收盘突破压力线 → 等价格回踩旧压力线附近（压力变支撑）再入场
    3. 入场用限价回踩，不追突破当根
    4. 同理跌破支撑→等反抽再空
    5. SL $15 / TP $18，手数可调
    """
    lot = clamp_lot(lot)
    tp_usd = 18.0
    # 通道检测需要至少 lookback 根历史
    lookback = 80
    trades: list[Trade] = []
    open_tr = None
    # 突破后等待回踩的状态
    pending_pullback = None  # ("long"/"short", entry_level, sl, tp, since_idx)
    pullback_max_bars = 15   # 最多等15根H1，否则放弃
    day_trade_count: dict = defaultdict(int)
    day_loss_count: dict = defaultdict(int)
    day_pnl: dict[object, float] = defaultdict(float)

    for idx in range(lookback, len(bars)):
        b = bars[idx]
        t = b.ts.astimezone(BEIJING).time()
        day = session_date(b.ts)
        is_entry_time = in_entry(t)
        is_flat_time = (t >= FLAT_END and t < ASIA_START)

        # ---- 日损/日限 ----
        over_daily = (
            day_trade_count[day] >= MAX_DAY_TRADES
            or day_loss_count[day] >= MAX_DAY_LOSSES
            or day_pnl[day] <= -MAX_DAY_LOSS_USD
        )

        # ---- 持仓管理 ----
        if open_tr:
            side, entry, sl, tp, ets, mfe_max, be = open_tr
            mfe_max = max(mfe_max, mfe(b, side, entry))
            if mfe_max >= BE_USD:
                be = entry
            if is_flat_time:
                tr = Trade(day, "LINES", side, entry, sl, tp, ets, b.ts, b.open, "01:45平", "lines_v2", lot)
                trades.append(tr)
                day_pnl[day] += tr.pnl_account
                open_tr = None
                pending_pullback = None
                continue
            hit_r = exit_open(b, side, sl, tp, be)
            if hit_r:
                px, why = hit_r
                tr = Trade(day, "LINES", side, entry, sl, tp, ets, b.ts, px, why, "lines_v2", lot)
                trades.append(tr)
                day_pnl[day] += tr.pnl_account
                day_trade_count[day] += 1
                if tr.pnl_usd_price < 0:
                    day_loss_count[day] += 1
                open_tr = None
                pending_pullback = None
            else:
                open_tr = (side, entry, sl, tp, ets, mfe_max, be)
            continue

        if over_daily or not is_entry_time:
            pending_pullback = None
            continue

        chunk = bars[idx - lookback: idx + 1]
        cn = len(chunk)

        # ---- 等回踩入场 ----
        if pending_pullback is not None:
            pb_side, pb_entry, pb_sl, pb_tp, pb_since = pending_pullback
            if idx - pb_since > pullback_max_bars:
                pending_pullback = None  # 等太久放弃
            else:
                # 回踩判断：价格碰到旧压力线附近（±$3）
                if abs(b.low - pb_entry) <= 3.0 and pb_side == "long":
                    open_tr = ("long", pb_entry, pb_sl, pb_tp, b.ts, mfe(b, "long", pb_entry), None)
                    pending_pullback = None
                    continue
                if abs(b.high - pb_entry) <= 3.0 and pb_side == "short":
                    open_tr = ("short", pb_entry, pb_sl, pb_tp, b.ts, mfe(b, "short", pb_entry), None)
                    pending_pullback = None
                    continue

        # ---- 检测突破 ----
        hi_pts = _find_major_swings(chunk, "high")
        lo_pts = _find_major_swings(chunk, "low")
        if len(hi_pts) < 2 or len(lo_pts) < 2:
            continue

        # 大熊式：只连最近2个高点
        p1h, p2h = hi_pts[-2], hi_pts[-1]
        # 要求是下降通道：高点依次降低
        if p2h[1] >= p1h[1]:
            # 不是下降压力线，检查是否是上升支撑线（另一类做法）
            # 评估期简化：不管上升通道，只做下降通道
            continue
        up_line = _two_point_line(p1h, p2h)

        p1l, p2l = lo_pts[-2], lo_pts[-1]
        dn_line = _two_point_line(p1l, p2l)

        last_x = cn - 1
        up_now = up_line[0] * last_x + up_line[1]
        dn_now = dn_line[0] * last_x + dn_line[1]
        prev_x = last_x - 1
        up_prev = up_line[0] * prev_x + up_line[1]

        close = b.close
        prev_close = bars[idx - 1].close

        # 收盘突破（不是影线）
        broke_up = close > up_now and prev_close <= up_prev
        broke_dn = close < dn_now and prev_close >= (dn_line[0] * prev_x + dn_line[1])

        if broke_up and pending_pullback is None:
            # 大熊式：压力变支撑，等回踩 up_now
            entry_lv = up_now
            pending_pullback = ("long", entry_lv, entry_lv - SL_USD, entry_lv + tp_usd, idx)
        elif broke_dn and pending_pullback is None:
            entry_lv = dn_now
            pending_pullback = ("short", entry_lv, entry_lv + SL_USD, entry_lv - tp_usd, idx)

    if open_tr:
        side, entry, sl, tp, ets, mfe_max, be = open_tr
        last = bars[-1]
        trades.append(Trade(session_date(last.ts), "LINES", side, entry, sl, tp, ets,
                            last.ts, last.close, "数据结束平", "lines_v2", lot))
    return trades


def run() -> None:
    bars, _ = fetch_gc_bars("1h", "730d")
    buy_hold(bars)
    classic = simulate(bars, "classic", 0.02)
    hwr = simulate(bars, "high_winrate", 0.02)
    sprint = simulate(bars, "sprint", 0.05)
    lines_v2 = simulate_lines_v2(bars, 0.05)      # 正确还原大熊式
    summarize("原版盒子 classic  SL15/TP12  0.02手", classic, bars)
    summarize("高胜率版 hwr  SL15/TP10  0.02手  要确认K", hwr, bars)
    summarize("冲刺版 sprint  只做B  SL15/TP18  0.05手  要确认K", sprint, bars)
    summarize("画线(正确版v2) 大熊式下降通道破位+回踩  SL15/TP18  0.05手", lines_v2, bars)

    print("\n\n========== 策略胜率对比 ==========")
    all_runs = [
        ("asia_box", classic),
        ("asia_box_hwr", hwr),
        ("asia_box_sprint", sprint),
        ("lines_v2(大熊式)", lines_v2),
    ]
    print(f"{'策略':<22} {'笔数':>5} {'含平胜率':>8} {'剔平胜率':>8} {'净利':>8} {'最大回撤':>8}")
    for name, ts in all_runs:
        n = len(ts)
        w = sum(1 for t in ts if t.win)
        l = sum(1 for t in ts if not t.win and abs(t.pnl_usd_price) > 0.05)
        dec = w + l
        wr = 100 * w / n if n else 0
        wr_d = 100 * w / dec if dec else 0
        acc = sum(t.pnl_account for t in ts)
        eq = 0.0; pk = 0.0; dd = 0.0
        for t in ts:
            eq += t.pnl_account; pk = max(pk, eq); dd = min(dd, eq - pk)
        print(f"{name:<22} {n:>5} {wr:>7.1f}% {wr_d:>7.1f}% {acc:>+7.0f}$ {dd:>+7.0f}$")

    print("\n注意:")
    print("- 这是 COMEX 黄金期货 H1，不是 MT5 XAUUSD M15，点差/滑点/大数据都没扣。")
    print("- 确认K在 H1 上比 M15 更稀，高胜率版笔数会偏少。")
    print("- 手数按所选 lot 计算，没有把盈利再加仓。不能当未来承诺。")


if __name__ == "__main__":
    run()
