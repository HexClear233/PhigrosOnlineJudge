# -*- coding: utf-8 -*-
"""榜单聚合计算：课题曲矩阵 + 自选曲矩阵（每首歌一列）。"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.jackets import jacket_url
from app.models import Contest, ContestSong, Song, Submission, User


def _norm(s: str) -> str:
    """只保留字母数字并小写，用于歌名排序。"""
    return re.sub(r"[\W_]+", "", s or "").lower()


def _best_rks_by_user(db: Session, contest: Contest) -> Dict[int, Dict[Any, float]]:
    """每个用户按聚合键（课题曲: contest_song_id / 自选曲: (song_id, difficulty)）取最高 RKS。"""
    best: Dict[int, Dict[Any, float]] = defaultdict(dict)
    subs = db.scalars(
        select(Submission).where(
            Submission.contest_id == contest.id,
            Submission.status == "approved",
        )
    ).all()
    for s in subs:
        if contest.contest_type == "topic":
            if s.contest_song_id is None:
                continue
            key: Any = s.contest_song_id
        else:
            if s.song_id is None or s.difficulty is None:
                continue
            key = (s.song_id, s.difficulty.upper())
        prev = best[s.user_id].get(key)
        if prev is None or s.rks > prev:
            best[s.user_id][key] = s.rks
    return best


def _build_topic_board(
    db: Session, contest: Contest, best: Dict[int, Dict[Any, float]], users: Dict[int, User]
) -> Dict[str, Any]:
    """课题曲赛事：每首课题曲一列，表头带曲绘。"""
    contest_songs: List[ContestSong] = list(contest.songs)
    songs = [
        {
            "id": cs.id,
            "name": cs.song.name,
            "difficulty": cs.difficulty,
            "chart_level": cs.chart_level,
            "weight": cs.weight,
            "jacket": jacket_url(cs.song.name),
        }
        for cs in contest_songs
    ]
    rows = []
    for user_id, cells in best.items():
        cell_list = []
        total = 0.0
        max_rks = 0.0
        for cs in contest_songs:
            rks = cells.get(cs.id)
            weighted = (rks or 0.0) * cs.weight
            total += weighted
            if rks:
                max_rks = max(max_rks, rks)
            cell_list.append(
                {
                    "contest_song_id": cs.id,
                    "rks": round(rks, 6) if rks is not None else None,
                    "weighted": round(weighted, 6) if rks is not None else None,
                }
            )
        rows.append(
            {
                "user": users[user_id],
                "total": round(total, 6),
                "max_rks": round(max_rks, 6),
                "cells": cell_list,
            }
        )
    rows.sort(key=lambda r: (-r["total"], -r["max_rks"], r["user"].username))
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return {"songs": songs, "rows": rows, "columns": []}


def _build_free_choice_board(
    db: Session, contest: Contest, best: Dict[int, Dict[Any, float]], users: Dict[int, User]
) -> Dict[str, Any]:
    """自选曲赛事：A/B/C/D/E... 固定 N 列（类似 ACM 每题一列），
    每个选手把成绩从高到低“填左空右”，成绩格用曲绘背景指示曲目。"""
    top_n = min(contest.top_n or 5, 26)
    columns = [chr(ord("A") + i) for i in range(top_n)]
    song_names = {
        s.id: s.name
        for s in db.scalars(select(Song).where(Song.id.in_({k[0] for c in best.values() for k in c}))).all()
    }

    rows = []
    for user_id, cells in best.items():
        entries = sorted(
            [
                {
                    "song_id": key[0],
                    "difficulty": key[1],
                    "song_name": song_names.get(key[0], str(key[0])),
                    "rks": round(rks, 6),
                }
                for key, rks in cells.items()
            ],
            key=lambda e: -e["rks"],
        )[:top_n]
        padded = entries + [None] * (top_n - len(entries))  # 填左空右
        cell_list = []
        for e in padded:
            if e is None:
                cell_list.append({"song_name": None, "difficulty": None, "rks": None, "jacket": None})
            else:
                cell_list.append(
                    {
                        "song_name": e["song_name"],
                        "difficulty": e["difficulty"],
                        "rks": e["rks"],
                        "jacket": jacket_url(e["song_name"]),
                    }
                )
        rows.append(
            {
                "user": users[user_id],
                "cells": cell_list,
                "total": round(sum(e["rks"] for e in entries), 6),
            }
        )
    rows.sort(key=lambda r: (-r["total"], r["user"].username))
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return {"songs": [], "rows": rows, "columns": columns}


def build_leaderboard(db: Session, contest: Contest) -> Dict[str, Any]:
    """构建榜单数据：songs（列，课题曲=课题曲/自选曲=出现的歌）+ rows（rank/user/total/cells）。"""
    best = _best_rks_by_user(db, contest)
    user_ids = set(best.keys())
    if not user_ids:
        return {"songs": [], "rows": [], "columns": []}

    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(user_ids))).all()}
    if contest.contest_type == "topic":
        return _build_topic_board(db, contest, best, users)
    return _build_free_choice_board(db, contest, best, users)
