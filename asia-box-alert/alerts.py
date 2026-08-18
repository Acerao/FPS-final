"""Cross-platform alert: always try to show a visible dialog on Windows."""

from __future__ import annotations

import sys
from typing import Any


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


def popup_alert(title: str, message: str, parent: Any = None) -> None:
    """Show a visible popup. parent should be the tk root when inside the GUI."""
    _beep()

    if parent is not None:
        try:
            parent.lift()
            parent.focus_force()
        except Exception:
            pass

    shown = False
    try:
        from tkinter import messagebox

        if parent is not None:
            messagebox.showinfo(title, message, parent=parent)
        else:
            messagebox.showinfo(title, message)
        shown = True
    except Exception:
        pass

    if not shown:
        _win_messagebox(title, message)

    _toast(title, message)
