"""应用配置与路径。"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = Path(os.environ.get("PHIGROS_OJ_DB", DATA_DIR / "phigros_oj.db"))
DATABASE_URL = f"sqlite:///{DB_PATH}"

SONGS_CSV = BASE_DIR / "diff_board" / "3.19.5" / "PHI_3.19.5.csv"

SESSION_COOKIE = "phigros_oj_session"
SESSION_MAX_AGE = 7 * 24 * 3600  # 7 天

HOST = "127.0.0.1"
PORT = 8000
