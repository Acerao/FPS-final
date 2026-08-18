#!/usr/bin/env python3
"""Asia Box desktop monitor: live gold price + A/B entry alerts."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

from alerts import popup_alert
from bar_source import get_indicator_bars
from dashboard import ENTRY_KEYS, build_dashboard
from gold_feed import asia_high_low, fetch_spot, last_closed_m15, load_spot_cache, save_spot_cache, aggregate_bars
from news_calendar import get_news_status
from scale_grid import GRID_MAX_LAYERS, GridState
from spot_history import get_history
from strategy import Box, beijing_now, compute_adx, compute_rsi, session_status

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
PRICE_MS = 2500
BAR_MS = 30000
NEWS_MS = 300000
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


class App:
    def __init__(self) -> None:
        import tkinter as tk

        self.tk = tk
        self.cfg = load_config()
        self.last_alert_key = ""
        self.last_alert_at = 0.0
        self.price_busy = False
        self.bar_busy = False
        self.news_busy = False
        self.last_price: float | None = None
        self.cached_price_source = ""
        self.cached_bars = []
        self.cached_adx = None
        self.cached_rsi = None
        self.cached_last_close = None
        self.cached_auto_box = None
        self.cached_news = get_news_status()
        self.cached_bar_src = ""
        self.cached_bar_note = ""
        self.cached_adx_tf = "M15"
        self._bar_log = ""

        self.root = tk.Tk()
        self.root.title("亚盘盒子监测 · XAUUSD")
        self.root.geometry("700x820")
        self.root.configure(bg="#111318")
        self.root.attributes("-topmost", True)

        fg = "#f5f5f5"
        muted = "#9aa3b2"
        font_ui = ("Microsoft YaHei UI", 10)
        font_big = ("Microsoft YaHei UI", 28, "bold")
        self._font_ui = font_ui
        self._font_big = font_big
        self._font_big_compact = ("Microsoft YaHei UI", 18, "bold")
        self._font_ui_compact = ("Microsoft YaHei UI", 9)

        self.price_var = tk.StringVar(value="--")
        self.mode_var = tk.StringVar(value="启动中")
        self.tick_var = tk.StringVar(value="等待第一次报价…")
        self.indicators_var = tk.StringVar(value="指标加载中…")
        self.news_var = tk.StringVar(value="大数据：加载中…")
        self.msg_var = tk.StringVar(value="正在拉取黄金价格…")
        self.status_var = tk.StringVar(value="")
        self.h_var = tk.StringVar(value=str(self.cfg.get("asia_h", "")))
        self.l_var = tk.StringVar(value=str(self.cfg.get("asia_l", "")))
        self.p_var = tk.StringVar(value=str(self.cfg.get("manual_price", "")))
        self.topmost_var = tk.BooleanVar(value=True)
        self.strategy_var = tk.StringVar(value=self.cfg.get("strategy", "asia_box"))
        self.lot_var = tk.StringVar(value=str(self.cfg.get("lot", "0.02")))
        self.grid_side_var = tk.StringVar(value=self.cfg.get("grid_side", "long"))
        self.grid_info_var = tk.StringVar(value="")

        # 用于“小窗极简显示”的组件引用
        self.price_title_label = tk.Label(self.root, text="现货黄金", fg=muted, bg="#111318", font=font_ui)
        self.price_title_label.pack(pady=(12, 0))
        self.price_label = tk.Label(self.root, textvariable=self.price_var, fg="#f4d35e", bg="#111318", font=font_big)
        self.price_label.pack()
        self.tick_label = tk.Label(self.root, textvariable=self.tick_var, fg="#7ee787", bg="#111318", font=font_ui)
        self.tick_label.pack()
        self.mode_label = tk.Label(self.root, textvariable=self.mode_var, fg="#7ee787", bg="#111318", font=("Microsoft YaHei UI", 15, "bold"))
        self.mode_label.pack(pady=4)

        ind = tk.Label(
            self.root,
            textvariable=self.indicators_var,
            fg=fg,
            bg="#1c212b",
            font=font_ui,
            wraplength=580,
            justify="left",
            padx=12,
            pady=10,
        )
        self.ind_label = ind
        self.ind_label.pack(fill="x", padx=16, pady=6)

        news = tk.Label(
            self.root,
            textvariable=self.news_var,
            fg="#ffb86c",
            bg="#2a1f12",
            font=font_ui,
            wraplength=580,
            justify="left",
            padx=12,
            pady=8,
        )
        self.news_label = news
        self.news_label.pack(fill="x", padx=16, pady=4)

        msg = tk.Label(
            self.root,
            textvariable=self.msg_var,
            fg=fg,
            bg="#1c212b",
            font=font_ui,
            wraplength=580,
            justify="left",
            padx=12,
            pady=10,
        )
        self.msg_label = msg
        self.msg_label.pack(fill="x", padx=16, pady=6)
        self.chart = tk.Canvas(
            self.root,
            width=660,
            height=230,
            bg="#0f1219",
            highlightthickness=1,
            highlightbackground="#2e3440",
        )
        self.chart.pack(fill="x", padx=16, pady=(2, 6))

        strat = tk.Frame(self.root, bg="#111318")
        self.strat_frame = strat
        strat.pack(fill="x", padx=16, pady=(8, 0))
        tk.Label(strat, text="策略", fg=muted, bg="#111318").pack(side="left")
        self.strategy_box = tk.OptionMenu(
            strat,
            self.strategy_var,
            "asia_box",
            "asia_box_hwr",
            "asia_box_sprint",
            "asia_box_lines",
            "asia_box_lines_h1",
            "asia_box_dual_lines_hwr",
            "scale_grid",
            command=lambda _: self.on_strategy_change(),
        )
        self.strategy_box.pack(side="left", padx=6)
        tk.Label(strat, text="手数", fg=muted, bg="#111318").pack(side="left", padx=(12, 0))
        self.lot_box = tk.OptionMenu(
            strat,
            self.lot_var,
            "0.02",
            "0.03",
            "0.05",
            "0.07",
            command=lambda _: self.on_lot_change(),
        )
        self.lot_box.pack(side="left", padx=6)
        tk.Label(
            strat,
            text="高胜率=确认K  冲刺=只做B+加大手  画线=K线+趋势线",
            fg=muted,
            bg="#111318",
            font=("Microsoft YaHei UI", 8),
        ).pack(side="left")

        form = tk.Frame(self.root, bg="#111318")
        self.form_frame = form
        form.pack(fill="x", padx=16)
        tk.Label(form, text="ASIA_H", fg=muted, bg="#111318").grid(row=0, column=0, sticky="w")
        tk.Entry(form, textvariable=self.h_var, width=10).grid(row=0, column=1, padx=4)
        tk.Label(form, text="ASIA_L", fg=muted, bg="#111318").grid(row=0, column=2, sticky="w")
        tk.Entry(form, textvariable=self.l_var, width=10).grid(row=0, column=3, padx=4)
        tk.Button(form, text="保存盒子", command=self.save_box).grid(row=0, column=4, padx=4)
        tk.Button(form, text="自动盒子", command=self.clear_box).grid(row=0, column=5, padx=2)

        form2 = tk.Frame(self.root, bg="#111318")
        self.form2_frame = form2
        form2.pack(fill="x", padx=16, pady=(4, 0))
        tk.Label(form2, text="MT5现价", fg=muted, bg="#111318").grid(row=0, column=0, sticky="w")
        tk.Entry(form2, textvariable=self.p_var, width=10).grid(row=0, column=1, padx=4)
        tk.Button(form2, text="应用现价", command=self.apply_manual_price).grid(row=0, column=2, padx=4)
        tk.Label(
            form2,
            text="网络断时填 MT5 报价，仍可看位置/入场",
            fg=muted,
            bg="#111318",
            font=("Microsoft YaHei UI", 9),
        ).grid(row=0, column=3, columnspan=3, sticky="w", padx=4)

        gridf = tk.Frame(self.root, bg="#111318")
        self.grid_frame = gridf
        gridf.pack(fill="x", padx=16, pady=(6, 0))
        tk.Label(gridf, text="网格方向", fg=muted, bg="#111318").grid(row=0, column=0, sticky="w")
        tk.OptionMenu(gridf, self.grid_side_var, "long", "short").grid(row=0, column=1, padx=4)
        tk.Button(gridf, text="开始本轮", command=self.start_grid).grid(row=0, column=2, padx=2)
        tk.Button(gridf, text="记+1层", command=self.add_grid_layer).grid(row=0, column=3, padx=2)
        tk.Button(gridf, text="结束本轮", command=self.end_grid).grid(row=0, column=4, padx=2)
        tk.Label(
            gridf,
            text="long=跌了加多  short=涨了加空；等手数禁止翻倍",
            fg=muted,
            bg="#111318",
            font=("Microsoft YaHei UI", 8),
        ).grid(row=1, column=0, columnspan=5, sticky="w", pady=(2, 0))

        opts = tk.Frame(self.root, bg="#111318")
        self.opts_frame = opts
        opts.pack(fill="x", padx=16, pady=8)
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
        tk.Button(opts, text="测试提醒", command=self.test_alert).pack(side="right", padx=4)
        tk.Button(opts, text="更新程序", command=self.update_program).pack(side="right", padx=4)
        tk.Button(opts, text="运行自测", command=self.run_selftest).pack(side="right")

        self.status_label = tk.Label(self.root, textvariable=self.status_var, fg=muted, bg="#111318", font=("Microsoft YaHei UI", 9))
        self.status_label.pack(side="bottom", pady=6)
        self.log = tk.Text(self.root, height=7, bg="#0d1017", fg="#c8d0dc", insertbackground="white", borderwidth=0)
        self.log.pack(fill="both", expand=True, padx=16, pady=(0, 10))

        # 监听缩放：小窗时只保留实时金价
        self._compact_mode = False
        self.root.bind("<Configure>", self._on_resize)

        # 最小化时：用一个“极小置顶 Toplevel”继续显示金价
        self.compact_win = tk.Toplevel(self.root)
        self.compact_win.withdraw()
        self.compact_win.overrideredirect(True)
        self.compact_win.attributes("-topmost", True)
        self.compact_win.configure(bg="#111318")
        self.compact_price_label = tk.Label(
            self.compact_win,
            textvariable=self.price_var,
            fg="#f4d35e",
            bg="#111318",
            font=self._font_big_compact,
        )
        self.compact_price_label.pack(padx=10, pady=(10, 2))
        self.compact_tick_label = tk.Label(
            self.compact_win,
            textvariable=self.tick_var,
            fg="#7ee787",
            bg="#111318",
            font=self._font_ui_compact,
        )
        self.compact_tick_label.pack(padx=10, pady=(0, 8))

        # 允许拖拽移动小框：记录鼠标相对窗口左上角的偏移
        self._compact_drag_dx = 0
        self._compact_drag_dy = 0

        def _compact_on_press(evt) -> None:
            try:
                self._compact_drag_dx = evt.x_root - self.compact_win.winfo_x()
                self._compact_drag_dy = evt.y_root - self.compact_win.winfo_y()
            except Exception:
                self._compact_drag_dx = 0
                self._compact_drag_dy = 0

        def _compact_on_motion(evt) -> None:
            try:
                x = evt.x_root - self._compact_drag_dx
                y = evt.y_root - self._compact_drag_dy
                self.compact_win.geometry(f"+{x}+{y}")
            except Exception:
                pass

        # 绑到整个窗口（标签上也会触发，保证好用）
        self.compact_win.bind("<Button-1>", _compact_on_press)
        self.compact_win.bind("<B1-Motion>", _compact_on_motion)
        self.compact_price_label.bind("<Button-1>", _compact_on_press)
        self.compact_price_label.bind("<B1-Motion>", _compact_on_motion)
        self.compact_tick_label.bind("<Button-1>", _compact_on_press)
        self.compact_tick_label.bind("<B1-Motion>", _compact_on_motion)
        self.root.bind("<Unmap>", self._on_unmap)
        self.root.bind("<Map>", self._on_map)

        # 初始判定一次
        self.root.update_idletasks()
        self._set_compact_mode(False)

        # 确保最小化时不会误显示极简窗
        self.compact_win.withdraw()

        self.root.after(300, self.refresh_price)
        self.root.after(800, self.refresh_bars)
        self.root.after(1500, self.refresh_news)

    def toggle_topmost(self) -> None:
        self.root.attributes("-topmost", bool(self.topmost_var.get()))

    def _on_unmap(self, _evt=None) -> None:
        """窗口被最小化/隐藏后：显示极简金价框。"""
        try:
            state = self.root.state()
        except Exception:
            state = ""
        if state == "iconic":
            self._show_compact_toplevel()

    def _on_map(self, _evt=None) -> None:
        """窗口恢复后：隐藏极简金价框。"""
        self.compact_win.withdraw()

    def _show_compact_toplevel(self) -> None:
        try:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            # 放在右上角附近
            x = sw - 190
            y = 20
        except Exception:
            x, y = 10, 10
        # 更小 + 仍可读
        self.compact_win.geometry(f"140x80+{x}+{y}")
        self.compact_win.deiconify()

    def _on_resize(self, _evt=None) -> None:
        """窗口变小后进入“极简实时金价”模式。"""
        try:
            w = self.root.winfo_width()
            h = self.root.winfo_height()
        except Exception:
            return
        compact = w < 360 or h < 240
        self._set_compact_mode(compact)

    def _set_compact_mode(self, compact: bool) -> None:
        if compact == self._compact_mode:
            return
        self._compact_mode = compact

        if compact:
            # 只保留：标题 + 现价 + 小提示（tick）
            self.mode_label.pack_forget()
            self.ind_label.pack_forget()
            self.news_label.pack_forget()
            self.msg_label.pack_forget()
            self.chart.pack_forget()
            self.strat_frame.pack_forget()
            self.form_frame.pack_forget()
            self.form2_frame.pack_forget()
            self.grid_frame.pack_forget()
            self.opts_frame.pack_forget()
            self.status_label.pack_forget()
            self.log.pack_forget()
            self.price_label.configure(font=("Microsoft YaHei UI", 18, "bold"))
            self.tick_label.configure(font=("Microsoft YaHei UI", 9))
            self.root.geometry("360x140")
        else:
            # 恢复原界面
            self.price_label.configure(font=self._font_big)
            self.tick_label.configure(font=self._font_ui)
            self.mode_label.configure(font=("Microsoft YaHei UI", 15, "bold"))
            self.mode_label.pack(pady=4)
            self.ind_label.pack(fill="x", padx=16, pady=6)
            self.news_label.pack(fill="x", padx=16, pady=4)
            self.msg_label.pack(fill="x", padx=16, pady=6)
            self.chart.pack(fill="x", padx=16, pady=(2, 6))
            self.strat_frame.pack(fill="x", padx=16, pady=(8, 0))
            self.form_frame.pack(fill="x", padx=16)
            self.form2_frame.pack(fill="x", padx=16, pady=(4, 0))
            self.grid_frame.pack(fill="x", padx=16, pady=(6, 0))
            self.opts_frame.pack(fill="x", padx=16, pady=8)
            self.status_label.pack(side="bottom", pady=6)
            self.log.pack(fill="both", expand=True, padx=16, pady=(0, 10))
            self.root.geometry("700x820")

    def _parse_manual(self) -> tuple[float | None, float | None]:
        try:
            h = float(self.h_var.get()) if self.h_var.get().strip() else None
            l = float(self.l_var.get()) if self.l_var.get().strip() else None
        except ValueError:
            return None, None
        if h and l and h > l:
            return h, l
        return None, None

    def _parse_manual_price(self) -> float | None:
        raw = self.p_var.get().strip() or str(self.cfg.get("manual_price", "")).strip()
        if not raw:
            return None
        try:
            price = float(raw)
        except ValueError:
            return None
        return price if price > 500 else None

    def _resolve_price(self) -> tuple[float, str]:
        manual = self._parse_manual_price()
        try:
            price, source = fetch_spot()
            return price, source
        except Exception:
            pass
        if manual is not None:
            save_spot_cache(manual, "MT5手动")
            get_history().add(manual, beijing_now())
            return manual, "MT5手动(网络断)"
        cached = load_spot_cache()
        if cached:
            price, src, ts = cached
            age_min = max(0, int((beijing_now() - ts).total_seconds() // 60))
            get_history().add(price, beijing_now())
            return price, f"缓存·{src}({age_min}分钟前)"
        last = get_history().last_tick()
        if last:
            ts, price = last
            age_min = max(0, int((beijing_now() - ts).total_seconds() // 60))
            return price, f"本地采样({age_min}分钟前)"
        raise RuntimeError("无法获取报价：请检查网络，或在 MT5现价 填当前价后点「应用现价」")

    def _grid_state(self) -> GridState:
        try:
            layers = int(self.cfg.get("grid_layers") or 0)
        except (TypeError, ValueError):
            layers = 0
        try:
            anchor = float(self.cfg.get("grid_anchor") or 0)
        except (TypeError, ValueError):
            anchor = 0.0
        side = self.grid_side_var.get() or self.cfg.get("grid_side") or ""
        if side not in {"long", "short"}:
            side = ""
        return GridState(side=side, anchor=anchor, layers=layers)

    def _current_lot(self) -> float:
        from strategy import clamp_lot

        return clamp_lot(self.lot_var.get() or self.cfg.get("lot"))

    def on_lot_change(self) -> None:
        lot = self._current_lot()
        self.lot_var.set(f"{lot:.2f}")
        self.cfg["lot"] = lot
        save_config(self.cfg)
        from strategy import risk_dollars

        self.append_log(f"手数改为 {lot:.2f}（SL$15 约亏 ${risk_dollars(lot):.0f}）")
        if self.last_price is not None:
            self._render(self.last_price, self.cached_price_source or "现货", beijing_now())

    def on_strategy_change(self) -> None:
        self.cfg["strategy"] = self.strategy_var.get()
        if self.cfg["strategy"] == "asia_box_sprint" and self._current_lot() <= 0.021:
            self.lot_var.set("0.05")
            self.cfg["lot"] = 0.05
            self.append_log("冲刺版默认手数改成 0.05（可再手动改）")
        save_config(self.cfg)
        labels = {
            "asia_box": "亚盘盒子",
            "asia_box_hwr": "亚盘盒子·高胜率",
            "asia_box_sprint": "亚盘盒子·冲刺$1k",
            "asia_box_lines": "画线策略·H8风格",
            "asia_box_lines_h1": "画线策略·小时级",
            "asia_box_dual_lines_hwr": "双策略·画线+高胜率",
            "scale_grid": "等距网格",
        }
        self.append_log("已切换策略：" + labels.get(self.cfg["strategy"], self.cfg["strategy"]))
        if self.last_price is not None:
            self._render(self.last_price, self.cached_price_source or "现货", beijing_now())

    def start_grid(self) -> None:
        price = self.last_price or self._parse_manual_price()
        if price is None:
            self.append_log("没有现价，无法开始网格")
            return
        side = self.grid_side_var.get()
        if side not in {"long", "short"}:
            side = "long"
        self.cfg["strategy"] = "scale_grid"
        self.strategy_var.set("scale_grid")
        self.cfg["grid_side"] = side
        self.cfg["grid_anchor"] = round(price, 2)
        self.cfg["grid_layers"] = 1
        save_config(self.cfg)
        self.append_log(f"网格开始：{side} 锚点 {price:.2f} 第1层（手数 0.01）")
        self._render(price, self.cached_price_source or "现货", beijing_now())

    def add_grid_layer(self) -> None:
        st = self._grid_state()
        if not st.active:
            self.append_log("还没开始本轮网格")
            return
        if st.layers >= GRID_MAX_LAYERS:
            self.append_log("已到最大层，不能再加")
            return
        self.cfg["grid_layers"] = st.layers + 1
        save_config(self.cfg)
        self.append_log(f"已记第 {st.layers + 1} 层（请在 MT5 确认已成交）")
        if self.last_price is not None:
            self._render(self.last_price, self.cached_price_source or "现货", beijing_now())

    def end_grid(self) -> None:
        self.cfg["grid_layers"] = 0
        self.cfg["grid_anchor"] = 0
        save_config(self.cfg)
        self.append_log("本轮网格结束（请确认 MT5 已全平）")
        if self.last_price is not None:
            self._render(self.last_price, self.cached_price_source or "现货", beijing_now())

    def _make_box(self, auto):
        h, l = self._parse_manual()
        if not h:
            h = self.cfg.get("asia_h")
        if not l:
            l = self.cfg.get("asia_l")
        if h and l:
            return Box(high=float(h), low=float(l)), "手动"
        if auto:
            return Box(high=auto[0], low=auto[1]), "自动"
        return None, "未锁定"

    def _render(self, price: float, source: str, now) -> None:
        box, box_src = self._make_box(self.cached_auto_box)
        dash = build_dashboard(
            price,
            box,
            box_src,
            self.cached_adx,
            self.cached_rsi,
            self.cached_last_close,
            self.cached_news,
            now,
            self.cached_bar_src,
            self.cached_bar_note,
            getattr(self, "cached_adx_tf", "M15"),
            self.strategy_var.get() or "asia_box",
            self._grid_state(),
            self.cached_bars,
            self._current_lot(),
        )

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
        self.tick_var.set(f"实时 {now:%H:%M:%S}{delta}  |  时段 {dash.session}")
        self.mode_var.set(f"{dash.signal.mode} · {dash.signal.title}")
        self.indicators_var.set(dash.indicators_text)
        self.news_var.set(f"📰 {dash.news.summary}\n{dash.news.detail}")
        self.msg_var.set(dash.signal.message)
        self.status_var.set(f"{now:%H:%M:%S}  {source}")
        self._draw_chart(dash)

        sig = dash.signal
        should_alert = (
            (dash.entry_ok and sig.key in ENTRY_KEYS)
            or sig.key == "news_blackout"
        ) and sig.key not in {"flat", "sleep"}

        if should_alert:
            now_ts = time.time()
            if sig.key != self.last_alert_key or now_ts - self.last_alert_at > ALERT_COOLDOWN_SEC:
                self.last_alert_key = sig.key
                self.last_alert_at = now_ts
                self.append_log(f"【提醒】{sig.title} | {sig.message}")
                popup_alert(sig.title, sig.message, parent=self.root)

    def _draw_chart(self, dash) -> None:
        self.chart.delete("all")
        strategy = self.strategy_var.get()
        if strategy not in {"asia_box_lines", "asia_box_lines_h1", "asia_box_dual_lines_hwr"}:
            self.chart.create_text(
                330,
                115,
                fill="#57606a",
                text="切到画线策略可查看 K线与画线",
                font=("Microsoft YaHei UI", 10),
            )
            return

        # asia_box_lines: 直接用当前缓存 K线（实际为 M15）
        # asia_box_lines_h1: 把 M15 聚合成 H1，用于更贴近“小时级画线单”的尺度
        # asia_box_dual_lines_hwr: 图表按 asia_box_lines（M15）显示
        base_m15 = self.cached_bars[-240:] if self.cached_bars else []
        if strategy == "asia_box_lines_h1":
            bars = aggregate_bars(base_m15, 60)
        else:
            bars = base_m15

        if len(bars) < 20:
            self.chart.create_text(330, 115, fill="#9aa3b2", text="K线不足，等待更多数据后画线", font=("Microsoft YaHei UI", 10))
            return
        w = max(100, self.chart.winfo_width())
        h = max(100, self.chart.winfo_height())
        left, right, top, bottom = 18, w - 18, 14, h - 24
        highs = [float(getattr(b, "high")) for b in bars]
        lows = [float(getattr(b, "low")) for b in bars]
        max_p, min_p = max(highs), min(lows)
        span = max(max_p - min_p, 1.0)

        def px(i: int) -> float:
            return left + (right - left) * i / max(len(bars) - 1, 1)

        def py(v: float) -> float:
            return top + (max_p - v) / span * (bottom - top)

        # Candles
        for i, b in enumerate(bars):
            o = float(getattr(b, "open"))
            c = float(getattr(b, "close"))
            hi = float(getattr(b, "high"))
            lo = float(getattr(b, "low"))
            x = px(i)
            self.chart.create_line(x, py(hi), x, py(lo), fill="#8b949e")
            half = max(1.5, (right - left) / max(len(bars), 1) * 0.35)
            y1, y2 = py(o), py(c)
            color = "#2ea043" if c >= o else "#f85149"
            self.chart.create_rectangle(x - half, min(y1, y2), x + half, max(y1, y2), outline=color, fill=color)

        ov = dash.line_overlay or {}
        up = ov.get("upper_fit")
        dn = ov.get("lower_fit")
        if up:
            y1 = py(up[0] * 0 + up[1])
            y2 = py(up[0] * (len(bars) - 1) + up[1])
            self.chart.create_line(px(0), y1, px(len(bars) - 1), y2, fill="#58a6ff", width=2)
        if dn:
            y1 = py(dn[0] * 0 + dn[1])
            y2 = py(dn[0] * (len(bars) - 1) + dn[1])
            self.chart.create_line(px(0), y1, px(len(bars) - 1), y2, fill="#58a6ff", width=2)
        box_low = ov.get("box_low")
        box_high = ov.get("box_high")
        if box_low is not None and box_high is not None:
            self.chart.create_rectangle(left, py(box_high), right, py(box_low), outline="#d29922", fill="#d29922", stipple="gray25")

        plan = ov.get("plan")
        if isinstance(plan, dict):
            entry = plan.get("entry")
            sl = plan.get("sl")
            tp = plan.get("tp")
            side = plan.get("side", "")
            if all(v is not None for v in (entry, sl, tp)):
                entry_c = "#3fb950"
                sl_c = "#f85149"
                tp_c = "#f2cc60"
                y_e = py(float(entry))
                y_sl = py(float(sl))
                y_tp = py(float(tp))
                self.chart.create_line(left, y_e, right, y_e, fill=entry_c, width=2, dash=(5, 3))
                self.chart.create_line(left, y_sl, right, y_sl, fill=sl_c, width=2, dash=(4, 2))
                self.chart.create_line(left, y_tp, right, y_tp, fill=tp_c, width=2, dash=(4, 2))
                self.chart.create_text(right - 6, y_e - 2, anchor="se", fill=entry_c, text=f"Entry {entry:.1f}", font=("Consolas", 9, "bold"))
                self.chart.create_text(right - 6, y_sl - 2, anchor="se", fill=sl_c, text=f"SL {sl:.1f}", font=("Consolas", 9))
                self.chart.create_text(right - 6, y_tp - 2, anchor="se", fill=tp_c, text=f"TP {tp:.1f}", font=("Consolas", 9))
                if side in {"long", "short"}:
                    self.chart.create_text(left + 8, y_e - 2, anchor="sw", fill=entry_c, text=side.upper(), font=("Consolas", 9, "bold"))
        self.chart.create_text(left + 6, top + 8, anchor="w", fill="#c9d1d9", text=f"{max_p:.1f}", font=("Consolas", 9))
        self.chart.create_text(left + 6, bottom - 8, anchor="w", fill="#c9d1d9", text=f"{min_p:.1f}", font=("Consolas", 9))
        bias = ov.get("bias", "等待")
        self.chart.create_text(right - 4, top + 8, anchor="ne", fill="#f4d35e", text=f"Bias: {bias}", font=("Microsoft YaHei UI", 9, "bold"))

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

    def refresh_news(self) -> None:
        if not self.news_busy:
            self.news_busy = True
            threading.Thread(target=self._poll_news, daemon=True).start()
        self.root.after(NEWS_MS, self.refresh_news)

    def _poll_price(self) -> None:
        try:
            price, source = self._resolve_price()
            now = beijing_now()
            get_history().add(price, now)
            self.root.after(0, lambda: self._apply_price(price, source, now))
        except Exception as exc:
            self.root.after(0, lambda e=str(exc): self._fail_price(e))

    def _poll_bars(self) -> None:
        try:
            pack = get_indicator_bars()
            auto = asia_high_low(pack.bars, beijing_now()) if pack.bars else None
            adx = rsi = last_close = None
            if pack.bars:
                rsi_closes = [b.close for b in pack.bars]
                rsi = compute_rsi(rsi_closes)
                closed = last_closed_m15(pack.bars)
                last_close = closed.close if closed else None
            adx_series = pack.adx_bars or pack.bars
            if adx_series:
                adx = compute_adx(
                    [b.high for b in adx_series],
                    [b.low for b in adx_series],
                    [b.close for b in adx_series],
                )
            self.root.after(
                0,
                lambda: self._apply_bars(
                    pack.source, pack.note, auto, adx, rsi, last_close, pack.adx_tf, pack.bars
                ),
            )
        except Exception as exc:
            self.root.after(0, lambda e=str(exc): self._fail_bars(e))

    def _poll_news(self) -> None:
        try:
            news = get_news_status()
            self.root.after(0, lambda: self._apply_news(news))
        except Exception as exc:
            self.root.after(0, lambda e=str(exc): self._fail_news(e))

    def _fail_price(self, err: str) -> None:
        self.price_busy = False
        self.status_var.set(f"报价失败：{err}")
        self.tick_var.set("报价失败 — 请填 MT5现价 并点「应用现价」")
        if err != self._bar_log:
            self.append_log(f"报价失败：{err}")

    def _fail_bars(self, err: str) -> None:
        self.bar_busy = False
        msg = f"K线刷新异常：{err}（不影响现价；本地采样会继续积累）"
        if msg != self._bar_log:
            self._bar_log = msg
            self.append_log(msg)

    def _fail_news(self, err: str) -> None:
        self.news_busy = False
        self.news_var.set(f"📰 日历拉取失败：{err}")

    def _apply_bars(self, bar_src, bar_note, auto, adx, rsi, last_close, adx_tf="M15", bars=None) -> None:
        self.bar_busy = False
        self.cached_adx = adx
        self.cached_rsi = rsi
        self.cached_last_close = last_close
        self.cached_auto_box = auto
        self.cached_bars = list(bars or [])
        self.cached_bar_src = bar_src
        self.cached_bar_note = bar_note
        self.cached_adx_tf = adx_tf
        log_line = f"{bar_src} | {bar_note}" if bar_note else bar_src
        if log_line and log_line != self._bar_log:
            self._bar_log = log_line
            self.append_log(log_line)
        if self.last_price is not None:
            self._render(self.last_price, self.cached_price_source or "现货", beijing_now())

    def _apply_news(self, news) -> None:
        self.news_busy = False
        self.cached_news = news

    def _apply_price(self, price, source, now) -> None:
        self.price_busy = False
        self.cached_price_source = source
        self._render(price, source, now)

    def apply_manual_price(self) -> None:
        price = self._parse_manual_price()
        if price is None:
            self.append_log("MT5现价无效，请输入如 4410.5")
            return
        self.cfg["manual_price"] = price
        save_config(self.cfg)
        save_spot_cache(price, "MT5手动")
        now = beijing_now()
        get_history().add(price, now)
        self.append_log(f"已应用 MT5 现价 {price:.2f}（网络断时自动使用）")
        self._apply_price(price, "MT5手动", now)

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

    def update_program(self) -> None:
        self.append_log("正在从 GitHub 更新，并同步到 E:\\gold\\asia-box-alert …")
        self.root.update_idletasks()
        try:
            from updater import update_from_github

            msg = update_from_github()
            self.append_log(msg.replace("\n", " | "))
            popup_alert("更新完成", msg + "\n\n建议关掉窗口后重新打开一次。", parent=self.root)
        except Exception as exc:
            from sync_local import sync_to_mirror

            ok, sync_msg = sync_to_mirror()
            self.append_log(f"云端更新失败：{exc}")
            self.append_log(sync_msg)
            popup_alert(
                "更新失败",
                f"GitHub 拉不到（可稍后重试）。\n{sync_msg}\n\n{exc}",
                parent=self.root,
            )

    def test_alert(self) -> None:
        popup_alert(
            "亚盘盒子测试",
            "如果你能看到这个对话框，说明提醒正常。\n推荐入场时会同样弹窗。",
            parent=self.root,
        )
        self.append_log("已发送测试提醒（应弹出对话框）")

    def run_selftest(self) -> None:
        from selftest import run_selftest

        self.append_log("开始自测…")
        code = run_selftest(popup=True)
        self.append_log("自测完成：全部通过" if code == 0 else "自测完成：有失败项，请看控制台")

    def append_log(self, line: str) -> None:
        stamp = beijing_now().strftime("%H:%M:%S")
        self.log.insert("end", f"{stamp}  {line}\n")
        self.log.see("end")

    def run(self) -> None:
        self.root.mainloop()


def print_once() -> None:
    cfg = load_config()
    manual = cfg.get("manual_price")
    try:
        price, source = fetch_spot()
    except Exception:
        if manual and float(manual) > 500:
            price, source = float(manual), "MT5手动"
        else:
            cached = load_spot_cache()
            if not cached:
                raise
            price, src, ts = cached
            source = f"缓存·{src}"
    now = beijing_now()
    get_history().add(price, now)
    box = None
    box_src = "未锁定"
    if cfg.get("asia_h") and cfg.get("asia_l"):
        box = Box(high=cfg["asia_h"], low=cfg["asia_l"])
        box_src = "手动"
    pack = get_indicator_bars()
    adx = rsi = last_close = None
    if pack.bars:
        rsi = compute_rsi([b.close for b in pack.bars])
        closed = last_closed_m15(pack.bars)
        last_close = closed.close if closed else None
    adx_series = pack.adx_bars or pack.bars
    if adx_series:
        adx = compute_adx(
            [b.high for b in adx_series],
            [b.low for b in adx_series],
            [b.close for b in adx_series],
        )
    dash = build_dashboard(
        price,
        box,
        box_src,
        adx,
        rsi,
        last_close,
        None,
        now,
        pack.source,
        pack.note,
        pack.adx_tf,
        cfg.get("strategy", "asia_box"),
        None,
        pack.bars,
        cfg.get("lot", 0.02),
    )
    print(dash.indicators_text)
    print(dash.news.summary)
    print(f"[{dash.signal.mode}] {dash.signal.title}")
    print(dash.signal.message)
    print(f"报价来源: {source}")


def main() -> None:
    parser = argparse.ArgumentParser(description="亚盘盒子黄金监测")
    parser.add_argument("--once", action="store_true", help="只打印一次状态")
    parser.add_argument("--test", action="store_true", help="运行自测")
    args = parser.parse_args()
    if args.test:
        from selftest import run_selftest

        sys.exit(run_selftest(popup="--popup" in sys.argv))
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
        if "--once" not in sys.argv and "--test" not in sys.argv:
            try:
                input("按回车退出...")
            except EOFError:
                pass
        sys.exit(1)
