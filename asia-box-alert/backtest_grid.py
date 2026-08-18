"""Backtest equal-lot scale grid (not martingale) on Yahoo GC=F."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, time

from gold_feed import fetch_gc_bars
from scale_grid import (
    GRID_BASKET_TP,
    GRID_HARD_EXTRA,
    GRID_LOT,
    GRID_MAX_LAYERS,
    GRID_STEP,
    GridState,
    average_price,
    basket_tp_price,
    float_pnl_usd,
    next_add_price,
    stop_price,
)
from strategy import ADX_TREND_MIN, compute_adx
from tzutil import BEIJING

ASIA_START = time(8, 0)
ENTRY_START = time(15, 0)
ENTRY_END = time(1, 0)
FLAT_END = time(1, 45)


def session_date(ts: datetime):
    ts = ts.astimezone(BEIJING)
    if ts.time() < ASIA_START:
        return (ts - timedelta(days=1)).date()
    return ts.date()


@dataclass
class Round:
    day: object
    side: str
    layers: int
    pnl: float
    reason: str
    start: float
    end_px: float

    @property
    def win(self) -> bool:
        return self.pnl > 0.5


def _fill_long(bar, level: float) -> bool:
    return bar.low <= level


def _fill_short(bar, level: float) -> bool:
    return bar.high >= level


def simulate(bars) -> list[Round]:
    by_day: dict = defaultdict(list)
    for b in bars:
        by_day[session_date(b.ts)].append(b)
    days = sorted(by_day)
    rounds: list[Round] = []
    hist: list = []

    for i, day in enumerate(days):
        day_bars = sorted(by_day[day], key=lambda x: x.ts)
        after = [b for b in day_bars if b.ts.astimezone(BEIJING).time() >= ENTRY_START]
        if i + 1 < len(days):
            nxt = sorted(by_day[days[i + 1]], key=lambda x: x.ts)
            after += [b for b in nxt if b.ts.astimezone(BEIJING).time() < ASIA_START]
        if not after:
            hist.extend(day_bars)
            continue

        adx = compute_adx([b.high for b in hist], [b.low for b in hist], [b.close for b in hist])
        trend = adx is not None and adx.adx >= ADX_TREND_MIN
        ref = after[0].open
        st = GridState()
        started = False
        done = False

        def close_round(px: float, reason: str, ts: datetime) -> None:
            nonlocal done, st
            pnl = float_pnl_usd(px, st) or 0.0
            rounds.append(
                Round(day, st.side, st.layers, pnl, reason, st.anchor, px)
            )
            st = GridState()
            done = True

        for b in after:
            t = b.ts.astimezone(BEIJING).time()
            if t >= FLAT_END and t < ASIA_START:
                if st.active:
                    close_round(b.open, "01:45平", b.ts)
                break
            if done:
                break
            can_add = t >= ENTRY_START or t < ENTRY_END

            if st.active:
                stop = stop_price(st)
                tp = basket_tp_price(st)
                # adds first (down-then-up for long / up-then-down for short)
                while can_add and st.layers < st.max_layers:
                    nxt_px = next_add_price(st)
                    if nxt_px is None:
                        break
                    hit = _fill_long(b, nxt_px) if st.side == "long" else _fill_short(b, nxt_px)
                    if not hit:
                        break
                    st.layers += 1
                stop = stop_price(st)
                tp = basket_tp_price(st)
                if st.side == "long":
                    hit_stop = stop is not None and b.low <= stop
                    hit_tp = tp is not None and b.high >= tp
                    if hit_stop and hit_tp:
                        close_round(stop, "止损优先(同根)", b.ts)
                    elif hit_stop:
                        close_round(stop, "硬止损", b.ts)
                    elif hit_tp:
                        close_round(tp, "回弹全平", b.ts)
                else:
                    hit_stop = stop is not None and b.high >= stop
                    hit_tp = tp is not None and b.low <= tp
                    if hit_stop and hit_tp:
                        close_round(stop, "止损优先(同根)", b.ts)
                    elif hit_stop:
                        close_round(stop, "硬止损", b.ts)
                    elif hit_tp:
                        close_round(tp, "回弹全平", b.ts)
                continue

            if trend or not can_add or t >= ENTRY_END:
                continue
            long_lv = ref - GRID_STEP
            short_lv = ref + GRID_STEP
            long_hit = _fill_long(b, long_lv)
            short_hit = _fill_short(b, short_lv)
            if long_hit and short_hit:
                continue
            if long_hit:
                st = GridState(side="long", anchor=long_lv, layers=1)
                started = True
            elif short_hit:
                st = GridState(side="short", anchor=short_lv, layers=1)
                started = True
            if not st.active:
                continue
            # 开仓当根继续判断加层 / 止盈 / 止损
            while can_add and st.layers < st.max_layers:
                nxt_px = next_add_price(st)
                if nxt_px is None:
                    break
                hit = _fill_long(b, nxt_px) if st.side == "long" else _fill_short(b, nxt_px)
                if not hit:
                    break
                st.layers += 1
            stop = stop_price(st)
            tp = basket_tp_price(st)
            if st.side == "long":
                hit_stop = stop is not None and b.low <= stop
                hit_tp = tp is not None and b.high >= tp
                if hit_stop:
                    close_round(stop, "硬止损", b.ts)
                elif hit_tp:
                    close_round(tp, "回弹全平", b.ts)
            else:
                hit_stop = stop is not None and b.high >= stop
                hit_tp = tp is not None and b.low <= tp
                if hit_stop:
                    close_round(stop, "硬止损", b.ts)
                elif hit_tp:
                    close_round(tp, "回弹全平", b.ts)

        if st.active:
            last = after[-1]
            close_round(last.close, "数据结束平", last.ts)
        hist.extend(day_bars)

    return rounds


def summarize(title: str, bars, rounds: list[Round]) -> None:
    n = len(rounds)
    wins = [r for r in rounds if r.win]
    losses = [r for r in rounds if r.pnl < -0.5]
    flats = [r for r in rounds if abs(r.pnl) <= 0.5]
    wr = 100 * len(wins) / n if n else 0
    wr2 = 100 * len(wins) / max(1, len(wins) + len(losses))
    acc = sum(r.pnl for r in rounds)
    gw = sum(r.pnl for r in wins)
    gl = abs(sum(r.pnl for r in losses))
    pf = gw / gl if gl else float("inf")
    by_why = defaultdict(int)
    by_side = defaultdict(list)
    by_layers = defaultdict(list)
    for r in rounds:
        by_why[r.reason] += 1
        by_side[r.side].append(r)
        by_layers[r.layers].append(r)
    print(f"=== {title} ===")
    print(f"数据 {bars[0].ts:%Y-%m-%d} → {bars[-1].ts:%Y-%m-%d}  K线 {len(bars)}  轮次 {n}")
    print(
        f"胜率 {wr:.1f}%  （{len(wins)}盈 / {len(losses)}亏 / {len(flats)}平）  "
        f"去掉平盘 {wr2:.1f}%"
    )
    print(f"利润因子 {pf:.2f}  账户合计 ${acc:.0f}  （0.01手/层）")
    if wins:
        print(f"平均盈利 ${sum(r.pnl for r in wins)/len(wins):.1f}  平均亏损 ${sum(r.pnl for r in losses)/len(losses) if losses else 0:.1f}")
    for side, rs in by_side.items():
        w = sum(1 for x in rs if x.win)
        print(f"  {side}: {len(rs)}轮 胜率 {100*w/len(rs):.0f}%  净 ${sum(x.pnl for x in rs):.0f}")
    for k in sorted(by_layers):
        rs = by_layers[k]
        w = sum(1 for x in rs if x.win)
        print(f"  {k}层: {len(rs)}轮 胜率 {100*w/len(rs):.0f}%")
    print("结束原因:", dict(by_why))
    print()


def main() -> None:
    print(
        f"规则: 间距 ${GRID_STEP:.0f}  最多 {GRID_MAX_LAYERS} 层  等手数 {GRID_LOT}  "
        f"回弹均价±${GRID_BASKET_TP:.0f}全平  末层再逆行 ${GRID_HARD_EXTRA:.0f}止损"
    )
    print("每天最多 1 轮；15:00 起，先触及 ±$8 才开仓；ADX≥28 不开新轮；01:45 强制平。\n")
    specs = [("15m", "60d"), ("60m", "3mo"), ("1h", "6mo")]
    for interval, rng in specs:
        try:
            bars, _ = fetch_gc_bars(interval, rng)
        except Exception as exc:
            print(interval, rng, "拉不到:", exc)
            continue
        if interval == "1h":
            cutoff = bars[-1].ts - timedelta(days=92)
            bars = [b for b in bars if b.ts >= cutoff]
        rounds = simulate(bars)
        summarize(f"等距网格  {interval} {rng}", bars, rounds)
    print("注意: Yahoo GC=F 模拟，不是 MT5 实盘。同根止损/止盈按亏损。无点差、无大数据过滤。")
    print("不能当未来胜率承诺。翻倍马丁未测，评估期禁止。")


if __name__ == "__main__":
    main()
