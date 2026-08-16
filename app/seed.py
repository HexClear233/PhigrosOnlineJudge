"""初始数据：从 diff_board CSV 导入歌曲库。"""

from __future__ import annotations

import csv

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import SONGS_CSV
from app.models import Song


def seed_songs(db: Session) -> int:
    """从 PHI 难度表导入歌曲（已存在则跳过）。返回新增数量。"""
    if db.scalar(select(func.count(Song.id))):
        return 0
    if not SONGS_CSV.exists():
        return 0

    count = 0
    with open(SONGS_CSV, encoding="utf-8") as f:
        for row in csv.reader(f):
            if not row or row[0].strip().lower() in ("no.", ""):
                continue
            # No., Song name, EZ, HD, IN, AT
            name = row[1].strip() if len(row) > 1 else ""
            if not name:
                continue

            def lv(idx: int) -> float | None:
                if len(row) <= idx or not row[idx].strip():
                    return None
                try:
                    return float(row[idx])
                except ValueError:
                    return None

            db.add(
                Song(
                    name=name,
                    ez_level=lv(2),
                    hd_level=lv(3),
                    in_level=lv(4),
                    at_level=lv(5),
                )
            )
            count += 1
    db.commit()
    return count
