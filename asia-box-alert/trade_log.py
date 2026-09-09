"""Manual trade journal for Asia Box monitor (no auto-trading)."""

from __future__ import annotations

import csv
import json
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path

from strategy import beijing_now, clamp_lot, dollars_per_dollar
from tzutil import BEIJING

ROOT = Path(__file__).resolve().parent
TRADES_PATH = ROOT / "trades.json"
CSV_PATH = ROOT / "trades.csv"


@dataclass
class TradeRecord:
    id: str
    ts: str  # Beijing ISO
    strategy: str
    side: str  # long / short
    entry: float
    exit: float | None
    sl: float | None
    tp: float | None
    lot: float
    pnl_usd: float | None
    result: str  # win / loss / be / open
    note: str = ""
    asia_h: float | None = None
    asia_l: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _load_raw() -> list[dict]:
    if not TRADES_PATH.exists():
        return []
    try:
        data = json.loads(TRADES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _save_raw(rows: list[dict]) -> None:
    TRADES_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def list_trades(limit: int | None = None) -> list[TradeRecord]:
    rows = _load_raw()
    out: list[TradeRecord] = []
    keys = {f.name for f in fields(TradeRecord)}
    for row in rows:
        if not isinstance(row, dict):
            continue
        payload = {k: row.get(k) for k in keys}
        try:
            out.append(TradeRecord(**payload))  # type: ignore[arg-type]
        except TypeError:
            continue
    if limit is not None:
        return out[-limit:]
    return out


def estimate_pnl(side: str, entry: float, exit_px: float, lot: float) -> float:
    lot = clamp_lot(lot)
    move = (exit_px - entry) if side == "long" else (entry - exit_px)
    return round(move * dollars_per_dollar(lot), 2)


def add_trade(
    *,
    strategy: str,
    side: str,
    entry: float,
    exit: float | None = None,
    sl: float | None = None,
    tp: float | None = None,
    lot: float = 0.02,
    pnl_usd: float | None = None,
    result: str = "open",
    note: str = "",
    asia_h: float | None = None,
    asia_l: float | None = None,
    when: datetime | None = None,
) -> TradeRecord:
    side = "long" if str(side).lower() in {"long", "buy", "多"} else "short"
    result = str(result or "open").lower()
    if result not in {"win", "loss", "be", "open"}:
        result = "open"
    lot = clamp_lot(lot)
    if pnl_usd is None and exit is not None:
        pnl_usd = estimate_pnl(side, float(entry), float(exit), lot)
        if result == "open":
            if pnl_usd > 0.5:
                result = "win"
            elif pnl_usd < -0.5:
                result = "loss"
            else:
                result = "be"
    now = beijing_now(when)
    rec = TradeRecord(
        id=uuid.uuid4().hex[:10],
        ts=now.isoformat(timespec="seconds"),
        strategy=strategy or "",
        side=side,
        entry=round(float(entry), 2),
        exit=None if exit is None else round(float(exit), 2),
        sl=None if sl is None else round(float(sl), 2),
        tp=None if tp is None else round(float(tp), 2),
        lot=lot,
        pnl_usd=None if pnl_usd is None else round(float(pnl_usd), 2),
        result=result,
        note=(note or "").strip(),
        asia_h=asia_h,
        asia_l=asia_l,
    )
    rows = _load_raw()
    rows.append(rec.to_dict())
    _save_raw(rows)
    export_csv(rows)
    return rec


def _parse_ts(raw: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BEIJING)
    return dt.astimezone(BEIJING)


def summary(day: datetime | None = None) -> dict:
    day = beijing_now(day)
    target = day.date()
    trades = []
    for t in list_trades():
        ts = _parse_ts(t.ts)
        if ts is not None and ts.date() == target:
            trades.append(t)
    wins = sum(1 for t in trades if t.result == "win")
    losses = sum(1 for t in trades if t.result == "loss")
    be = sum(1 for t in trades if t.result == "be")
    open_n = sum(1 for t in trades if t.result == "open")
    pnl = sum(float(t.pnl_usd or 0.0) for t in trades)
    return {
        "date": target.isoformat(),
        "count": len(trades),
        "wins": wins,
        "losses": losses,
        "be": be,
        "open": open_n,
        "pnl_usd": round(pnl, 2),
        "trades": trades,
    }


def export_csv(rows: list[dict] | None = None) -> Path:
    rows = rows if rows is not None else _load_raw()
    fieldnames = [f.name for f in fields(TradeRecord)]
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            if isinstance(row, dict):
                writer.writerow({k: row.get(k, "") for k in fieldnames})
    return CSV_PATH


def format_summary_text(day: datetime | None = None) -> str:
    s = summary(day)
    lines = [
        f"复盘 {s['date']}（北京）",
        f"共 {s['count']} 笔  盈 {s['wins']}  亏 {s['losses']}  平 {s['be']}  未平 {s['open']}",
        f"当日盈亏合计约 ${s['pnl_usd']:.0f}",
    ]
    for t in s["trades"][-12:]:
        side_cn = "多" if t.side == "long" else "空"
        pnl = "--" if t.pnl_usd is None else f"${t.pnl_usd:+.0f}"
        exit_txt = "--" if t.exit is None else f"{t.exit:.1f}"
        lines.append(
            f"- {t.ts[11:16]} {side_cn} {t.entry:.1f}→{exit_txt}  {pnl}  [{t.result}] {t.strategy}"
        )
    lines.append(f"明细：{TRADES_PATH.name} / {CSV_PATH.name}")
    return "\n".join(lines)
