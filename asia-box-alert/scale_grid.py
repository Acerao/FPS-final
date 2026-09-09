"""等距加仓网格（回弹全平）。不是翻倍马丁。"""

from __future__ import annotations

from dataclasses import dataclass

from news_calendar import NewsStatus
from strategy import ADX_TREND_MIN, AdxState, Signal, session_status

GRID_STEP = 8.0
GRID_LOT = 0.01
GRID_MAX_LAYERS = 3
GRID_BASKET_TP = 5.0  # 现价回到均价上方（多）/下方（空）这么多美元 → 全平
GRID_HARD_EXTRA = 10.0  # 最后一层之外再走这么多还不弹 → 全止损


@dataclass
class GridState:
    side: str = ""  # long / short / ""
    anchor: float = 0.0
    layers: int = 0
    step: float = GRID_STEP
    max_layers: int = GRID_MAX_LAYERS

    @property
    def active(self) -> bool:
        return self.side in {"long", "short"} and self.anchor > 500 and self.layers >= 1


def layer_prices(state: GridState) -> list[float]:
    if not state.active:
        return []
    prices = []
    for i in range(state.layers):
        if state.side == "long":
            prices.append(state.anchor - i * state.step)
        else:
            prices.append(state.anchor + i * state.step)
    return prices


def average_price(state: GridState) -> float | None:
    prices = layer_prices(state)
    if not prices:
        return None
    return sum(prices) / len(prices)


def next_add_price(state: GridState) -> float | None:
    if not state.active or state.layers >= state.max_layers:
        return None
    if state.side == "long":
        return state.anchor - state.layers * state.step
    return state.anchor + state.layers * state.step


def stop_price(state: GridState) -> float | None:
    if not state.active:
        return None
    last = layer_prices(state)[-1]
    if state.side == "long":
        return last - GRID_HARD_EXTRA
    return last + GRID_HARD_EXTRA


def basket_tp_price(state: GridState) -> float | None:
    avg = average_price(state)
    if avg is None:
        return None
    if state.side == "long":
        return avg + GRID_BASKET_TP
    return avg - GRID_BASKET_TP


def float_pnl_usd(price: float, state: GridState) -> float | None:
    """Account USD at 0.01 lot: $1 price ≈ $1."""
    prices = layer_prices(state)
    if not prices:
        return None
    total = 0.0
    for px in prices:
        if state.side == "long":
            total += (price - px) * (GRID_LOT * 100)
        else:
            total += (px - price) * (GRID_LOT * 100)
    return total


def evaluate_grid(
    price: float,
    state: GridState,
    adx: AdxState | None = None,
    now=None,
    news: NewsStatus | None = None,
) -> Signal:
    status = session_status(now)
    if status == "SLEEP":
        return Signal("sleep", "OFF", "已过平仓时间", "01:45 后网格也必须空仓。", False)
    if status == "LATE_FLAT":
        if state.active:
            return Signal("grid_flatten", "GRID", "01:00 后全平网格", "评估期 01:45 前把本轮网格全部平掉。", True)
        return Signal("flat", "FLAT", "停止开新仓", "01:00 后不再开网格新层。", True)
    if status == "WAIT_BOX":
        return Signal("wait_box", "WAIT", "亚盘只观察", "14:30 前不启动新网格。可先填方向，15:00 后再开。", False)

    if news is not None and news.in_blackout:
        return Signal("news_blackout", "NEWS", "大数据时段，勿加层", news.detail, True)

    trend = adx is not None and adx.adx >= ADX_TREND_MIN
    if trend and not state.active:
        return Signal(
            "grid_trend_off",
            "WAIT",
            "趋势日先别开网格",
            f"ADX {adx.adx:.1f} ≥ {ADX_TREND_MIN}。等距网格怕单边，今天用亚盘盒子 B，或空仓。",
            False,
        )

    if not state.active:
        return Signal(
            "grid_idle",
            "GRID",
            "网格空闲，可开第 1 层",
            f"等距网格（非马丁翻倍）。手数 {GRID_LOT}，每跌/涨 ${GRID_STEP:.0f} 加 1 层，最多 {GRID_MAX_LAYERS} 层。"
            f"回弹到均价±${GRID_BASKET_TP:.0f} 全平；再逆行 ${GRID_HARD_EXTRA:.0f} 全止损。"
            f"点「开始本轮」用现价 {price:.1f} 做锚点。",
            False,
        )

    avg = average_price(state)
    tp = basket_tp_price(state)
    stop = stop_price(state)
    nxt = next_add_price(state)
    pnl = float_pnl_usd(price, state) or 0.0
    side_cn = "多" if state.side == "long" else "空"
    levels = " / ".join(f"{p:.1f}" for p in layer_prices(state))

    if state.side == "long" and price <= (stop or 0):
        return Signal(
            "grid_stop_all",
            "GRID",
            "网格触及硬止损，全部平掉",
            f"已 {state.layers} 层多 @ {levels}。现价 {price:.1f} ≤ 止损 {stop:.1f}。"
            f"浮亏约 ${pnl:.0f}。不要再加层，本轮结束。",
            True,
        )
    if state.side == "short" and price >= (stop or 9e9):
        return Signal(
            "grid_stop_all",
            "GRID",
            "网格触及硬止损，全部平掉",
            f"已 {state.layers} 层空 @ {levels}。现价 {price:.1f} ≥ 止损 {stop:.1f}。"
            f"浮亏约 ${pnl:.0f}。不要再加层，本轮结束。",
            True,
        )

    if state.side == "long" and tp is not None and price >= tp:
        return Signal(
            "grid_close_all",
            "GRID",
            "回弹到位，网格全部平掉",
            f"{state.layers} 层多均价 {avg:.1f}，目标 {tp:.1f}。现价 {price:.1f}，浮盈约 ${pnl:.0f}。全清。",
            True,
        )
    if state.side == "short" and tp is not None and price <= tp:
        return Signal(
            "grid_close_all",
            "GRID",
            "回弹到位，网格全部平掉",
            f"{state.layers} 层空均价 {avg:.1f}，目标 {tp:.1f}。现价 {price:.1f}，浮盈约 ${pnl:.0f}。全清。",
            True,
        )

    if nxt is not None:
        near = abs(price - nxt) <= 1.5
        if state.side == "long" and price <= nxt + 0.4:
            return Signal(
                "grid_add",
                "GRID",
                f"加第 {state.layers + 1} 层多",
                f"现价 {price:.1f} 靠近 {nxt:.1f}。Buy Limit {nxt:.0f}，手数 {GRID_LOT}（等量，禁止翻倍）。"
                f"已有 {state.layers} 层，均价 {avg:.1f}。回弹到 {tp:.1f} 全平。",
                True,
            )
        if state.side == "short" and price >= nxt - 0.4:
            return Signal(
                "grid_add",
                "GRID",
                f"加第 {state.layers + 1} 层空",
                f"现价 {price:.1f} 靠近 {nxt:.1f}。Sell Limit {nxt:.0f}，手数 {GRID_LOT}（等量，禁止翻倍）。"
                f"已有 {state.layers} 层，均价 {avg:.1f}。回弹到 {tp:.1f} 全平。",
                True,
            )
        if near:
            return Signal(
                "grid_add_soon",
                "GRID",
                "接近下一层",
                f"{side_cn}下一层 {nxt:.1f}，现价 {price:.1f}。准备好限价，不要提前乱加。",
                False,
            )

    return Signal(
        "grid_hold",
        "GRID",
        f"网格持有 {state.layers}/{state.max_layers} 层{side_cn}",
        f"成本 {levels}，均价 {avg:.1f}，回弹全平 {tp:.1f}，硬止损 {stop:.1f}。"
        f"现价 {price:.1f}，浮盈约 ${pnl:.0f}。"
        + (f" 下一层 {nxt:.1f}。" if nxt else " 已到最大层，只等回弹或止损。"),
        False,
    )
