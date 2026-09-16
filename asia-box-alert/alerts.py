"""Cross-platform alert: non-modal topmost popups that can be closed together."""

from __future__ import annotations

import sys
from typing import Any

# 当前未关的提醒窗（仅 tk Toplevel）
_open_alert_windows: list[Any] = []


def _win_messagebox(title: str, message: str) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, title, 0x40)  # MB_ICONINFORMATION
        return True
    except Exception:
        return False


def _toast(title: str, message: str) -> None:
    try:
        from winotify import Notification

        n = Notification(app_id="Asia Box", title=title, msg=message[:240], duration="long")
        n.show()
    except Exception:
        pass


def _beep() -> None:
    try:
        import winsound

        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        pass


def _forget(win: Any) -> None:
    try:
        _open_alert_windows.remove(win)
    except ValueError:
        pass


def close_all_alert_windows() -> int:
    """关闭所有提醒弹窗，返回关掉的数量。不影响主窗口/极简金价窗。"""
    closed = 0
    for win in list(_open_alert_windows):
        try:
            if win.winfo_exists():
                win.destroy()
                closed += 1
        except Exception:
            pass
        _forget(win)
    _open_alert_windows.clear()
    return closed


def open_alert_count() -> int:
    alive = []
    for win in list(_open_alert_windows):
        try:
            if win.winfo_exists():
                alive.append(win)
            else:
                _forget(win)
        except Exception:
            _forget(win)
    _open_alert_windows[:] = alive
    return len(alive)


def popup_alert(title: str, message: str, parent: Any = None) -> None:
    """
    弹出非模态置顶提醒窗（不卡住主界面）。
    多个提醒可叠在一起，用 close_all_alert_windows() 一键全关。
    """
    _beep()

    if parent is None:
        # 无主窗时退回系统对话框
        shown = False
        try:
            from tkinter import messagebox

            messagebox.showinfo(title, message)
            shown = True
        except Exception:
            pass
        if not shown:
            _win_messagebox(title, message)
        _toast(title, message)
        return

    try:
        import tkinter as tk
    except Exception:
        _win_messagebox(title, message)
        _toast(title, message)
        return

    try:
        parent.lift()
    except Exception:
        pass

    win = tk.Toplevel(parent)
    win.title(title)
    win.configure(bg="#1c212b")
    try:
        win.attributes("-topmost", True)
    except Exception:
        pass
    win.resizable(True, True)

    outer = tk.Frame(win, bg="#1c212b", padx=14, pady=12)
    outer.pack(fill="both", expand=True)

    tk.Label(
        outer,
        text=title,
        fg="#f4d35e",
        bg="#1c212b",
        font=("Microsoft YaHei UI", 11, "bold"),
        anchor="w",
        justify="left",
        wraplength=420,
    ).pack(fill="x", pady=(0, 8))

    tk.Label(
        outer,
        text=message,
        fg="#f5f5f5",
        bg="#1c212b",
        font=("Microsoft YaHei UI", 10),
        anchor="nw",
        justify="left",
        wraplength=420,
    ).pack(fill="both", expand=True)

    btns = tk.Frame(outer, bg="#1c212b")
    btns.pack(fill="x", pady=(12, 0))

    def close_me() -> None:
        _forget(win)
        try:
            win.destroy()
        except Exception:
            pass

    def close_all() -> None:
        close_all_alert_windows()

    tk.Button(btns, text="关闭全部弹窗", command=close_all).pack(side="left")
    tk.Button(btns, text="知道了", command=close_me).pack(side="right")

    win.protocol("WM_DELETE_WINDOW", close_me)
    _open_alert_windows.append(win)

    try:
        win.update_idletasks()
        # 稍微错开位置，避免完全重叠
        n = max(0, len(_open_alert_windows) - 1)
        x = 80 + (n % 6) * 28
        y = 80 + (n % 6) * 28
        win.geometry(f"460x280+{x}+{y}")
        win.lift()
    except Exception:
        pass

    _toast(title, message)
