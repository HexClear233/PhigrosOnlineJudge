"""数据模型：用户、会话、歌曲、赛事、赛事谱面、提交。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class SessionToken(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    user: Mapped[User] = relationship()


class Song(Base):
    __tablename__ = "songs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    artist: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ez_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    hd_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    in_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    at_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    sp_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    def level_of(self, difficulty: str) -> float | None:
        return {
            "EZ": self.ez_level,
            "HD": self.hd_level,
            "IN": self.in_level,
            "AT": self.at_level,
            "SP": self.sp_level,
        }.get(difficulty.upper())


class Contest(Base):
    __tablename__ = "contests"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    contest_type: Mapped[str] = mapped_column(String(16), default="topic")  # topic / free_choice
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    top_n: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 自选曲阈值 N
    is_sealed: Mapped[bool] = mapped_column(Boolean, default=False)
    seal_reveal_time: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )  # 揭榜时间：到点自动解除封榜
    version: Mapped[str | None] = mapped_column(String(16), nullable=True)  # 对应游戏版本，如 3.19.5
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    creator: Mapped[User] = relationship()
    songs: Mapped[list[ContestSong]] = relationship(
        back_populates="contest", cascade="all, delete-orphan", order_by="ContestSong.sort_order"
    )

    @property
    def is_open(self) -> bool:
        now = datetime.now()
        return self.start_time <= now <= self.end_time


class ContestSong(Base):
    __tablename__ = "contest_songs"
    __table_args__ = (UniqueConstraint("contest_id", "song_id", "difficulty"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    contest_id: Mapped[int] = mapped_column(ForeignKey("contests.id"), nullable=False)
    song_id: Mapped[int] = mapped_column(ForeignKey("songs.id"), nullable=False)
    difficulty: Mapped[str] = mapped_column(String(8), nullable=False)
    chart_level: Mapped[float] = mapped_column(Float, nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    contest: Mapped[Contest] = relationship(back_populates="songs")
    song: Mapped[Song] = relationship()


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    contest_id: Mapped[int] = mapped_column(ForeignKey("contests.id"), nullable=False, index=True)
    contest_song_id: Mapped[int | None] = mapped_column(ForeignKey("contest_songs.id"), nullable=True)
    song_id: Mapped[int | None] = mapped_column(ForeignKey("songs.id"), nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String(8), nullable=True)
    chart_level: Mapped[float | None] = mapped_column(Float, nullable=True)

    score: Mapped[int] = mapped_column(Integer, nullable=False)
    accuracy: Mapped[float] = mapped_column(Float, nullable=False)
    perfect: Mapped[int | None] = mapped_column(Integer, nullable=True)
    good: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bad: Mapped[int | None] = mapped_column(Integer, nullable=True)
    miss: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_combo: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rank: Mapped[str | None] = mapped_column(String(8), nullable=True)
    rks: Mapped[float] = mapped_column(Float, nullable=False)

    source: Mapped[str] = mapped_column(String(16), default="manual")  # ocr / manual
    status: Mapped[str] = mapped_column(String(16), default="approved")  # pending / approved / rejected
    image_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    image_path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_note: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # 榜单撤回（withdrawn 状态）；撤回后不计入排行榜
    withdrawn_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    withdraw_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)

    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    withdrawn_actor: Mapped[User | None] = relationship(foreign_keys="Submission.withdrawn_by")

    user: Mapped[User] = relationship(foreign_keys="Submission.user_id")
    contest: Mapped[Contest] = relationship()
    contest_song: Mapped[ContestSong | None] = relationship()
    song: Mapped[Song | None] = relationship()


class RecognitionLog(Base):
    """一次截图 OCR 识别的日志，按赛事聚合。

    status: staged(识别完成未确认) / failed(识别失败) / submitted(选手已确认并关联提交)
    action: confirm(直接入榜) / review(提交审核) / None(未选择或失败)
    """

    __tablename__ = "recognition_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    contest_id: Mapped[int] = mapped_column(
        ForeignKey("contests.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    submission_id: Mapped[int | None] = mapped_column(
        ForeignKey("submissions.id"), nullable=True
    )

    # 识别数据（OCR 原始结果）
    image_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image_path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    song_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    song_name_raw: Mapped[str | None] = mapped_column(String(128), nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String(8), nullable=True)
    chart_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    rks: Mapped[float | None] = mapped_column(Float, nullable=True)
    matched_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    warnings: Mapped[str | None] = mapped_column(Text, nullable=True)

    action: Mapped[str | None] = mapped_column(String(16), nullable=True)  # confirm / review
    status: Mapped[str] = mapped_column(String(16), default="staged")  # staged/failed/submitted
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(foreign_keys="RecognitionLog.user_id")
    contest: Mapped[Contest] = relationship()
    submission: Mapped[Submission | None] = relationship()
