"""Trade journal for Asia Box monitor (alerts can auto-log; no auto-trading)."""

from __future__ import annotations

import csv
import json
import re
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path

from strategy import beijing_now, clamp_lot, dollars_per_dollar
from tzutil import BEIJING

ROOT = Path(__file__).resolve().parent
TRADES_PATH = ROOT / "trades.json"
CSV_PATH = ROOT / "trades.csv"

OPEN_ALERT_KEYS = {
    "a_buy",
    "a_sell",
    "b_long",
    "b_short",
    "line_long_call",
    "line_short_call",
    "grid_add",
}
CLOSE_ALERT_KEYS = {
    "grid_close_all",
    "grid_stop_all",
    "grid_flatten",
}
LONG_KEYS = {"a_buy", "b_long", "line_long_call"}
SHORT_KEYS = {"a_sell", "b_short", "line_short_call"}


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
    source: str = "manual"  # manual / alert
    alert_key: str = ""

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
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            out.append(
                TradeRecord(
                    id=str(row.get("id") or ""),
                    ts=str(row.get("ts") or ""),
                    strategy=str(row.get("strategy") or ""),
                    side=str(row.get("side") or "long"),
                    entry=float(row["entry"]),
                    exit=None if row.get("exit") in (None, "") else float(row["exit"]),
                    sl=None if row.get("sl") in (None, "") else float(row["sl"]),
                    tp=None if row.get("tp") in (None, "") else float(row["tp"]),
                    lot=float(row.get("lot") or 0.02),
                    pnl_usd=None if row.get("pnl_usd") in (None, "") else float(row["pnl_usd"]),
                    result=str(row.get("result") or "open"),
                    note=str(row.get("note") or ""),
                    asia_h=None if row.get("asia_h") in (None, "") else float(row["asia_h"]),
                    asia_l=None if row.get("asia_l") in (None, "") else float(row["asia_l"]),
                    source=str(row.get("source") or "manual"),
                    alert_key=str(row.get("alert_key") or ""),
                )
            )
        except (TypeError, ValueError, KeyError):
            continue
    if limit is not None:
        return out[-limit:]
    return out


def estimate_pnl(side: str, entry: float, exit_px: float, lot: float) -> float:
    lot = clamp_lot(lot)
    move = (exit_px - entry) if side == "long" else (entry - exit_px)
    return round(move * dollars_per_dollar(lot), 2)


def _parse_float(message: str, patterns: list[str]) -> float | None:
    for pat in patterns:
        m = re.search(pat, message or "", re.I)
        if not m:
            continue
        try:
            return float(m.group(1))
        except ValueError:
            continue
    return None


def parse_alert_order(alert_key: str, message: str, grid_side: str | None = None) -> dict | None:
    """从提醒文案解析方向/入场/SL/TP；解析失败返回 None。"""
    entry = _parse_float(
        message,
        [
            r"(?:Buy Limit|Sell Limit|Entry)\s*([0-9]+(?:\.[0-9]+)?)",
            r"(?:挂|到)\s*([0-9]+(?:\.[0-9]+)?)",
        ],
    )
    if entry is None:
        return None
    sl = _parse_float(message, [r"SL\s*([0-9]+(?:\.[0-9]+)?)"] )
    tp = _parse_float(message, [r"TP\s*([0-9]+(?:\.[0-9]+)?)"] )
    lot = _parse_float(message, [r"手数\s*([0-9]+(?:\.[0-9]+)?)"] )

    if alert_key in LONG_KEYS or "Buy Limit" in (message or ""):
        side = "long"
    elif alert_key in SHORT_KEYS or "Sell Limit" in (message or ""):
        side = "short"
    elif alert_key == "grid_add":
        side = "long" if (grid_side or "long") == "long" else "short"
    else:
        side = "long" if ("做多" in (message or "") or "买" in (message or "")) else "short"

    return {"side": side, "entry": entry, "sl": sl, "tp": tp, "lot": lot}


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
    source: str = "manual",
    alert_key: str = "",
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
        source=source or "manual",
        alert_key=alert_key or "",
    )
    rows = _load_raw()
    rows.append(rec.to_dict())
    _save_raw(rows)
    export_csv(rows)
    return rec


def close_latest_open(
    *,
    exit_px: float,
    note: str = "",
    when: datetime | None = None,
) -> TradeRecord | None:
    rows = _load_raw()
    for i in range(len(rows) - 1, -1, -1):
        row = rows[i]
        if not isinstance(row, dict) or str(row.get("result")) != "open":
            continue
        side = str(row.get("side") or "long")
        entry = float(row["entry"])
        lot = float(row.get("lot") or 0.02)
        pnl = estimate_pnl(side, entry, float(exit_px), lot)
        if pnl > 0.5:
            result = "win"
        elif pnl < -0.5:
            result = "loss"
        else:
            result = "be"
        now = beijing_now(when)
        row["exit"] = round(float(exit_px), 2)
        row["pnl_usd"] = pnl
        row["result"] = result
        extra = (note or "").strip()
        old_note = str(row.get("note") or "")
        row["note"] = f"{old_note} | {extra}".strip(" |") if extra else old_note
        row["closed_ts"] = now.isoformat(timespec="seconds")
        _save_raw(rows)
        export_csv(rows)
        return TradeRecord(
            id=str(row.get("id") or ""),
            ts=str(row.get("ts") or ""),
            strategy=str(row.get("strategy") or ""),
            side=side,
            entry=entry,
            exit=float(row["exit"]),
            sl=None if row.get("sl") in (None, "") else float(row["sl"]),
            tp=None if row.get("tp") in (None, "") else float(row["tp"]),
            lot=lot,
            pnl_usd=pnl,
            result=result,
            note=str(row.get("note") or ""),
            asia_h=None if row.get("asia_h") in (None, "") else float(row["asia_h"]),
            asia_l=None if row.get("asia_l") in (None, "") else float(row["asia_l"]),
            source=str(row.get("source") or "manual"),
            alert_key=str(row.get("alert_key") or ""),
        )
    return None


def auto_log_from_alert(
    *,
    alert_key: str,
    message: str,
    strategy: str,
    lot: float,
    asia_h: float | None = None,
    asia_l: float | None = None,
    grid_side: str | None = None,
    price: float | None = None,
    when: datetime | None = None,
) -> TradeRecord | None:
    """入场提醒 → 自动记开仓；平仓类提醒 → 尝试平最近一笔未平仓。"""
    if alert_key in CLOSE_ALERT_KEYS:
        if price is None:
            return None
        return close_latest_open(exit_px=float(price), note=f"自动平仓·{alert_key}", when=when)

    if alert_key not in OPEN_ALERT_KEYS:
        return None
    parsed = parse_alert_order(alert_key, message, grid_side=grid_side)
    if not parsed:
        return None
    used_lot = parsed["lot"] if parsed["lot"] is not None else lot
    return add_trade(
        strategy=strategy,
        side=parsed["side"],
        entry=parsed["entry"],
        exit=None,
        sl=parsed["sl"],
        tp=parsed["tp"],
        lot=used_lot,
        result="open",
        note=f"提醒自动记·{alert_key}",
        asia_h=asia_h,
        asia_l=asia_l,
        when=when,
        source="alert",
        alert_key=alert_key,
    )


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
        src = "自动" if t.source == "alert" else "手动"
        lines.append(
            f"- {t.ts[11:16]} {side_cn} {t.entry:.1f}→{exit_txt}  {pnl}  [{t.result}/{src}] {t.strategy}"
        )
    lines.append(f"明细：{TRADES_PATH.name} / {CSV_PATH.name}")
    return "\n".join(lines)
