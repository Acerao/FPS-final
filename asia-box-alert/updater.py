"""Pull latest asia-box-alert from GitHub, then mirror to E:\\gold\\asia-box-alert."""

from __future__ import annotations

import io
import ssl
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

from sync_local import ROOT, copy_program_files, sync_to_mirror

UA = {"User-Agent": "Mozilla/5.0 AsiaBoxAlert/updater"}

ZIP_URLS = [
    "https://github.com/Acerao/FPS-final/archive/refs/heads/cursor/asia-box-scalp-playbook-dbcf.zip",
    "https://codeload.github.com/Acerao/FPS-final/zip/refs/heads/cursor/asia-box-scalp-playbook-dbcf",
    "https://github.com/Acerao/FPS-final/archive/refs/heads/main.zip",
]


def _ssl_contexts():
    yield ssl.create_default_context()
    yield ssl._create_unverified_context()


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
    raise RuntimeError(f"下载失败: {last}") from last


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
