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

from gold_feed import asia_high_low, fetch_snapshot, fetch_spot, last_closed_m15
from strategy import Box, beijing_now, compute_adx, evaluate, session_status

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
PRICE_MS = 2500
BAR_MS = 30000
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
        self.price_busy = False
        self.bar_busy = False
        self.last_price: float | None = None
        self.cached_bars = []
        self.cached_adx = None
        self.cached_last_close = None
        self.cached_auto_box = None
        self._bar_log = ""

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
        self.tick_var = tk.StringVar(value="等待第一次报价…")
        tk.Label(self.root, textvariable=self.tick_var, fg="#7ee787", bg="#111318", font=("Microsoft YaHei UI", 10)).pack()
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

        self.root.after(300, self.refresh_price)
        self.root.after(800, self.refresh_bars)

    def refresh_price(self) -> None:
        if not self.price_busy:
            self.price_busy = True
            threading.Thread(target=self._poll_price, daemon=True).start()
        self.root.after(PRICE_MS, self.refresh_price)

    def refresh_bars(self) -> None:
        if not self.bar_busy:
            self.bar_busy = True
            threading.Thread(target=self._poll_bars, daemon=True).start()
        self.root.after(BAR_MS, self.refresh_bars)

    def _manual_hl(self) -> tuple[float | None, float | None]:
        h, l = self._parse_manual()
        if not h:
            h = self.cfg.get("asia_h")
        if not l:
            l = self.cfg.get("asia_l")
        return h, l

    def _make_box(self, auto):
        h, l = self._manual_hl()
        if h and l:
            return Box(high=float(h), low=float(l)), "手动"
        if auto:
            return Box(high=auto[0], low=auto[1]), "自动"
        return None, "未锁定"

    def _poll_price(self) -> None:
        try:
            price, source = fetch_spot()
            now = beijing_now()
            box, box_src = self._make_box(self.cached_auto_box)
            signal = evaluate(price, box, self.cached_adx, self.cached_last_close, now=now)
            self.root.after(0, lambda: self._apply_price(price, source, box, box_src, signal, now))
        except Exception as exc:
            self.root.after(0, lambda e=str(exc): self._fail_price(e))

    def _poll_bars(self) -> None:
        try:
            snap = fetch_snapshot()
            auto = asia_high_low(snap.bars_15m, beijing_now())
            adx = None
            last_close = None
            if snap.bars_15m:
                adx = compute_adx(
                    [b.high for b in snap.bars_15m],
                    [b.low for b in snap.bars_15m],
                    [b.close for b in snap.bars_15m],
                )
                closed = last_closed_m15(snap.bars_15m)
                last_close = closed.close if closed else None
            self.root.after(0, lambda: self._apply_bars(snap, auto, adx, last_close))
        except Exception as exc:
            self.root.after(0, lambda e=str(exc): self._fail_bars(e))

    def _fail_price(self, err: str) -> None:
        self.price_busy = False
        self.status_var.set(f"报价失败：{err}")

    def _fail_bars(self, err: str) -> None:
        self.bar_busy = False
        self.adx_var.set("ADX：K线暂不可用，现货仍在刷新")
        msg = "K线暂不可用，请手动填 ASIA_H / ASIA_L（现货价不受影响）"
        if msg != self._bar_log:
            self._bar_log = msg
            self.append_log(msg)

    def _apply_bars(self, snap, auto, adx, last_close) -> None:
        self.bar_busy = False
        self.cached_bars = snap.bars_15m
        self.cached_adx = adx
        self.cached_last_close = last_close
        self.cached_auto_box = auto
        if adx:
            self.adx_var.set(f"ADX {adx.adx:.1f}    +DI {adx.plus_di:.1f}    -DI {adx.minus_di:.1f}")
        if snap.warning and snap.warning != self._bar_log:
            self._bar_log = snap.warning
            self.append_log(snap.warning)

    def _apply_price(self, price, source, box, box_src, signal, now) -> None:
        self.price_busy = False
        delta = ""
        if self.last_price is not None:
            diff = price - self.last_price
            if diff > 0:
                delta = f"  ▲{diff:.2f}"
            elif diff < 0:
                delta = f"  ▼{abs(diff):.2f}"
            else:
                delta = "  ="
        self.last_price = price
        self.price_var.set(f"{price:,.2f}")
        self.tick_var.set(f"实时 {now:%H:%M:%S}{delta}  每2.5秒刷新")
        self.mode_var.set(f"{signal.mode} · {signal.title}")
        if box:
            self.box_var.set(
                f"盒子[{box_src}]  H {box.high:.1f}  L {box.low:.1f}  "
                f"上沿 {box.upper_start:.1f}-{box.high:.1f}  下沿 {box.low:.1f}-{box.lower_end:.1f}"
            )
        else:
            self.box_var.set("盒子：未锁定（14:30后自动，或手动填写）")
        self.msg_var.set(signal.message)
        self.status_var.set(f"{now:%H:%M:%S}  {source}  时段 {session_status(now)}")

        if signal.urgent and signal.key not in {"flat", "sleep"}:
            now_ts = time.time()
            if signal.key != self.last_alert_key or now_ts - self.last_alert_at > ALERT_COOLDOWN_SEC:
                self.last_alert_key = signal.key
                self.last_alert_at = now_ts
                self.append_log(f"{signal.title} | {signal.message}")
                popup_alert(signal.title, signal.message)

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

    def clear_box(self) -> None:
        self.cfg.pop("asia_h", None)
        self.cfg.pop("asia_l", None)
        save_config(self.cfg)
        self.h_var.set("")
        self.l_var.set("")
        self.append_log("改回自动盒子")

    def test_alert(self) -> None:
        popup_alert("亚盘盒子测试", "提醒通道正常。到点会弹出同样窗口。")
        self.append_log("已发送测试提醒")

    def append_log(self, line: str) -> None:
        stamp = beijing_now().strftime("%H:%M:%S")
        self.log.insert("end", f"{stamp}  {line}\n")
        self.log.see("end")

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
    except Exception:
        import traceback

        tb = traceback.format_exc()
        log_path = ROOT / "error.log"
        try:
            log_path.write_text(tb, encoding="utf-8")
        except OSError:
            pass
        print(tb)
        print("错误已写入:", log_path)
        if "--once" not in sys.argv:
            try:
                input("按回车退出...")
            except EOFError:
                pass
        sys.exit(1)
