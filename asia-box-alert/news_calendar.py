"""USD high-impact calendar for gold trading blackout windows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import re
from typing import Any
from urllib.request import Request, urlopen
import ssl

from tzutil import BEIJING

CAL_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
BLACKOUT_MIN = 30
UA = {"User-Agent": "Mozilla/5.0 AsiaBoxAlert/1.2"}

GOLD_KEYWORDS = (
    "cpi", "nfp", "non-farm", "nonfarm", "fomc", "fed ", "federal funds",
    "interest rate", "ppi", "pce", "gdp", "jobless", "unemployment",
    "powell", "retail sales", "ism ", "adp", "claims", "payroll",
)


@dataclass
class NewsEvent:
    title: str
    when: datetime
    impact: str

    @property
    def beijing_str(self) -> str:
        return self.when.astimezone(BEIJING).strftime("%m-%d %H:%M")


@dataclass
class NewsStatus:
    in_blackout: bool
    today_events: list[NewsEvent]
    active: NewsEvent | None
    next_event: NewsEvent | None
    summary: str
    detail: str


def _fetch_calendar() -> list[dict[str, Any]]:
    ctx = ssl.create_default_context()
    try:
        req = Request(CAL_URL, headers=UA)
        with urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _parse_event(raw: dict[str, Any]) -> NewsEvent | None:
    if str(raw.get("country", "")).upper() not in ("USD", "US", "USA"):
        return None
    impact = str(raw.get("impact", "")).lower()
    if impact not in ("high", "medium"):
        return None
    title = str(raw.get("title", ""))
    if impact != "high" and not any(k in title.lower() for k in GOLD_KEYWORDS):
        return None
    if impact == "medium" and not any(k in title.lower() for k in ("cpi", "nfp", "fomc", "fed", "gdp", "ppi")):
        return None

    when_raw = raw.get("date") or raw.get("timestamp")
    if not when_raw:
        d = str(raw.get("date", ""))
        t = str(raw.get("time", ""))
        if not d:
            return None
        when_raw = f"{d} {t}".strip()

    when: datetime | None = None
    if isinstance(when_raw, (int, float)):
        when = datetime.fromtimestamp(when_raw, tz=BEIJING)
    else:
        s = str(when_raw).strip()
        try:
            when = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=BEIJING)
        except ValueError:
            m = re.match(r"(\d{4}-\d{2}-\d{2})[T ](\d{1,2}):(\d{2})", s)
            if m:
                when = datetime.strptime(f"{m.group(1)} {m.group(2)}:{m.group(3)}", "%Y-%m-%d %H:%M").replace(
                    tzinfo=BEIJING
                )
    if when is None:
        return None
    return NewsEvent(title=title, when=when.astimezone(BEIJING), impact=impact.title())


def get_news_status(now: datetime | None = None, events: list[NewsEvent] | None = None) -> NewsStatus:
    now = now or datetime.now(BEIJING)
    if now.tzinfo is None:
        now = now.replace(tzinfo=BEIJING)
    else:
        now = now.astimezone(BEIJING)

    if events is None:
        events = []
        for raw in _fetch_calendar():
            ev = _parse_event(raw)
            if ev:
                events.append(ev)
        events.sort(key=lambda e: e.when)

    today = now.date()
    today_events = [e for e in events if e.when.astimezone(BEIJING).date() == today]

    active = None
    for e in events:
        start = e.when - timedelta(minutes=BLACKOUT_MIN)
        end = e.when + timedelta(minutes=BLACKOUT_MIN)
        if start <= now <= end:
            active = e
            break

    next_ev = None
    for e in events:
        if e.when > now:
            next_ev = e
            break

    if active:
        summary = f"禁做：{active.title} 前后{BLACKOUT_MIN}分钟"
        detail = f"大数据时段 {active.beijing_str}，本次入场不合适，等数据过后再看。"
        in_blackout = True
    elif today_events:
        names = "、".join(e.title for e in today_events[:4])
        summary = f"今日大数据：{names}"
        if next_ev and next_ev.when.date() == today:
            mins = int((next_ev.when - now).total_seconds() // 60)
            detail = f"下一项 {next_ev.title} {next_ev.beijing_str}（约{mins}分钟后）。数据前{BLACKOUT_MIN}分钟勿开仓。"
        else:
            detail = "今日有重要数据，接近公布时间前30分钟不要新开仓。"
        in_blackout = False
    else:
        summary = "今日暂无监测到 USD 大数据"
        detail = "无 CPI/NFP/FOMC 等待公布项（以日历源为准）。"
        in_blackout = False

    return NewsStatus(
        in_blackout=in_blackout,
        today_events=today_events,
        active=active,
        next_event=next_ev,
        summary=summary,
        detail=detail,
    )
