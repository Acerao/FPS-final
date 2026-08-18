"""Sync program files to the Windows trading PC folder.

Cloud agents cannot write E:\\gold. On the user's PC, run.bat / 更新程序
will copy the latest files into E:\\gold\\asia-box-alert automatically.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_MIRROR = Path(r"E:\gold\asia-box-alert")
MIRROR_FILE = ROOT / "local_mirror.txt"

# User state — never overwrite when syncing/updating
KEEP_NAMES = {
    "config.json",
    "price_ticks.json",
    "last_spot.json",
    "error.log",
    "local_mirror.txt",
}

SKIP_DIR_NAMES = {"__pycache__", ".venv", ".git"}

COPY_SUFFIXES = {
    ".py",
    ".bat",
    ".vbs",
    ".ps1",
    ".md",
    ".txt",
    ".json",
    ".gitignore",
}


def mirror_path() -> Path:
    if MIRROR_FILE.exists():
        raw = MIRROR_FILE.read_text(encoding="utf-8").strip()
        if raw:
            return Path(raw)
    return DEFAULT_MIRROR


def should_copy(path: Path) -> bool:
    if path.name in KEEP_NAMES:
        return False
    if path.suffix.lower() in COPY_SUFFIXES or path.name == ".gitignore":
        return True
    return False


def copy_program_files(src: Path, dest: Path) -> list[str]:
    """Copy code/config templates. Preserve dest user data files."""
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for item in src.iterdir():
        if item.name in SKIP_DIR_NAMES:
            continue
        if item.is_dir():
            continue
        if not should_copy(item):
            continue
        target = dest / item.name
        shutil.copy2(item, target)
        copied.append(item.name)
    return copied


def sync_to_mirror(src: Path | None = None) -> tuple[bool, str]:
    src = (src or ROOT).resolve()
    dest = mirror_path()
    if os.name != "nt":
        return False, "非 Windows，跳过 E:\\gold 同步。你电脑上双击 run.bat 或点「更新程序」即可写入。"
    try:
        if dest.exists() and dest.resolve() == src:
            return True, f"已在本机目录运行：{dest}"
        copied = copy_program_files(src, dest)
        return True, f"已同步 {len(copied)} 个文件 → {dest}"
    except OSError as exc:
        return False, f"无法写入 {dest}：{exc}"
