"""Pull latest asia-box-alert from GitHub, then mirror to E:\\gold\\asia-box-alert.

Private repos can't use anonymous zip downloads, so we prefer `git pull`.
Falls back to zip download (works if the repo is public or a token is set).
"""

from __future__ import annotations

import io
import os
import ssl
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

from sync_local import ROOT, copy_program_files, sync_to_mirror

UA = {"User-Agent": "Mozilla/5.0 AsiaBoxAlert/updater"}
REPO_URL = "https://github.com/Acerao/FPS-final.git"
BRANCH = "cursor/asia-box-scalp-playbook-dbcf"

ZIP_URLS = [
    f"https://github.com/Acerao/FPS-final/archive/refs/heads/{BRANCH}.zip",
    f"https://codeload.github.com/Acerao/FPS-final/zip/refs/heads/{BRANCH}",
    "https://github.com/Acerao/FPS-final/archive/refs/heads/main.zip",
]


def _ssl_contexts():
    yield ssl.create_default_context()
    yield ssl._create_unverified_context()


def _find_git() -> str | None:
    for name in ("git", "git.exe"):
        try:
            subprocess.run([name, "--version"], capture_output=True, timeout=5)
            return name
        except Exception:
            pass
    return None


def _git_pull() -> str | None:
    """Try `git pull` in the repo root (parent of asia-box-alert). Returns message or None."""
    git = _find_git()
    if not git:
        return None
    repo_root = ROOT.parent
    git_dir = repo_root / ".git"
    if not git_dir.exists():
        return None
    try:
        r = subprocess.run(
            [git, "pull", "origin", BRANCH],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(repo_root),
        )
        if r.returncode == 0:
            return f"git pull 成功：{r.stdout.strip()}"
        r2 = subprocess.run(
            [git, "pull"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(repo_root),
        )
        if r2.returncode == 0:
            return f"git pull 成功：{r2.stdout.strip()}"
    except Exception:
        pass
    return None


def _download_zip() -> bytes:
    last: Exception | None = None
    for url in ZIP_URLS:
        for ctx in _ssl_contexts():
            try:
                req = Request(url, headers=UA)
                with urlopen(req, timeout=25, context=ctx) as resp:
                    data = resp.read()
                if len(data) > 1000:
                    return data
            except Exception as exc:
                last = exc
    raise RuntimeError(f"下载失败: HTTP Error 404: Not Found\n仓库是 private，请用 git pull 或把仓库改成 public。\n原始错误: {last}") from last


def _extract_alert_dir(data: bytes) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="asiabox-"))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(tmp)
    matches = list(tmp.rglob("app.py"))
    for hit in matches:
        if hit.parent.name == "asia-box-alert":
            return hit.parent
    if matches:
        return matches[0].parent
    raise RuntimeError("压缩包里没有 asia-box-alert/app.py")


def update_from_github() -> str:
    git_msg = _git_pull()
    if git_msg:
        ok, mirror_msg = sync_to_mirror(ROOT)
        return git_msg + "\n" + (mirror_msg if ok else f"本机目录同步跳过：{mirror_msg}")
    data = _download_zip()
    src = _extract_alert_dir(data)
    copied = copy_program_files(src, ROOT)
    lines = [f"已从 GitHub 更新 {len(copied)} 个文件 → {ROOT}"]
    ok, msg = sync_to_mirror(ROOT)
    lines.append(msg if ok else f"本机目录同步跳过：{msg}")
    return "\n".join(lines)


def main() -> int:
    try:
        print(update_from_github())
        return 0
    except Exception as exc:
        print(f"自动更新失败（不影响启动）：{exc}")
        ok, msg = sync_to_mirror()
        print(msg)
        return 0 if ok else 0


if __name__ == "__main__":
    sys.exit(main())
