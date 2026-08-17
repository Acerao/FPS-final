#!/usr/bin/env python3
"""Asia Box desktop monitor: live gold price + A/B entry alerts."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from gold_feed import asia_high_low, fetch_snapshot, last_closed_m15
from strategy import Box, beijing_now, compute_adx, evaluate, session_status

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
POLL_MS = 8000
ALERT_COOLDOWN_SEC = 180


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def popup_alert(title: str, message: str) -> None:
    try:
        from winotify import Notification

        toast = Notification(app_id="Asia Box", title=title, msg=message, duration="long")
        toast.set_audio("ms-winsoundevent:Notification.Looping.Alarm2", loop=False)
        toast.show()
        return
    except Exception:
        pass
    try:
        import winsound

        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        pass
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk._default_root
        if root is not None:
            root.bell()
            messagebox.showinfo(title, message)
    except Exception:
        print(f"[ALERT] {title}: {message}")


def build_state(manual_h: float | None, manual_l: float | None):
    snap = fetch_snapshot()
    now = beijing_now()
    auto = asia_high_low(snap.bars_15m, now)
    if manual_h and manual_l:
        box = Box(high=manual_h, low=manual_l)
        box_src = "手动"
    elif auto:
        box = Box(high=auto[0], low=auto[1])
        box_src = "自动(现货校准)"
    else:
        box = None
        box_src = "未锁定"

    adx = None
    last_close = None
    if snap.bars_15m:
        highs = [b.high for b in snap.bars_15m]
        lows = [b.low for b in snap.bars_15m]
        closes = [b.close for b in snap.bars_15m]
        adx = compute_adx(highs, lows, closes)
        closed = last_closed_m15(snap.bars_15m)
        last_close = closed.close if closed else None

    signal = evaluate(snap.price, box, adx, last_close, now=now)
    return snap, box, box_src, adx, signal, now


def print_once() -> None:
    cfg = load_config()
    snap, box, box_src, adx, signal, now = build_state(cfg.get("asia_h"), cfg.get("asia_l"))
    print(f"北京时间 {now:%H:%M:%S}  时段 {session_status(now)}")
    print(f"现价 {snap.price:.2f}  来源 {snap.source}")
    if box:
        print(
            f"盒子[{box_src}] H={box.high:.2f} L={box.low:.2f} "
            f"上沿 {box.upper_start:.2f}-{box.high:.2f} 下沿 {box.low:.2f}-{box.lower_end:.2f}"
        )
    else:
        print("盒子未锁定")
    if adx:
        print(f"ADX {adx.adx:.1f}  +DI {adx.plus_di:.1f}  -DI {adx.minus_di:.1f}")
    print(f"[{signal.mode}] {signal.title}")
    print(signal.message)
    if snap.warning:
        print("注意:", snap.warning)


class App:
    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.cfg = load_config()
        self.last_alert_key = ""
        self.last_alert_at = 0.0
        self.busy = False

        self.root = tk.Tk()
        self.root.title("亚盘盒子监测 · XAUUSD")
        self.root.geometry("560x520")
        self.root.configure(bg="#111318")
        self.root.attributes("-topmost", True)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        fg = "#f5f5f5"
        muted = "#9aa3b2"
        font_ui = ("Microsoft YaHei UI", 11)
        font_big = ("Microsoft YaHei UI", 28, "bold")

        self.price_var = tk.StringVar(value="--")
        self.mode_var = tk.StringVar(value="启动中")
        self.box_var = tk.StringVar(value="盒子：未锁定")
        self.adx_var = tk.StringVar(value="ADX：--")
        self.msg_var = tk.StringVar(value="正在拉取黄金价格…")
        self.status_var = tk.StringVar(value="")
        self.h_var = tk.StringVar(value=str(self.cfg.get("asia_h", "")))
        self.l_var = tk.StringVar(value=str(self.cfg.get("asia_l", "")))
        self.topmost_var = tk.BooleanVar(value=True)

        tk.Label(self.root, text="现货黄金", fg=muted, bg="#111318", font=font_ui).pack(pady=(16, 0))
        tk.Label(self.root, textvariable=self.price_var, fg="#f4d35e", bg="#111318", font=font_big).pack()
        tk.Label(self.root, textvariable=self.mode_var, fg="#7ee787", bg="#111318", font=("Microsoft YaHei UI", 16, "bold")).pack(pady=4)
        tk.Label(self.root, textvariable=self.box_var, fg=fg, bg="#111318", font=font_ui).pack()
        tk.Label(self.root, textvariable=self.adx_var, fg=muted, bg="#111318", font=font_ui).pack()

        msg = tk.Label(
            self.root,
            textvariable=self.msg_var,
            fg=fg,
            bg="#1c212b",
            font=font_ui,
            wraplength=500,
            justify="left",
            padx=12,
            pady=12,
        )
        msg.pack(fill="x", padx=18, pady=12)

        form = tk.Frame(self.root, bg="#111318")
        form.pack(fill="x", padx=18)
        tk.Label(form, text="ASIA_H", fg=muted, bg="#111318").grid(row=0, column=0, sticky="w")
        tk.Entry(form, textvariable=self.h_var, width=12).grid(row=0, column=1, padx=6)
        tk.Label(form, text="ASIA_L", fg=muted, bg="#111318").grid(row=0, column=2, sticky="w")
        tk.Entry(form, textvariable=self.l_var, width=12).grid(row=0, column=3, padx=6)
        tk.Button(form, text="保存盒子", command=self.save_box).grid(row=0, column=4, padx=6)
        tk.Button(form, text="用自动盒子", command=self.clear_box).grid(row=0, column=5)

        opts = tk.Frame(self.root, bg="#111318")
        opts.pack(fill="x", padx=18, pady=8)
        tk.Checkbutton(
            opts,
            text="窗口置顶",
            variable=self.topmost_var,
            bg="#111318",
            fg=fg,
            selectcolor="#111318",
            activebackground="#111318",
            activeforeground=fg,
            command=self.toggle_topmost,
        ).pack(side="left")
        tk.Button(opts, text="测试提醒", command=self.test_alert).pack(side="right")

        tk.Label(self.root, textvariable=self.status_var, fg=muted, bg="#111318", font=("Microsoft YaHei UI", 9)).pack(
            side="bottom", pady=8
        )
        self.log = tk.Text(self.root, height=8, bg="#0d1017", fg="#c8d0dc", insertbackground="white", borderwidth=0)
        self.log.pack(fill="both", expand=True, padx=18, pady=(0, 12))

        self.root.after(400, self.refresh)

    def toggle_topmost(self) -> None:
        self.root.attributes("-topmost", bool(self.topmost_var.get()))

    def _parse_manual(self) -> tuple[float | None, float | None]:
        try:
            h = float(self.h_var.get()) if self.h_var.get().strip() else None
            l = float(self.l_var.get()) if self.l_var.get().strip() else None
        except ValueError:
            return None, None
        if h and l and h > l:
            return h, l
        return None, None

    def save_box(self) -> None:
        h, l = self._parse_manual()
        if not h or not l:
            self.append_log("盒子数字无效，需要 H > L")
            return
        self.cfg["asia_h"] = h
        self.cfg["asia_l"] = l
        save_config(self.cfg)
        self.append_log(f"已保存手动盒子 H={h} L={l}")
        self.refresh()

    def clear_box(self) -> None:
        self.cfg.pop("asia_h", None)
        self.cfg.pop("asia_l", None)
        save_config(self.cfg)
        self.h_var.set("")
        self.l_var.set("")
        self.append_log("改回自动盒子")
        self.refresh()

    def test_alert(self) -> None:
        popup_alert("亚盘盒子测试", "提醒通道正常。到点会弹出同样窗口。")
        self.append_log("已发送测试提醒")

    def append_log(self, line: str) -> None:
        stamp = beijing_now().strftime("%H:%M:%S")
        self.log.insert("end", f"{stamp}  {line}\n")
        self.log.see("end")

    def refresh(self) -> None:
        if self.busy:
            self.root.after(POLL_MS, self.refresh)
            return
        self.busy = True
        threading.Thread(target=self._poll, daemon=True).start()

    def _poll(self) -> None:
        try:
            h, l = self._parse_manual()
            if not h:
                h = self.cfg.get("asia_h")
            if not l:
                l = self.cfg.get("asia_l")
            snap, box, box_src, adx, signal, now = build_state(h, l)
            self.root.after(0, lambda: self._apply(snap, box, box_src, adx, signal, now))
        except Exception as exc:
            self.root.after(0, lambda: self._fail(str(exc)))

    def _fail(self, err: str) -> None:
        self.busy = False
        self.status_var.set(f"拉取失败：{err}")
        self.append_log(err)
        self.root.after(POLL_MS, self.refresh)

    def _apply(self, snap, box, box_src, adx, signal, now) -> None:
        self.busy = False
        self.price_var.set(f"{snap.price:,.2f}")
        self.mode_var.set(f"{signal.mode} · {signal.title}")
        if box:
            self.box_var.set(
                f"盒子[{box_src}]  H {box.high:.1f}  L {box.low:.1f}  "
                f"上沿 {box.upper_start:.1f}-{box.high:.1f}  下沿 {box.low:.1f}-{box.lower_end:.1f}"
            )
        else:
            self.box_var.set("盒子：未锁定（14:30后自动，或手动填写）")
        if adx:
            self.adx_var.set(f"ADX {adx.adx:.1f}    +DI {adx.plus_di:.1f}    -DI {adx.minus_di:.1f}")
        else:
            self.adx_var.set("ADX：样本不足")
        self.msg_var.set(signal.message)
        warn = snap.warning or ""
        self.status_var.set(f"{now:%H:%M:%S}  {snap.source}  {warn}")

        if signal.urgent:
            now_ts = time.time()
            if signal.key != self.last_alert_key or now_ts - self.last_alert_at > ALERT_COOLDOWN_SEC:
                self.last_alert_key = signal.key
                self.last_alert_at = now_ts
                self.append_log(f"{signal.title} | {signal.message}")
                popup_alert(signal.title, signal.message)
        self.root.after(POLL_MS, self.refresh)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="亚盘盒子黄金监测")
    parser.add_argument("--once", action="store_true", help="只打印一次状态，不打开窗口")
    args = parser.parse_args()
    if args.once:
        print_once()
        return
    App().run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
