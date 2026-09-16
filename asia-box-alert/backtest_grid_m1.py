"""M1/M5 equal-lot grid: one swing, add on dips, close basket on bounce."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from gold_feed import fetch_gc_bars
from scale_grid import (
    GridState,
    average_price,
    basket_tp_price,
    float_pnl_usd,
    next_add_price,
    stop_price,
)
from tzutil import BEIJING

# monkeypatch distances via GridState fields; TP/hard from module constants
import scale_grid as sg

ASIA_START = time(8, 0)
ENTRY_START = time(15, 0)
ENTRY_END = time(1, 0)
FLAT_END = time(1, 45)


def session_date(ts: datetime):
    ts = ts.astimezone(BEIJING)
    if ts.time() < ASIA_START:
        return (ts - timedelta(days=1)).date()
    return ts.date()


def in_trade(t: time) -> bool:
    return t >= ENTRY_START or t < ENTRY_END


def in_flat_window(t: time) -> bool:
    return t >= FLAT_END and t < ASIA_START


@dataclass
class Round:
    day: object
    side: str
    layers: int
    pnl: float
    reason: str

    @property
    def win(self) -> bool:
        return self.pnl > 0.2


def simulate(
    bars,
    step: float,
    max_layers: int,
    basket_tp: float,
    hard_extra: float,
    lookback: int,
    lot: float = 0.01,
) -> list[Round]:
    sg.GRID_STEP = step
    sg.GRID_BASKET_TP = basket_tp
    sg.GRID_HARD_EXTRA = hard_extra
    sg.GRID_MAX_LAYERS = max_layers
    sg.GRID_LOT = lot

    rounds: list[Round] = []
    st = GridState(step=step, max_layers=max_layers)
    cooldown_until = None

    def close_at(px: float, reason: str, ts: datetime) -> None:
        nonlocal st, cooldown_until
        pnl = float_pnl_usd(px, st) or 0.0
        # rescale if lot patched inside float_pnl (uses GRID_LOT)
        rounds.append(Round(session_date(ts), st.side, st.layers, pnl, reason))
        st = GridState(step=step, max_layers=max_layers)
        cooldown_until = ts + timedelta(minutes=max(3, lookback // 3))

    for i, b in enumerate(bars):
        t = b.ts.astimezone(BEIJING).time()
        if in_flat_window(t):
            if st.active:
                close_at(b.open, "01:45平", b.ts)
            continue
        if not in_trade(t):
            if st.active:
                close_at(b.close, "时段结束平", b.ts)
            continue

        if st.active:
            while st.layers < max_layers:
                nxt = next_add_price(st)
                if nxt is None:
                    break
                hit = b.low <= nxt if st.side == "long" else b.high >= nxt
                if not hit:
                    break
                st.layers += 1
            stop = stop_price(st)
            tp = basket_tp_price(st)
            if st.side == "long":
                hit_stop = stop is not None and b.low <= stop
                hit_tp = tp is not None and b.high >= tp
                if hit_stop:
                    close_at(stop, "硬止损", b.ts)
                elif hit_tp:
                    close_at(tp, "回弹全平", b.ts)
            else:
                hit_stop = stop is not None and b.high >= stop
                hit_tp = tp is not None and b.low <= tp
                if hit_stop:
                    close_at(stop, "硬止损", b.ts)
                elif hit_tp:
                    close_at(tp, "回弹全平", b.ts)
            continue

        if cooldown_until and b.ts < cooldown_until:
            continue
        window = bars[max(0, i - lookback) : i + 1]
        if len(window) < 5:
            continue
        swing_h = max(x.high for x in window)
        swing_l = min(x.low for x in window)
        long_lv = swing_h - step
        short_lv = swing_l + step
        long_hit = b.low <= long_lv
        short_hit = b.high >= short_lv
        if long_hit and not short_hit:
            st = GridState(side="long", anchor=long_lv, layers=1, step=step, max_layers=max_layers)
        elif short_hit and not long_hit:
            st = GridState(side="short", anchor=short_lv, layers=1, step=step, max_layers=max_layers)

    if st.active:
        last = bars[-1]
        close_at(last.close, "数据结束平", last.ts)
    return rounds


def report(title: str, bars, rounds: list[Round]) -> None:
    n = len(rounds)
    if not n:
        print(f"=== {title} ===  无成交\n")
        return
    wins = [r for r in rounds if r.win]
    losses = [r for r in rounds if r.pnl < -0.2]
    flats = n - len(wins) - len(losses)
    wr = 100 * len(wins) / n
    wr2 = 100 * len(wins) / max(1, len(wins) + len(losses))
    acc = sum(r.pnl for r in rounds)
    gw = sum(r.pnl for r in wins)
    gl = abs(sum(r.pnl for r in losses)) or 1
    by_l = defaultdict(list)
    by_why = defaultdict(int)
    for r in rounds:
        by_l[r.layers].append(r)
        by_why[r.reason] += 1
    print(f"=== {title} ===")
    print(f"K线 {bars[0].ts:%Y-%m-%d %H:%M} → {bars[-1].ts:%Y-%m-%d %H:%M}  n={len(bars)}  轮次 {n}")
    print(
        f"胜率 {wr:.1f}%  （{len(wins)}盈/{len(losses)}亏/{flats}平）  去平 {wr2:.1f}%  "
        f"PF {gw/gl:.2f}  合计 ${acc:.0f}"
    )
    if wins and losses:
        print(
            f"均盈 ${sum(r.pnl for r in wins)/len(wins):.1f}  "
            f"均亏 ${sum(r.pnl for r in losses)/len(losses):.1f}"
        )
    for k in sorted(by_l):
        rs = by_l[k]
        w = sum(1 for x in rs if x.win)
        print(f"  {k}层 {len(rs)}轮 胜率 {100*w/len(rs):.0f}%")
    print("原因", dict(by_why))
    print()


def main() -> None:
    cfgs = [
        dict(step=1.5, max_layers=5, basket_tp=1.2, hard_extra=3.0, lookback=15, tag="密网 $1.5×5"),
        dict(step=2.0, max_layers=4, basket_tp=1.5, hard_extra=4.0, lookback=20, tag="标准 $2×4"),
        dict(step=3.0, max_layers=4, basket_tp=2.0, hard_extra=5.0, lookback=20, tag="宽网 $3×4"),
    ]
    datasets = []
    for iv, rng in [("1m", "8d"), ("5m", "60d")]:
        bars, _ = fetch_gc_bars(iv, rng)
        datasets.append((iv, rng, bars))

    print("这是 M1/M5 小波段网格：从近端高低点回撤 1 格才开仓，回弹篮子全平。")
    print("Yahoo 1 分钟最多约 8 天；近两个月用 5 分钟代替（公开源没有 3 个月 M1）。\n")
    for iv, rng, bars in datasets:
        for cfg in cfgs:
            tag = cfg.pop("tag")
            rounds = simulate(bars, **cfg)
            report(f"{tag}  {iv} {rng}", bars, rounds)
            cfg["tag"] = tag


if __name__ == "__main__":
    main()
