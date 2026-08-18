"""Asia Box A/B signals. Prices in USD per ounce."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import TYPE_CHECKING

from tzutil import BEIJING

if TYPE_CHECKING:
    from news_calendar import NewsStatus
ASIA_START = time(8, 0)
ASIA_END = time(14, 30)
ENTRY_END = time(1, 0)  # next calendar day 01:00
FLAT_END = time(1, 45)

ADX_RANGE_MAX = 22.0
ADX_TREND_MIN = 28.0
PULLBACK_TOL = 3.0  # dollars from breakout level
CHASE_PAD = 8.0


@dataclass
class Box:
    high: float
    low: float

    @property
    def range(self) -> float:
        return max(self.high - self.low, 0.01)

    @property
    def upper_start(self) -> float:
        return self.high - 0.25 * self.range

    @property
    def lower_end(self) -> float:
        return self.low + 0.25 * self.range


@dataclass
class Signal:
    key: str
    mode: str  # WAIT / A / B / FLAT / OFF
    title: str
    message: str
    urgent: bool


def beijing_now(now: datetime | None = None) -> datetime:
    if now is None:
        now = datetime.now(BEIJING)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=BEIJING)
    else:
        now = now.astimezone(BEIJING)
    return now


def session_status(now: datetime | None = None) -> str:
    """WAIT_BOX / LOCKED / LATE_FLAT / SLEEP."""
    t = beijing_now(now).time()
    if ASIA_START <= t < ASIA_END:
        return "WAIT_BOX"
    if ASIA_END <= t or t < ENTRY_END:
        return "LOCKED"
    if ENTRY_END <= t < FLAT_END:
        return "LATE_FLAT"
    return "SLEEP"


@dataclass
class AdxState:
    adx: float
    plus_di: float
    minus_di: float


def compute_adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> AdxState | None:
    n = min(len(highs), len(lows), len(closes))
    if n < period + 2:
        return None
    trs: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        trs.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )
    if len(trs) < period:
        return None

    def wilder(values: list[float]) -> list[float]:
        out = [sum(values[:period])]
        for v in values[period:]:
            out.append(out[-1] - out[-1] / period + v)
        return out

    atr = wilder(trs)
    pdm = wilder(plus_dm)
    mdm = wilder(minus_dm)
    dx: list[float] = []
    plus_di = 0.0
    minus_di = 0.0
    for a, p, m in zip(atr, pdm, mdm):
        if a <= 0:
            plus_di, minus_di = 0.0, 0.0
            dx.append(0.0)
            continue
        plus_di = 100 * p / a
        minus_di = 100 * m / a
        s = plus_di + minus_di
        dx.append(0.0 if s <= 0 else 100 * abs(plus_di - minus_di) / s)
    if len(dx) < period:
        return None
    adx = sum(dx[:period]) / period
    for v in dx[period:]:
        adx = (adx * (period - 1) + v) / period
    return AdxState(adx=adx, plus_di=plus_di, minus_di=minus_di)


def compute_rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss <= 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def price_zone(price: float, box: Box | None) -> str:
    if box is None:
        return "无盒子"
    if price >= box.upper_start:
        return "上沿区"
    if price <= box.lower_end:
        return "下沿区"
    return "中间禁区"


def evaluate(
    price: float,
    box: Box | None,
    adx: AdxState | None,
    last_m15_close: float | None,
    now: datetime | None = None,
    news: "NewsStatus | None" = None,
) -> Signal:
    status = session_status(now)
    if status == "SLEEP":
        return Signal("sleep", "OFF", "已过平仓时间", "01:45 后不再提醒新单，睡着不留仓。", False)
    if status == "LATE_FLAT":
        return Signal("flat", "FLAT", "停止开新仓", "01:00 后只平仓，01:45 必须全平。", True)
    if box is None or status == "WAIT_BOX":
        return Signal("wait_box", "WAIT", "等待锁盒", "北京 14:30 前只记录亚盘高低，不按 A/B 进场。", False)

    if news is not None and news.in_blackout:
        return Signal(
            "news_blackout",
            "NEWS",
            "大数据时段，勿开仓",
            news.detail,
            True,
        )

    adx_val = adx.adx if adx is not None else None
    pdi = adx.plus_di if adx is not None else None
    mdi = adx.minus_di if adx is not None else None
    broken_up = (last_m15_close is not None and last_m15_close > box.high) or price > box.high + 1
    broken_down = (last_m15_close is not None and last_m15_close < box.low) or price < box.low - 1
    bullish = pdi is not None and mdi is not None and pdi > mdi
    bearish = pdi is not None and mdi is not None and mdi > pdi
    trend = adx_val is not None and adx_val >= ADX_TREND_MIN
    ranging = (adx_val is None or adx_val < ADX_RANGE_MAX) and not broken_up and not broken_down

    if broken_up or (trend and bullish and price >= box.upper_start):
        if price > box.high + CHASE_PAD:
            return Signal(
                "b_no_chase_long",
                "B",
                "单边向上，不要追",
                f"现价 {price:.2f} 已离开上沿。等回踩 {box.high:.2f} 再挂 Buy Limit，SL {box.high - 15:.2f}。",
                True,
            )
        if abs(price - box.high) <= PULLBACK_TOL or (price <= box.high + PULLBACK_TOL and price >= box.high - PULLBACK_TOL):
            return Signal(
                "b_long",
                "B",
                "B 做多回踩到了",
                f"回踩上沿 {box.high:.2f}。Buy Limit {box.high:.0f}，SL {box.high - 15:.0f}，TP {box.high + 22:.0f}，手数 0.02。",
                True,
            )
        return Signal(
            "b_wait_long",
            "B",
            "已切 B，等回踩上沿",
            f"上沿 {box.high:.2f} 已破。撤掉 A 空单，等价格回到 {box.high:.2f} 附近再挂多，不要追 {price:.2f}。",
            True,
        )

    if broken_down or (trend and bearish and price <= box.lower_end):
        if price < box.low - CHASE_PAD:
            return Signal(
                "b_no_chase_short",
                "B",
                "单边向下，不要追",
                f"现价 {price:.2f} 已离开下沿。等反弹 {box.low:.2f} 再挂 Sell Limit，SL {box.low + 15:.2f}。",
                True,
            )
        if abs(price - box.low) <= PULLBACK_TOL:
            return Signal(
                "b_short",
                "B",
                "B 做空回踩到了",
                f"反弹下沿 {box.low:.2f}。Sell Limit {box.low:.0f}，SL {box.low + 15:.0f}，TP {box.low - 22:.0f}，手数 0.02。",
                True,
            )
        return Signal(
            "b_wait_short",
            "B",
            "已切 B，等反弹下沿",
            f"下沿 {box.low:.2f} 已破。撤掉 A 多单，等价格回到 {box.low:.2f} 附近再挂空。",
            True,
        )

    if adx_val is not None and ADX_RANGE_MAX <= adx_val < ADX_TREND_MIN and not broken_up and not broken_down:
        return Signal("fuzzy", "WAIT", "方向模糊，宁可不做", f"ADX {adx_val:.1f} 在 22–28，盒子未破，先空仓。", False)

    if ranging or adx_val is None:
        if price >= box.upper_start:
            return Signal(
                "a_sell",
                "A",
                "A 上沿可挂空",
                f"上沿区 {box.upper_start:.2f}–{box.high:.2f}。Sell Limit {box.high - 5:.0f}，SL {box.high - 5 + 15:.0f}，TP {box.high - 5 - 22:.0f}，手数 0.02。",
                True,
            )
        if price <= box.lower_end:
            return Signal(
                "a_buy",
                "A",
                "A 下沿可挂多",
                f"下沿区 {box.low:.2f}–{box.lower_end:.2f}。Buy Limit {box.low + 5:.0f}，SL {box.low + 5 - 15:.0f}，TP {box.low + 5 + 22:.0f}，手数 0.02。",
                True,
            )
        return Signal(
            "a_mid",
            "A",
            "中间禁区，空仓",
            f"现价 {price:.2f} 在 {box.lower_end:.2f}–{box.upper_start:.2f}，按规则不交易。",
            False,
        )

    adx_txt = f"{adx_val:.1f}" if adx_val is not None else "n/a"
    return Signal("hold", "WAIT", "继续观察", f"现价 {price:.2f}，ADX {adx_txt}。", False)
