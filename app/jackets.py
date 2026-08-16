"""曲绘映射：把数据库里的歌名映射到 Illu_Scrapy/jackets/ 下的曲绘文件。"""

from __future__ import annotations

import re
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
JACKET_DIR = BASE_DIR / "Illu_Scrapy" / "jackets"

_lock = threading.Lock()
_index: list[tuple[str, str]] | None = None  # [(规范化文件名, 文件名)]

# 少数歌曲在歌曲列表里显示名相同但实际是不同曲目，本地文件名无法区分，显式指定。
# 键为规范化后的歌名，值为 jackets/ 下的文件名。
_EXPLICIT: dict[str, str] = {
    "anothermeneutralmoon": "330_Another Me_current.webp",
    "anothermedaan": "308_Another Me_jacket.webp",
}


def _norm(s: str) -> str:
    """只保留字母数字并小写，用于模糊匹配歌名与文件名。"""
    return re.sub(r"[\W_]+", "", s or "").lower()


def _build_index() -> list[tuple[str, str]]:
    global _index
    if _index is None:
        with _lock:
            if _index is None:
                files: list[tuple[str, str]] = []
                if JACKET_DIR.is_dir():
                    for p in JACKET_DIR.iterdir():
                        if not p.is_file() or p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                            continue
                        stem = p.stem
                        # 去掉 “NNN_” 序号前缀
                        if len(stem) > 4 and stem[:3].isdigit() and stem[3] == "_":
                            stem = stem[4:]
                        files.append((_norm(stem), p.name))
                _index = files
    return _index


def refresh_index() -> None:
    """重新扫描曲绘目录（曲绘更新后调用）。"""
    global _index
    with _lock:
        _index = None


def jacket_filename(song_name: str) -> str | None:
    """返回歌名对应的曲绘文件名；没有匹配返回 None。

    匹配规则：文件名规范化后以歌名的规范形式开头，优先选择不带额外后缀、
    _jacket 或 _current 的文件（即当前版本），其次是 _old 等，最后取最短匹配。
    """
    key = _norm(song_name)
    if not key:
        return None
    if key in _EXPLICIT:
        fname = _EXPLICIT[key]
        if any(f[1] == fname for f in _build_index()):
            return fname
    best: tuple[int, int, str] | None = None
    for nstem, fname in _build_index():
        if not nstem.startswith(key):
            continue
        rest = nstem[len(key):]
        if rest in ("", "jacket", "current"):
            score = 0
        elif rest in ("old",) or rest.startswith("current") or rest.startswith("jacket"):
            score = 1
        else:
            score = 2
        cand = (score, len(rest), fname)
        if best is None or cand < best:
            best = cand
    return best[2] if best else None


def jacket_url(song_name: str) -> str | None:
    """返回曲绘的静态 URL（/jackets/xxx），没有匹配返回 None。"""
    fname = jacket_filename(song_name)
    return f"/jackets/{fname}" if fname else None
