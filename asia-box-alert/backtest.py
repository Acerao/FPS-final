"""Rough Asia Box backtest on Yahoo GC=F M15 (not MT5 XAUUSD)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, time

from gold_feed import fetch_gc_bars
from strategy import ADX_RANGE_MAX, ADX_TREND_MIN, compute_adx
from tzutil import BEIJING

ASIA_START = time(8, 0)
ASIA_END = time(14, 30)
ENTRY_START = time(15, 0)
ENTRY_END = time(1, 0)
FLAT_END = time(1, 45)
SL_USD = 15.0
TP_USD = 22.0
A_OFFSET = 5.0
LOT = 0.02
USD_PER_DOLLAR = 100 * LOT  # $2 per $1 move at 0.02 lot on XAUUSD


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
    """After lock until flatten."""
    return t >= ASIA_END or t < FLAT_END


def must_flat(t: time) -> bool:
    return ENTRY_END <= t < time(8, 0) and t >= FLAT_END or (ENTRY_END <= t <= FLAT_END and t >= FLAT_END)


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


def hit_level(bar, level: float) -> bool:
    return bar.low <= level <= bar.high


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


def mfe(bar, side: str, entry: float) -> float:
    if side == "long":
        return bar.high - entry
    return entry - bar.low


def run() -> None:
    bars, _ = fetch_gc_bars("15m", "60d")
    by_day: dict = defaultdict(list)
    for b in bars:
        by_day[session_date(b.ts)].append(b)
    days = sorted(by_day)
    trades: list[Trade] = []
    skipped = defaultdict(int)

    all_so_far: list = []
    for i, day in enumerate(days):
        day_bars = sorted(by_day[day], key=lambda x: x.ts)
        asia = [b for b in day_bars if in_asia_box(b.ts.astimezone(BEIJING).time())]
        if len(asia) < 8:
            skipped["亚盘K线不足"] += 1
            all_so_far.extend(day_bars)
            continue
        box_h = max(b.high for b in asia)
        box_l = min(b.low for b in asia)
        rng = box_h - box_l
        if rng < 8:
            skipped["盒子过窄"] += 1
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
        # include next calendar morning until 01:45 BJ (same session_date already groups before 08:00)
        if i + 1 < len(days):
            nxt = sorted(by_day[days[i + 1]], key=lambda x: x.ts)
            after += [b for b in nxt if b.ts.astimezone(BEIJING).time() < ASIA_START]

        buy_lim = box_l + A_OFFSET
        sell_lim = box_h - A_OFFSET
        pending_a_buy = allow_a
        pending_a_sell = allow_a
        pending_b = None  # ("long", price) or ("short", price)
        open_tr = None
        day_trade_n = 0
        day_loss_n = 0
        mode = "B" if (trend_day or already_break) else "A"
        if already_break or trend_day:
            pending_a_buy = pending_a_sell = False
            if last_asia_close > box_h or (adx and adx.plus_di > adx.minus_di and trend_day):
                pending_b = ("long", box_h)
            elif last_asia_close < box_l or (adx and adx.minus_di > adx.plus_di and trend_day):
                pending_b = ("short", box_l)
        b_used = False
        a_stopped = False

        for b in after:
            t = b.ts.astimezone(BEIJING).time()
            if not in_manage(t) and t >= ASIA_START:
                break
            if t >= FLAT_END and t < ASIA_START:
                if open_tr:
                    side, entry, sl, tp, ets, mfe_max, be = open_tr
                    trades.append(
                        Trade(day, mode, side, entry, sl, tp, ets, b.ts, b.open, "01:45平")
                    )
                    open_tr = None
                break

            # manage open
            if open_tr:
                side, entry, sl, tp, ets, mfe_max, be = open_tr
                mfe_max = max(mfe_max, mfe(b, side, entry))
                if mfe_max >= 15:
                    be = entry
                held_min = (b.ts - ets).total_seconds() / 60
                if held_min >= 90 and mfe_max < 5:
                    trades.append(Trade(day, mode, side, entry, sl, tp, ets, b.ts, b.close, "90分钟没动"))
                    if trades[-1].pnl_usd_price < 0:
                        day_loss_n += 1
                    open_tr = None
                    day_trade_n += 1
                    pending_a_buy = pending_a_sell = False
                    if day_loss_n >= 2 or day_trade_n >= 2:
                        break
                    continue
                hit = exit_open(b, side, sl, tp, be)
                if hit:
                    px, why = hit
                    tr = Trade(day, mode, side, entry, sl, tp, ets, b.ts, px, why)
                    trades.append(tr)
                    if tr.pnl_usd_price < 0:
                        day_loss_n += 1
                        a_stopped = mode == "A"
                    open_tr = None
                    day_trade_n += 1
                    pending_a_buy = pending_a_sell = False
                    if day_loss_n >= 2 or day_trade_n >= 2:
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

            # regime switch
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

            if day_trade_n >= 2 or day_loss_n >= 2:
                break
            if a_stopped and mode != "B":
                break

            # fills
            filled = None
            if pending_b and not b_used:
                side, lim = pending_b
                if hit_level(b, lim):
                    filled = (side, lim, "B")
                    pending_b = None
                    b_used = True
            if filled is None and mode == "A":
                buy_hit = pending_a_buy and hit_level(b, buy_lim)
                sell_hit = pending_a_sell and hit_level(b, sell_lim)
                if buy_hit and sell_hit:
                    skipped["同根双边触价跳过"] += 1
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
                tp = entry + TP_USD if side == "long" else entry - TP_USD
                open_tr = (side, entry, sl, tp, b.ts, mfe(b, side, entry), None)

        if open_tr:
            side, entry, sl, tp, ets, mfe_max, be = open_tr
            last = after[-1] if after else asia[-1]
            trades.append(Trade(day, mode, side, entry, sl, tp, ets, last.ts, last.close, "数据结束平"))

        all_so_far.extend(day_bars)

    wins = [t for t in trades if t.win]
    losses = [t for t in trades if not t.win and abs(t.pnl_usd_price) > 0.05]
    flats = [t for t in trades if abs(t.pnl_usd_price) <= 0.05]
    n = len(trades)
    wr = 100 * len(wins) / n if n else 0
    avg_w = sum(t.pnl_usd_price for t in wins) / len(wins) if wins else 0
    avg_l = sum(t.pnl_usd_price for t in losses) / len(losses) if losses else 0
    gross_w = sum(t.pnl_usd_price for t in wins)
    gross_l = abs(sum(t.pnl_usd_price for t in losses))
    pf = gross_w / gross_l if gross_l else float("inf")
    acc = sum(t.pnl_account for t in trades)
    days_traded = len({t.day for t in trades})

    print("=== 亚盘盒子 粗回测（非实盘）===")
    print(f"数据: Yahoo GC=F M15  {bars[0].ts:%Y-%m-%d} → {bars[-1].ts:%Y-%m-%d}  共{len(bars)}根")
    print(f"交易日(有盒子): {len(days) - skipped['亚盘K线不足'] - skipped['盒子过窄']}  有成交天数: {days_traded}")
    print(f"跳过: {dict(skipped)}")
    print()
    print(f"总笔数 {n}  盈利 {len(wins)}  亏损 {len(losses)}  持平 {len(flats)}")
    print(f"胜率 {wr:.1f}%")
    print(f"平均盈利 ${avg_w:.2f}/盎司  平均亏损 ${avg_l:.2f}/盎司  盈亏比 {abs(avg_w/avg_l) if avg_l else 0:.2f}")
    print(f"利润因子 {pf:.2f}  账户粗算(0.02手) ${acc:.0f}")
    print()
    by_kind = defaultdict(list)
    by_why = defaultdict(int)
    for t in trades:
        by_kind[t.kind].append(t)
        by_why[t.reason] += 1
    for k, ts in by_kind.items():
        w = sum(1 for x in ts if x.win)
        print(f"  {k}: {len(ts)}笔 胜率 {100*w/len(ts):.0f}%  净 ${sum(x.pnl_account for x in ts):.0f}")
    print("平仓原因:", dict(by_why))
    print()
    print("注意: 这是 GC=F 15分钟 OHLC 模拟，不是你 MT5 账户成绩。")
    print("同根既触止损又触止盈按亏损计；无滑点/点差/大数据过滤；样本只有约两个月。")
    print("不能当作未来胜率承诺。")


if __name__ == "__main__":
    run()
