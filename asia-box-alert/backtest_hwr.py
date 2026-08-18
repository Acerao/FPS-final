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
    TP_USD,
    _m15_confirmation,
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
USD_PER_DOLLAR = 100 * LOT  # $2 per $1 at 0.02 lot
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

    @property
    def pnl_usd_price(self) -> float:
        if self.side == "long":
            return self.exit - self.entry
        return self.entry - self.exit

    @property
    def pnl_account(self) -> float:
        return self.pnl_usd_price * USD_PER_DOLLAR

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


def simulate(bars: list, profile: str) -> list[Trade]:
    need_confirm = profile == "high_winrate"
    tp_usd = HWR_TP_USD if need_confirm else TP_USD
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
        allow_a = (adx_val is None or adx_val < ADX_RANGE_MAX) and not already_break
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
                    trades.append(Trade(day, mode, side, entry, sl, tp, ets, b.ts, b.open, "01:45平", profile))
                    open_tr = None
                break

            if open_tr:
                side, entry, sl, tp, ets, mfe_max, be = open_tr
                mfe_max = max(mfe_max, mfe(b, side, entry))
                if mfe_max >= BE_USD:
                    be = entry
                held_min = (b.ts - ets).total_seconds() / 60
                if held_min >= 90 and mfe_max < 5:
                    tr = Trade(day, mode, side, entry, sl, tp, ets, b.ts, b.close, "90分钟没动", profile)
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
                    tr = Trade(day, mode, side, entry, sl, tp, ets, b.ts, px, why, profile)
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
            trades.append(Trade(day, mode, side, entry, sl, tp, ets, last.ts, last.close, "数据结束平", profile))
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
    print(f"利润因子 {pf:.2f}  账户粗算(固定0.02手) ${acc:.0f}")
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


def run() -> None:
    bars, _ = fetch_gc_bars("1h", "730d")
    buy_hold(bars)
    classic = simulate(bars, "classic")
    hwr = simulate(bars, "high_winrate")
    summarize("原版盒子 classic  SL15/TP12  无确认K", classic, bars)
    summarize("高胜率版 hwr  SL15/TP10  要确认K", hwr, bars)
    print("\n注意:")
    print("- 这是 COMEX 黄金期货 H1，不是 MT5 XAUUSD M15，点差/滑点/大数据都没扣。")
    print("- 确认K在 H1 上比 M15 更稀，高胜率版笔数会偏少。")
    print("- 手数固定 0.02，没有把盈利加仓。不能当未来承诺。")


if __name__ == "__main__":
    run()
