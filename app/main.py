"""Phigros OJ MVP：赛事网页与服务器。"""

from __future__ import annotations

import hashlib
import mimetypes
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import (
    create_session,
    current_user,
    delete_session,
    hash_password,
    verify_password,
)
from app.config import SESSION_COOKIE, SESSION_MAX_AGE, UPLOAD_DIR
from app.db import SessionLocal, get_db, init_db
from app.leaderboard import build_leaderboard
from app.models import Contest, ContestSong, RecognitionLog, Song, Submission, User
from app.seed import seed_songs
from ocr.analyzer import analyze_settlement
from ocr.rks import calculate_rks

BASE_DIR = Path(__file__).resolve().parent.parent
mimetypes.add_type("image/webp", ".webp")
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))

# OCR 识别结果暂存：上传识别后按 image_hash 暂存，确认时由服务端直接读取，
# 选手看不到、也无法修改识别数据（防止篡改），确认只提交 image_hash + action。
# 单进程内存表，上传→确认在短时间内完成；进程重启后需重新上传。
_OCR_STAGING: Dict[str, Dict[str, Any]] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        seed_songs(db)
    finally:
        db.close()
    yield


app = FastAPI(title="Phigros OJ", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

# 曲绘静态目录（Illu_Scrapy/jackets），供榜单界面使用
JACKETS_DIR = BASE_DIR / "Illu_Scrapy" / "jackets"
if JACKETS_DIR.is_dir():
    app.mount("/jackets", StaticFiles(directory=str(JACKETS_DIR)), name="jackets")


# ---------- 工具 ----------


def _render(request: Request, name: str, context: Dict[str, Any], status_code: int = 200) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request=request, name=name, context=context, status_code=status_code
    )


def _available_versions() -> List[str]:
    """从 diff_board/ 目录读取可用游戏版本号；无则用默认 3.19.5。"""
    diff_dir = BASE_DIR / "diff_board"
    if diff_dir.is_dir():
        try:
            vers = sorted(
                p.name for p in diff_dir.iterdir()
                if p.is_dir() and p.name[0].isdigit()
            )
        except OSError:
            vers = []
        if vers:
            return vers
    return ["3.19.5"]


DEFAULT_VERSION = "3.19.5"


def _chart_table_data(db: Session, contest: Contest) -> List[Dict[str, Any]]:
    """本场比赛可供选手使用的谱面定数表（课题曲=赛事谱面，自选曲=歌曲库）。

    返回 [{song_name, difficulty, chart_level}]，供提交界面的可查询定数表使用。
    """
    rows: List[Dict[str, Any]] = []
    if contest.contest_type == "topic":
        for cs in contest.songs:
            rows.append(
                {
                    "song_name": cs.song.name,
                    "difficulty": cs.difficulty,
                    "chart_level": cs.chart_level,
                }
            )
    else:
        for song in db.scalars(select(Song).order_by(Song.name)).all():
            for diff in ("EZ", "HD", "IN", "AT", "SP"):
                lv = song.level_of(diff)
                if lv is not None:
                    rows.append(
                        {"song_name": song.name, "difficulty": diff, "chart_level": lv}
                    )
    return rows


def _page_context(request: Request, db: Session, **extra: Any) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {
        "request": request,
        "user": current_user(request, db),
        "now": datetime.now(),
    }
    ctx.update(extra)
    return ctx


def _norm(text: Optional[str]) -> str:
    return re.sub(r"[\W_]+", "", text or "").lower()


def _parse_score(text: str) -> int:
    return int(re.sub(r"[,\s]", "", text.strip()))


def _parse_float(text: str) -> float:
    return float(text.strip().rstrip("%"))


def _parse_int_or_none(text: Optional[str]) -> Optional[int]:
    if text is None or not text.strip():
        return None
    try:
        return int(text.strip())
    except ValueError:
        return None


def _require_user(request: Request, db: Session) -> User:
    user = current_user(request, db)
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def _require_admin(request: Request, db: Session) -> User:
    user = _require_user(request, db)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def _is_staff(contest: Contest, user: Optional[User]) -> bool:
    """管理员或赛事创建者（组织者）视为工作人员，可手动录入/审核。"""
    return bool(user and (user.is_admin or contest.created_by == user.id))


def _apply_auto_reveal(db: Session, contest: Contest) -> bool:
    """封榜到揭榜时间时自动解除封榜。

    若赛事处于封榜状态且已到达 seal_reveal_time，则自动置为揭榜并清空揭榜时间。
    返回是否发生了自动揭榜（调用方据此提示用户）。
    """
    if not contest.is_sealed or contest.seal_reveal_time is None:
        return False
    if datetime.now() >= contest.seal_reveal_time:
        contest.is_sealed = False
        contest.seal_reveal_time = None
        db.commit()
        return True
    return False


def _parse_datetime(text: str) -> Optional[datetime]:
    """把表单提交的本地时间字符串解析为 datetime；非法/空返回 None。"""
    if not text or not text.strip():
        return None
    try:
        dt = datetime.fromisoformat(text.strip())
    except ValueError:
        # 兼容去掉秒的 "YYYY-MM-DDTHH:MM"
        for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(text.strip(), fmt)
            except ValueError:
                continue
        return None
    return dt


def _save_submission(
    db: Session,
    user: User,
    contest: Contest,
    *,
    source: str,
    score: int,
    accuracy: float,
    perfect: Optional[int],
    good: Optional[int],
    bad: Optional[int],
    miss: Optional[int],
    max_combo: Optional[int],
    rank: Optional[str],
    contest_song: Optional[ContestSong] = None,
    song: Optional[Song] = None,
    difficulty: Optional[str] = None,
    chart_level: Optional[float] = None,
    image_hash: Optional[str] = None,
    image_path: Optional[str] = None,
    status: str = "approved",
) -> Submission:
    if contest.contest_type == "topic":
        if contest_song is None:
            raise ValueError("课题曲赛事必须选择赛事谱面")
        song = contest_song.song
        difficulty = contest_song.difficulty
        chart_level = contest_song.chart_level
    else:
        if song is None or difficulty is None or chart_level is None:
            raise ValueError("自选曲赛事必须选择歌曲与难度")

    if not (0 <= score <= 1_000_000):
        raise ValueError("分数应在 0 ~ 1,000,000 之间")
    if not (0.0 <= accuracy <= 100.0):
        raise ValueError("ACC 应在 0 ~ 100 之间")

    rks = calculate_rks(accuracy, chart_level)
    sub = Submission(
        user_id=user.id,
        contest_id=contest.id,
        contest_song_id=contest_song.id if contest_song else None,
        song_id=song.id if song else None,
        difficulty=difficulty.upper() if difficulty else None,
        chart_level=chart_level,
        score=score,
        accuracy=accuracy,
        perfect=perfect,
        good=good,
        bad=bad,
        miss=miss,
        max_combo=max_combo,
        rank=rank.upper() if rank else None,
        rks=rks,
        source=source,
        status=status,
        image_hash=image_hash,
        image_path=image_path,
    )
    db.add(sub)
    db.commit()
    return sub


def _contest_song_options(contest: Contest) -> List[Dict[str, Any]]:
    return [
        {
            "id": cs.id,
            "label": f"{cs.song.name} [{cs.difficulty}] Lv.{cs.chart_level}",
            "song_name": cs.song.name,
            "difficulty": cs.difficulty,
            "chart_level": cs.chart_level,
        }
        for cs in contest.songs
    ]


def _library_song_options(db: Session) -> List[Dict[str, Any]]:
    options: List[Dict[str, Any]] = []
    for song in db.scalars(select(Song).order_by(Song.name)).all():
        for diff in ("EZ", "HD", "IN", "AT", "SP"):
            level = song.level_of(diff)
            if level is None:
                continue
            options.append(
                {
                    "value": f"{song.id}|{diff}",
                    "label": f"{song.name} [{diff}] Lv.{level}",
                    "song_name": song.name,
                    "difficulty": diff,
                    "chart_level": level,
                }
            )
    return options


def _match_option(
    options: List[Dict[str, Any]], song_name: Optional[str], difficulty: Optional[str]
) -> Optional[str]:
    """按 OCR 曲名 + 难度匹配选项，返回选项 value/id；无匹配返回 None。

    规则：
    - 曲名匹配是硬条件：只有「精确同名」或「包含/被包含」的选项才有资格；
    - 难度仅在同名候选中作附加区分（曲名匹配但难度也一致者优先），
      绝不允许仅凭难度一致就匹配到不同歌曲（否则会误以为"非课题曲"是本场课题曲）。
    """
    target = _norm(song_name)
    if not target:
        return None
    best: Optional[str] = None
    best_song_score = -1
    best_diff_ok = False
    for opt in options:
        cand = _norm(opt["song_name"])
        # 曲名得分（硬条件）
        song_score = 0
        if cand == target:
            song_score = 10
        elif cand and (cand in target or target in cand):
            song_score = 5
        if song_score <= 0:
            continue  # 曲名不匹配，直接排除（哪怕难度相同）
        diff_ok = bool(
            difficulty and opt["difficulty"].upper() == difficulty.upper()
        )
        # 优先：曲名得分更高者；同分时难度也一致者优先
        better = song_score > best_song_score or (
            song_score == best_song_score and diff_ok and not best_diff_ok
        )
        if better:
            best_song_score = song_score
            best_diff_ok = diff_ok
            best = opt.get("value", opt.get("id"))
    return best


def _matched_chart_level(options: List[Dict[str, Any]], selected: Optional[str]) -> Optional[float]:
    """取匹配到的选项所对应的精确谱面定数（保留 0.1 精度）。

    selected 为选项的 value/id；找不到返回 None。OCR 中难度行 "IN Lv.16" 里的
    数字是取整到个位的定数，而这里返回的是数据库里的精确定数。
    """
    if not selected:
        return None
    for opt in options:
        ref = opt.get("value", opt.get("id"))
        if ref is not None:
            if str(ref) == str(selected):
                return opt.get("chart_level")
    return None


def _topic_warning_ctx(
    contest: Contest, parsed: Any, selected: Optional[str]
) -> Dict[str, Any]:
    """课题曲赛事“识别出非课题曲”的告警上下文。

    返回 dict 含 is_topic / off_topic / topic_song_names，供确认页展示警告与“申诉”入口。
    """
    is_topic = contest.contest_type == "topic"
    song_recognized = bool(parsed and getattr(parsed, "song_name", None))
    off_topic = is_topic and song_recognized and selected is None
    topic_song_names: List[str] = []
    if is_topic:
        topic_song_names = [
            f"{cs.song.name}（{cs.difficulty}）" for cs in contest.songs
        ]
    return {
        "is_topic": is_topic,
        "off_topic": off_topic,
        "topic_song_names": topic_song_names,
    }


def _resolve_ocr_option(
    db: Session, contest: Contest, song_ref: str
) -> Tuple[Optional[ContestSong], Optional[Song], Optional[str], Optional[float]]:
    """把确认时匹配到的 song_ref 解析为 (contest_song, song, difficulty, chart_level)。"""
    contest_song: Optional[ContestSong] = None
    song: Optional[Song] = None
    difficulty: Optional[str] = None
    chart_level: Optional[float] = None
    if not song_ref:
        return None, None, None, None
    if contest.contest_type == "topic":
        contest_song = db.get(ContestSong, int(song_ref))
        if contest_song is None or contest_song.contest_id != contest.id:
            return None, None, None, None
        song = contest_song.song
        difficulty = contest_song.difficulty
        chart_level = contest_song.chart_level
    else:
        song_id_s, diff = song_ref.split("|", 1)
        song = db.get(Song, int(song_id_s))
        difficulty = diff.upper()
        chart_level = song.level_of(difficulty) if song else None
        if song is None or chart_level is None:
            return None, None, None, None
    return contest_song, song, difficulty, chart_level


def _staged_as_result(staged: Dict[str, Any]) -> Any:
    """把暂存的 OCR 结果还原为 SettlementResult，供确认页只读展示。"""
    from ocr.analyzer import SettlementResult, calculate_rks

    accuracy = staged.get("accuracy")
    # 优先使用匹配到的精确谱面定数（0.1 精度），与最终入榜 RKS 计算保持一致；
    # 无匹配时退回 OCR 中难度行取整后的定数。
    chart_level = staged.get("chart_level_display") if staged.get("chart_level_display") is not None else staged.get("chart_level")
    rks = None
    if accuracy is not None and chart_level is not None:
        try:
            rks = calculate_rks(accuracy, chart_level)
        except ValueError:
            rks = None
    return SettlementResult(
        song_name=staged.get("song_name"),
        song_name_raw=staged.get("song_name_raw"),
        difficulty=staged.get("difficulty"),
        chart_level=chart_level,
        score=staged.get("score"),
        accuracy=accuracy,
        max_combo=staged.get("max_combo"),
        perfect=staged.get("perfect"),
        good=staged.get("good"),
        bad=staged.get("bad"),
        miss=staged.get("miss"),
        rank=staged.get("rank"),
        rks=rks,
        warnings=list(staged.get("warnings") or []),
    )


def _log_rks(accuracy: Optional[float], chart_level: Optional[float]) -> Optional[float]:
    """安全计算 OCR 识别日志所用的 RKS；缺任一输入返回 None。"""
    if accuracy is None or chart_level is None:
        return None
    try:
        return round(calculate_rks(accuracy, chart_level), 6)
    except ValueError:
        return None


def _build_log_row(log: RecognitionLog) -> Dict[str, Any]:
    """把一条识别日志转成展示用字典（含关联提交的状态/可撤回信息）。"""
    sub = log.submission
    return {
        "id": log.id,
        "contest_id": log.contest_id,
        "user": log.user.display_name if log.user else "—",
        "song_name": log.song_name,
        "song_name_raw": log.song_name_raw,
        "difficulty": log.difficulty,
        "chart_level": log.chart_level,
        "score": log.score,
        "accuracy": log.accuracy,
        "rks": log.rks,
        "matched_ok": log.matched_ok,
        "warnings": log.warnings,
        "action": log.action,  # confirm / review / None
        "status": log.status,  # staged / failed / submitted
        "has_image": bool(log.image_path),
        "image_path": log.image_path,
        "created_at": log.created_at,
        "confirmed_at": log.confirmed_at,
        # 关联提交
        "submission_id": sub.id if sub else None,
        "sub_status": sub.status if sub else None,
        "sub_rks": round(sub.rks, 4) if sub else None,
        "withdrawn": bool(sub and sub.status == "withdrawn"),
        "withdraw_reason": sub.withdraw_reason if sub else None,
        "withdrawn_at": sub.withdrawn_at if sub else None,
    }


def _record_upload_log(
    db: Session,
    contest: Contest,
    user: User,
    *,
    image_hash: str,
    image_path: str,
    parsed: Any,
    selected: Optional[str],
    chart_level_display: Optional[float] = None,
    failed: bool = False,
) -> RecognitionLog:
    """识别完成时写入识别日志（识别失败也记录）。返回日志对象。"""
    # 谱面定数优先使用匹配到的精确值（0.1 精度），无匹配时退回 OCR 整数
    log_chart_level = chart_level_display if chart_level_display is not None else (parsed.chart_level if parsed else None)
    log = RecognitionLog(
        contest_id=contest.id,
        user_id=user.id,
        image_hash=image_hash,
        image_path=image_path,
        song_name=parsed.song_name if parsed else None,
        song_name_raw=parsed.song_name_raw if parsed else None,
        difficulty=parsed.difficulty if parsed else None,
        chart_level=log_chart_level,
        score=parsed.score if parsed else None,
        accuracy=parsed.accuracy if parsed else None,
        rks=_log_rks(parsed.accuracy, chart_level_display or (parsed.chart_level if parsed else None)) if parsed else None,
        matched_ok=None if failed else (selected is not None),
        warnings="\n".join(parsed.warnings) if parsed and parsed.warnings else None,
        status="failed" if failed else "staged",
    )
    db.add(log)
    db.commit()
    return log


def _update_log_on_confirm(
    db: Session,
    log: RecognitionLog,
    sub: Optional[Submission],
    *,
    action: str,
) -> None:
    """选手确认后更新识别日志：关联提交、动作、状态、确认时间。"""
    log.submission_id = sub.id if sub else None
    log.action = action
    log.status = "submitted"
    log.confirmed_at = datetime.now()
    db.commit()


# ---------- 页面：首页 / 认证 ----------


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    contests = db.scalars(select(Contest).order_by(Contest.created_at.desc())).all()
    for c in contests:  # 服务端惰性自动揭榜：到点即解除封榜
        _apply_auto_reveal(db, c)
    return _render(request, "index.html", _page_context(request, db, contests=contests))


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request, db: Session = Depends(get_db)):
    return _render(request, "register.html", _page_context(request, db))


@app.post("/register", response_class=HTMLResponse)
def register(
    request: Request,
    username: str = Form(...),
    display_name: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    username = username.strip()
    display_name = display_name.strip() or username
    if len(username) < 2 or len(password) < 4:
        return _render(
            request,
            "register.html",
            _page_context(request, db, error="用户名至少 2 个字符，密码至少 4 位"),
            status_code=400,
        )
    if db.scalar(select(User).where(User.username == username)):
        return _render(request, "register.html", _page_context(request, db, error="用户名已存在"), status_code=400)

    is_admin = db.scalar(select(func.count(User.id))) == 0  # 首个注册用户为管理员
    user = User(
        username=username,
        display_name=display_name,
        password_hash=hash_password(password),
        is_admin=is_admin,
    )
    db.add(user)
    db.commit()
    token = create_session(db, user)
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_MAX_AGE, httponly=True)
    return resp


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    return _render(request, "login.html", _page_context(request, db))


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.username == username.strip()))
    if user is None or not verify_password(password, user.password_hash):
        return _render(request, "login.html", _page_context(request, db, error="用户名或密码错误"), status_code=400)
    token = create_session(db, user)
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_MAX_AGE, httponly=True)
    return resp


@app.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        delete_session(db, token)
    resp = RedirectResponse(url="/", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ---------- 赛事管理 ----------


@app.get("/contests/new", response_class=HTMLResponse)
def contest_new_page(request: Request, db: Session = Depends(get_db)):
    _require_admin(request, db)
    songs = db.scalars(select(Song).order_by(Song.name)).all()
    versions = _available_versions()

    # 每个谱面一个条目，共选手选择（一歌多难度即多条）
    charts: List[Dict[str, Any]] = []
    for s in songs:
        for diff in ("EZ", "HD", "IN", "AT", "SP"):
            lv = s.level_of(diff)
            if lv is not None:
                charts.append(
                    {
                        "value": f"{s.id}:{diff}",
                        "song_name": s.name,
                        "difficulty": diff,
                        "chart_level": lv,
                    }
                )

    # 曲绘映射（歌曲名 -> 曲绘 URL），用于“已选窗口”视觉增强；无曲绘为 None
    try:
        from app.jackets import jacket_url
        jackets: Dict[str, str] = {}
        for s in songs:
            url = jacket_url(s.name)
            if url:
                jackets[s.name] = url
    except Exception:  # noqa: BLE001
        jackets = {}

    return _render(
        request,
        "contest_new.html",
        _page_context(
            request,
            db,
            songs=songs,
            charts=charts,
            jackets=jackets,
            versions=versions,
            default_version=DEFAULT_VERSION,
        ),
    )


@app.post("/contests/new")
def contest_create(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    contest_type: str = Form("topic"),
    start_time: str = Form(...),
    end_time: str = Form(...),
    top_n: str = Form(""),
    version: str = Form(""),
    songs: List[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    admin = _require_admin(request, db)
    try:
        start = datetime.fromisoformat(start_time)
        end = datetime.fromisoformat(end_time)
    except ValueError:
        return _render(request, "contest_new.html", _page_context(request, db, error="时间格式不正确"), status_code=400)
    if end <= start:
        return _render(request, "contest_new.html", _page_context(request, db, error="结束时间需晚于开始时间"), status_code=400)
    if contest_type not in ("topic", "free_choice"):
        contest_type = "topic"

    version = version.strip() or DEFAULT_VERSION
    if version not in _available_versions():
        version = DEFAULT_VERSION

    contest = Contest(
        name=name.strip(),
        description=description.strip() or None,
        contest_type=contest_type,
        start_time=start,
        end_time=end,
        top_n=int(top_n) if contest_type == "free_choice" and top_n.strip().isdigit() else None,
        version=version,
        created_by=admin.id,
    )
    db.add(contest)
    db.flush()

    if contest_type == "topic":
        for idx, ref in enumerate(songs):
            parts = ref.split(":")
            if len(parts) != 2:
                continue
            song = db.get(Song, int(parts[0]))
            difficulty = parts[1].upper()
            level = song.level_of(difficulty) if song else None
            if song is None or level is None:
                continue
            db.add(
                ContestSong(
                    contest_id=contest.id,
                    song_id=song.id,
                    difficulty=difficulty,
                    chart_level=level,
                    sort_order=idx,
                )
            )
    db.commit()
    return RedirectResponse(url=f"/contests/{contest.id}", status_code=303)


@app.get("/contests/{contest_id}", response_class=HTMLResponse)
def contest_detail(contest_id: int, request: Request, db: Session = Depends(get_db)):
    contest = db.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="赛事不存在")
    auto_revealed = _apply_auto_reveal(db, contest)
    user = current_user(request, db)
    message = None
    if auto_revealed:
        message = "已到揭榜时间，本场赛事已自动揭榜。"
    elif request.query_params.get("ok") == "submitted":
        rks = request.query_params.get("rks")
        message = f"提交成功！RKS = {rks}" if rks else "提交成功！"
    elif request.query_params.get("ok") == "pending":
        message = "提交成功！成绩已进入审核队列，等待管理员/组织者审核确认后计入榜单。"

    # 课题曲曲绘映射（歌名 -> 曲绘 URL），用于赛事详情页视觉增强；无曲绘为 None
    jackets: dict[str, str] = {}
    if contest.contest_type == "topic":
        try:
            from app.jackets import jacket_url
            for cs in contest.songs:
                if cs.song and cs.song.name:
                    url = jacket_url(cs.song.name)
                    if url:
                        jackets[cs.song.name] = url
        except Exception:  # noqa: BLE001
            jackets = {}

    return _render(
        request,
        "contest.html",
        _page_context(
            request, db, contest=contest, user=user, message=message, jackets=jackets
        ),
    )


@app.post("/contests/{contest_id}/seal")
def contest_seal(
    contest_id: int,
    request: Request,
    reveal_time: str = Form(""),
    db: Session = Depends(get_db),
):
    _require_admin(request, db)
    contest = db.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="赛事不存在")
    contest.is_sealed = True
    contest.seal_reveal_time = _parse_datetime(reveal_time)  # 空则永久封榜，需手动揭榜
    db.commit()
    return RedirectResponse(url=f"/contests/{contest_id}", status_code=303)


@app.post("/contests/{contest_id}/reveal")
def contest_reveal(contest_id: int, request: Request, db: Session = Depends(get_db)):
    _require_admin(request, db)
    contest = db.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="赛事不存在")
    contest.is_sealed = False
    contest.seal_reveal_time = None
    db.commit()
    return RedirectResponse(url=f"/contests/{contest_id}", status_code=303)


# ---------- 成绩提交 ----------


@app.get("/contests/{contest_id}/submit", response_class=HTMLResponse)
def submit_page(contest_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_user(request, db)
    contest = db.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="赛事不存在")
    if not contest.is_open:
        return _render(
            request,
            "submit.html",
            _page_context(
                request,
                db,
                contest=contest,
                error="当前不在赛事开放时间内，无法提交",
                song_options=[],
            ),
        )
    if contest.contest_type == "topic":
        song_options = _contest_song_options(contest)
    else:
        song_options = _library_song_options(db)
    chart_table = _chart_table_data(db, contest)
    return _render(
        request,
        "submit.html",
        _page_context(
            request,
            db,
            contest=contest,
            song_options=song_options,
            chart_table=chart_table,
            is_staff=_is_staff(contest, user),
        ),
    )


@app.post("/contests/{contest_id}/submit/manual")
def submit_manual(
    contest_id: int,
    request: Request,
    song_ref: str = Form(...),
    score: str = Form(...),
    accuracy: str = Form(...),
    perfect: str = Form(""),
    good: str = Form(""),
    bad: str = Form(""),
    miss: str = Form(""),
    max_combo: str = Form(""),
    rank: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    contest = db.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="赛事不存在")
    if not contest.is_open:
        return _render(request, "submit.html", _page_context(request, db, contest=contest, error="赛事已截止"), status_code=400)
    if not _is_staff(contest, user):
        return _render(
            request,
            "submit.html",
            _page_context(
                request,
                db,
                contest=contest,
                is_staff=False,
                error="仅管理员或赛事组织人员可手动录入成绩",
            ),
            status_code=403,
        )

    def fail(error: str):
        options = _contest_song_options(contest) if contest.contest_type == "topic" else _library_song_options(db)
        return _render(
            request,
            "submit.html",
            _page_context(request, db, contest=contest, song_options=options, error=error),
            status_code=400,
        )

    contest_song = None
    song = None
    difficulty = None
    chart_level = None
    try:
        if contest.contest_type == "topic":
            contest_song = db.get(ContestSong, int(song_ref))
            if contest_song is None or contest_song.contest_id != contest.id:
                return fail("请选择有效的赛事谱面")
        else:
            song_id_s, diff = song_ref.split("|", 1)
            song = db.get(Song, int(song_id_s))
            difficulty = diff.upper()
            chart_level = song.level_of(difficulty) if song else None
            if song is None or chart_level is None:
                return fail("请选择有效的歌曲与难度")
        score_v = _parse_score(score)
        accuracy_v = _parse_float(accuracy)
    except (ValueError, TypeError):
        return fail("分数/ACC 格式不正确")

    try:
        sub = _save_submission(
            db,
            user,
            contest,
            source="manual",
            score=score_v,
            accuracy=accuracy_v,
            perfect=_parse_int_or_none(perfect),
            good=_parse_int_or_none(good),
            bad=_parse_int_or_none(bad),
            miss=_parse_int_or_none(miss),
            max_combo=_parse_int_or_none(max_combo),
            rank=rank or None,
            contest_song=contest_song,
            song=song,
            difficulty=difficulty,
            chart_level=chart_level,
        )
    except ValueError as exc:
        return fail(str(exc))

    return RedirectResponse(url=f"/contests/{contest_id}?ok=submitted&rks={sub.rks:.6f}", status_code=303)


@app.post("/contests/{contest_id}/submit/ocr")
async def submit_ocr_upload(
    contest_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    contest = db.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="赛事不存在")
    if not contest.is_open:
        return _render(request, "submit.html", _page_context(request, db, contest=contest, error="赛事已截止"), status_code=400)

    raw = await file.read()
    if not raw:
        return _render(request, "submit.html", _page_context(request, db, contest=contest, error="上传文件为空"), status_code=400)
    image_hash = hashlib.sha256(raw).hexdigest()
    if db.scalar(select(Submission).where(Submission.image_hash == image_hash)):
        return _render(request, "submit.html", _page_context(request, db, contest=contest, error="该截图已被使用过"), status_code=400)

    upload_path = UPLOAD_DIR / f"{image_hash}.png"
    upload_path.write_bytes(raw)
    preview_url = f"/uploads/{image_hash}.png"

    try:
        parsed = analyze_settlement(raw)
    except Exception as exc:  # noqa: BLE001
        # 识别失败也记录日志
        _record_upload_log(db, contest, user,
                           image_hash=image_hash, image_path=preview_url,
                           parsed=None, selected=None, failed=True)
        upload_path.unlink(missing_ok=True)
        return _render(
            request,
            "submit.html",
            _page_context(request, db, contest=contest, error=f"截图识别失败：{exc}"),
            status_code=400,
        )

    # 把识别结果暂存在服务端，按 image_hash 取回；确认页只读展示，选手无法修改。
    options = _contest_song_options(contest) if contest.contest_type == "topic" else _library_song_options(db)
    selected = _match_option(options, parsed.song_name, parsed.difficulty)
    # 谱面定数：用匹配到的谱面的精确定数（0.1 精度），而非 OCR 里难度行取整后的数字
    chart_level_display = _matched_chart_level(options, selected)

    # 课题曲赛事：识别出的曲目不属于本场课题曲 -> 弹出警告，并提供“申诉”进入审核队列
    warn = _topic_warning_ctx(contest, parsed, selected)

    # 记录本次识别日志（staged，选手尚未确认）
    log = _record_upload_log(
        db, contest, user,
        image_hash=image_hash, image_path=preview_url,
        parsed=parsed, selected=selected, chart_level_display=chart_level_display,
    )
    _OCR_STAGING[image_hash] = {
        "contest_id": contest.id,
        "log_id": log.id,
        "song_name": parsed.song_name,
        "song_name_raw": parsed.song_name_raw,
        "difficulty": parsed.difficulty,
        "chart_level": parsed.chart_level,
        "chart_level_display": chart_level_display,
        "score": parsed.score,
        "accuracy": parsed.accuracy,
        "perfect": parsed.perfect,
        "good": parsed.good,
        "bad": parsed.bad,
        "miss": parsed.miss,
        "max_combo": parsed.max_combo,
        "rank": parsed.rank,
        "warnings": parsed.warnings,
        "selected": selected,
        "off_topic": warn["off_topic"],
    }
    return _render(
        request,
        "submit_confirm.html",
        _page_context(
            request,
            db,
            contest=contest,
            parsed=parsed,
            selected=selected,
            matched_ok=selected is not None,
            chart_level_display=chart_level_display,
            **warn,
            image_hash=image_hash,
            preview_url=f"/uploads/{image_hash}.png",
        ),
    )


@app.post("/contests/{contest_id}/submit/ocr/confirm")
def submit_ocr_confirm(
    contest_id: int,
    request: Request,
    image_hash: str = Form(...),
    action: str = Form("confirm"),
    db: Session = Depends(get_db),
):
    """选手确认 OCR 识别结果。

    只接收 image_hash + action，识别数据一律取自服务端暂存，防止选手篡改。
    - action=confirm：识别无误，直接登入榜单（status=approved）
    - action=review ：认为识别有误，提交到审核队列（status=pending）
    """
    user = _require_user(request, db)
    contest = db.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="赛事不存在")
    if not contest.is_open:
        return _render(request, "submit.html", _page_context(request, db, contest=contest, error="赛事已截止"), status_code=400)

    staged = _OCR_STAGING.get(image_hash)
    if staged is None or staged["contest_id"] != contest.id:
        return _render(request, "submit.html", _page_context(request, db, contest=contest, error="识别结果已失效，请重新上传"), status_code=400)
    upload_path = UPLOAD_DIR / f"{image_hash}.png"
    if not upload_path.exists():
        return _render(request, "submit.html", _page_context(request, db, contest=contest, error="临时截图不存在，请重新上传"), status_code=400)

    log = db.get(RecognitionLog, staged.get("log_id"))
    _OCR_STAGING.pop(image_hash, None)

    resolved = _resolve_ocr_option(db, contest, staged["selected"])
    contest_song, song, difficulty, chart_level = resolved
    accuracy = staged.get("accuracy")
    chart_ok = song is not None and chart_level is not None

    if action == "confirm":
        # 直接登入榜单：必须能匹配到有效谱面且 ACC 识别有效，否则要求走审核。
        if not chart_ok:
            warn = _topic_warning_ctx(contest, _staged_as_result(staged), staged["selected"])
            return _render(
                request,
                "submit_confirm.html",
                _page_context(
                    request,
                    db,
                    contest=contest,
                    parsed=_staged_as_result(staged),
                    selected=staged["selected"],
                    matched_ok=False,
                    chart_level_display=staged.get("chart_level_display"),
                    **warn,
                    image_hash=image_hash,
                    preview_url=f"/uploads/{image_hash}.png",
                    error="未能匹配到有效谱面/难度，无法直接登入榜单，请提交审核等待人工修正。",
                ),
                status_code=400,
            )
        if not (0.0 <= (accuracy or -1.0) <= 100.0):
            warn = _topic_warning_ctx(contest, _staged_as_result(staged), staged["selected"])
            return _render(
                request,
                "submit_confirm.html",
                _page_context(
                    request,
                    db,
                    contest=contest,
                    parsed=_staged_as_result(staged),
                    selected=staged["selected"],
                    matched_ok=True,
                    chart_level_display=staged.get("chart_level_display"),
                    **warn,
                    image_hash=image_hash,
                    preview_url=f"/uploads/{image_hash}.png",
                    error="ACC 识别无效，无法直接登入榜单，请提交审核。",
                ),
                status_code=400,
            )

    if action == "confirm":
        try:
            sub = _save_submission(
                db,
                user,
                contest,
                source="ocr",
                score=staged.get("score") or 0,
                accuracy=accuracy,
                perfect=staged.get("perfect"),
                good=staged.get("good"),
                bad=staged.get("bad"),
                miss=staged.get("miss"),
                max_combo=staged.get("max_combo"),
                rank=staged.get("rank"),
                contest_song=contest_song,
                song=song,
                difficulty=difficulty,
                chart_level=chart_level,
                image_hash=image_hash,
                image_path=f"/uploads/{image_hash}.png",
                status="approved",
            )
        except ValueError as exc:
            return _render(
                request,
                "submit_confirm.html",
                _page_context(
                    request,
                    db,
                    contest=contest,
                    parsed=_staged_as_result(staged),
                    selected=staged["selected"],
                    matched_ok=chart_ok,
                    chart_level_display=staged.get("chart_level_display"),
                    image_hash=image_hash,
                    preview_url=f"/uploads/{image_hash}.png",
                    error=str(exc),
                ),
                status_code=400,
            )
        if log:
            _update_log_on_confirm(db, log, sub, action="confirm")
        return RedirectResponse(url=f"/contests/{contest_id}?ok=submitted&rks={sub.rks:.6f}", status_code=303)

    # 识别有误 -> 提交到审核队列：曲目/难度/ACC 可能未识别或匹配失败，
    # 允许曲子字段为空，交由管理员在审核中人工修正；RKS 先以 0 占位（不计入榜单）。
    rks = 0.0
    if accuracy is not None and chart_level is not None:
        try:
            rks = calculate_rks(accuracy, chart_level)
        except ValueError:
            rks = 0.0
    sub = Submission(
        user_id=user.id,
        contest_id=contest.id,
        contest_song_id=contest_song.id if contest_song else None,
        song_id=song.id if song else None,
        difficulty=difficulty,
        chart_level=chart_level,
        score=staged.get("score") or 0,
        accuracy=accuracy if accuracy is not None else 0.0,
        perfect=staged.get("perfect"),
        good=staged.get("good"),
        bad=staged.get("bad"),
        miss=staged.get("miss"),
        max_combo=staged.get("max_combo"),
        rank=staged.get("rank"),
        rks=round(rks, 6),
        source="ocr",
        status="pending",
        image_hash=image_hash,
        image_path=f"/uploads/{image_hash}.png",
    )
    db.add(sub)
    db.commit()
    if log:
        _update_log_on_confirm(db, log, sub, action="review")
    return RedirectResponse(url=f"/contests/{contest_id}?ok=pending", status_code=303)


# ---------- 审核平台（管理员 / 赛事组织者） ----------


def _submission_rows(db: Session, subs: List[Submission]) -> List[Dict[str, Any]]:
    rows = []
    for s in subs:
        song_name = None
        if s.contest_song:
            song_name = s.contest_song.song.name
        elif s.song:
            song_name = s.song.name
        rows.append(
            {
                "id": s.id,
                "contest_id": s.contest_id,
                "contest_name": s.contest.name,
                "user": s.user.display_name,
                "song_name": song_name,
                "difficulty": s.difficulty,
                "chart_level": s.chart_level,
                "score": s.score,
                "accuracy": s.accuracy,
                "rks": s.rks,
                "source": s.source,
                "status": s.status,
                "has_image": bool(s.image_path),
                "withdraw_reason": s.withdraw_reason,
                "submitted_at": s.submitted_at,
            }
        )
    return rows


@app.get("/review", response_class=HTMLResponse)
def review_global(
    request: Request,
    status: str = "pending",
    db: Session = Depends(get_db),
):
    _require_admin(request, db)
    q = select(Submission).order_by(Submission.submitted_at.desc())
    if status in ("pending", "approved", "rejected", "withdrawn"):
        q = q.where(Submission.status == status)
    subs = db.scalars(q).all()
    return _render(
        request,
        "review.html",
        _page_context(request, db, subs=_submission_rows(db, subs), status=status),
    )


@app.get("/contests/{contest_id}/review", response_class=HTMLResponse)
def contest_review(
    contest_id: int,
    request: Request,
    status: str = "pending",
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    contest = db.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="赛事不存在")
    if not _is_staff(contest, user):
        raise HTTPException(status_code=403, detail="需要管理员或赛事组织者权限")
    q = (
        select(Submission)
        .where(Submission.contest_id == contest_id)
        .order_by(Submission.submitted_at.desc())
    )
    if status in ("pending", "approved", "rejected", "withdrawn"):
        q = q.where(Submission.status == status)
    subs = db.scalars(q).all()
    return _render(
        request,
        "review.html",
        _page_context(request, db, contest=contest, subs=_submission_rows(db, subs), status=status),
    )


@app.get("/review/{submission_id}", response_class=HTMLResponse)
def review_detail(submission_id: int, request: Request, db: Session = Depends(get_db)):
    sub = db.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="提交不存在")
    user = _require_user(request, db)
    contest = sub.contest
    if not _is_staff(contest, user):
        raise HTTPException(status_code=403, detail="需要管理员或赛事组织者权限")

    if contest.contest_type == "topic":
        options = _contest_song_options(contest)
        selected = str(sub.contest_song_id) if sub.contest_song_id else ""
    else:
        options = _library_song_options(db)
        selected = f"{sub.song_id}|{sub.difficulty}" if sub.song_id else ""
    return _render(
        request,
        "review_detail.html",
        _page_context(
            request,
            db,
            sub=sub,
            contest=contest,
            options=options,
            selected=selected,
        ),
    )


@app.post("/review/{submission_id}")
def review_save(
    submission_id: int,
    request: Request,
    song_ref: str = Form(...),
    accuracy: str = Form(...),
    score: str = Form(""),
    perfect: str = Form(""),
    good: str = Form(""),
    bad: str = Form(""),
    miss: str = Form(""),
    max_combo: str = Form(""),
    rank: str = Form(""),
    action: str = Form("approved"),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    sub = db.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="提交不存在")
    user = _require_user(request, db)
    contest = sub.contest
    if not _is_staff(contest, user):
        raise HTTPException(status_code=403, detail="需要管理员或赛事组织者权限")

    options = _contest_song_options(contest) if contest.contest_type == "topic" else _library_song_options(db)
    selected = song_ref

    def fail(error: str):
        return _render(
            request,
            "review_detail.html",
            _page_context(
                request,
                db,
                sub=sub,
                contest=contest,
                options=options,
                selected=selected,
                error=error,
            ),
            status_code=400,
        )

    contest_song = None
    song = None
    difficulty = None
    chart_level = None
    try:
        if contest.contest_type == "topic":
            contest_song = db.get(ContestSong, int(song_ref))
            if contest_song is None or contest_song.contest_id != contest.id:
                return fail("请选择有效的赛事谱面")
            song = contest_song.song
            difficulty = contest_song.difficulty
            chart_level = contest_song.chart_level
        else:
            song_id_s, diff = song_ref.split("|", 1)
            song = db.get(Song, int(song_id_s))
            difficulty = diff.upper()
            chart_level = song.level_of(difficulty) if song else None
            if song is None or chart_level is None:
                return fail("请选择有效的歌曲与难度")
        score_v = _parse_score(score) if score.strip() else sub.score
        accuracy_v = _parse_float(accuracy)
    except (ValueError, TypeError) as exc:
        return fail(f"分数/ACC 格式不正确：{exc}")

    if not (0 <= score_v <= 1_000_000):
        return fail("分数应在 0 ~ 1,000,000 之间")
    if not (0.0 <= accuracy_v <= 100.0):
        return fail("ACC 应在 0 ~ 100 之间")

    sub.contest_song_id = contest_song.id if contest_song else None
    sub.song_id = song.id if song else None
    sub.difficulty = difficulty
    sub.chart_level = chart_level
    sub.score = score_v
    sub.accuracy = accuracy_v
    sub.perfect = _parse_int_or_none(perfect)
    sub.good = _parse_int_or_none(good)
    sub.bad = _parse_int_or_none(bad)
    sub.miss = _parse_int_or_none(miss)
    sub.max_combo = _parse_int_or_none(max_combo)
    sub.rank = rank.upper() if rank.strip() else None
    sub.rks = calculate_rks(accuracy_v, chart_level)
    sub.status = "approved" if action == "approved" else "rejected"
    if sub.status == "approved":
        sub.withdrawn_by = None
        sub.withdrawn_at = None
        sub.withdraw_reason = None
    sub.reviewed_by = user.id
    sub.reviewed_at = datetime.now()
    sub.review_note = note.strip() or None
    db.commit()
    return RedirectResponse(url=f"/contests/{contest.id}/review?status=pending", status_code=303)


# ---------- 识别日志与榜单撤回（管理员 / 赛事组织者） ----------


@app.get("/contests/{contest_id}/logs", response_class=HTMLResponse)
def contest_logs(
    contest_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """查阅某场比赛的 OCR 识别日志。"""
    user = _require_user(request, db)
    contest = db.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="赛事不存在")
    if not _is_staff(contest, user):
        raise HTTPException(status_code=403, detail="需要管理员或赛事组织者权限")

    logs = db.scalars(
        select(RecognitionLog)
        .where(RecognitionLog.contest_id == contest_id)
        .order_by(RecognitionLog.created_at.desc())
    ).all()
    return _render(
        request,
        "logs.html",
        _page_context(
            request,
            db,
            contest=contest,
            logs=[_build_log_row(l) for l in logs],
        ),
    )


@app.post("/contests/{contest_id}/logs/{log_id}/withdraw")
def log_withdraw(
    contest_id: int,
    log_id: int,
    request: Request,
    reason: str = Form(""),
    db: Session = Depends(get_db),
):
    """从榜单撤回该识别日志关联的成绩（仅对已通过/已上榜的成绩生效）。"""
    user = _require_user(request, db)
    contest = db.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="赛事不存在")
    if not _is_staff(contest, user):
        raise HTTPException(status_code=403, detail="需要管理员或赛事组织者权限")

    log = db.get(RecognitionLog, log_id)
    if log is None or log.contest_id != contest.id:
        raise HTTPException(status_code=404, detail="识别日志不存在")
    sub = log.submission
    if sub is None:
        raise HTTPException(status_code=400, detail="该日志未关联成绩，无法撤回")
    if sub.status != "approved":
        raise HTTPException(status_code=400, detail="只有已通过（上榜）的成绩才能撤回")

    sub.status = "withdrawn"
    sub.withdrawn_by = user.id
    sub.withdrawn_at = datetime.now()
    sub.withdraw_reason = reason.strip() or "主办方撤回过异常成绩"
    db.commit()
    return RedirectResponse(url=f"/contests/{contest.id}/logs", status_code=303)


@app.post("/contests/{contest_id}/logs/{log_id}/restore")
def log_restore(
    contest_id: int,
    log_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """把已撤回的成绩恢复为已通过（重新计入榜单）。"""
    user = _require_user(request, db)
    contest = db.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="赛事不存在")
    if not _is_staff(contest, user):
        raise HTTPException(status_code=403, detail="需要管理员或赛事组织者权限")

    log = db.get(RecognitionLog, log_id)
    if log is None or log.contest_id != contest.id:
        raise HTTPException(status_code=404, detail="识别日志不存在")
    sub = log.submission
    if sub is None or sub.status != "withdrawn":
        raise HTTPException(status_code=400, detail="没有可恢复的已撤回成绩")

    sub.status = "approved"
    sub.reviewed_by = user.id
    sub.reviewed_at = datetime.now()
    sub.withdraw_reason = None
    db.commit()
    return RedirectResponse(url=f"/contests/{contest.id}/logs", status_code=303)


@app.post("/contests/{contest_id}/logs/{log_id}/edit")
def log_edit(
    contest_id: int,
    log_id: int,
    request: Request,
    song_name: str = Form(""),
    difficulty: str = Form(""),
    chart_level: str = Form(""),
    accuracy: str = Form(""),
    score: str = Form(""),
    db: Session = Depends(get_db),
):
    """管理员/赛事组织者修正识别日志中的识别错误（重点是曲名识别错误）。

    仅当某字段填了内容时才更新该字段；song_name_raw 保留原始 OCR 数值，
    便于日志页展示"已由原始识别更正为 ..."。同时用 ACC 与谱面定数重算 OCR-RKS。
    """
    user = _require_user(request, db)
    contest = db.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="赛事不存在")
    if not _is_staff(contest, user):
        raise HTTPException(status_code=403, detail="需要管理员或赛事组织者权限")

    log = db.get(RecognitionLog, log_id)
    if log is None or log.contest_id != contest.id:
        raise HTTPException(status_code=404, detail="识别日志不存在")

    if song_name.strip():
        # 保留原始识别值，便于对比
        if not log.song_name_raw:
            log.song_name_raw = log.song_name
        log.song_name = song_name.strip()
    if difficulty.strip():
        log.difficulty = difficulty.strip().upper()
    if chart_level.strip():
        try:
            log.chart_level = float(chart_level.strip())
        except ValueError:
            raise HTTPException(status_code=400, detail="谱面定数格式不正确")
    if accuracy.strip():
        try:
            log.accuracy = float(accuracy.strip().rstrip("%"))
        except ValueError:
            raise HTTPException(status_code=400, detail="ACC 格式不正确")
    if score.strip():
        try:
            log.score = int(score.strip())
        except ValueError:
            raise HTTPException(status_code=400, detail="分数格式不正确")

    # 用更新后的 ACC 与谱面定数重算 OCR-RKS
    log.rks = _log_rks(log.accuracy, log.chart_level)
    db.commit()
    return RedirectResponse(url=f"/contests/{contest.id}/logs", status_code=303)


# ---------- 排行榜 ----------


@app.get("/contests/{contest_id}/leaderboard", response_class=HTMLResponse)
def leaderboard(contest_id: int, request: Request, db: Session = Depends(get_db)):
    contest = db.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="赛事不存在")
    _apply_auto_reveal(db, contest)
    user = current_user(request, db)
    data = build_leaderboard(db, contest)
    return _render(
        request,
        "leaderboard.html",
        _page_context(
            request,
            db,
            contest=contest,
            songs=data["songs"],
            rows=data["rows"],
            columns=data.get("columns", []),
            is_admin=bool(user and user.is_admin),
        ),
    )


# ---------- 排行榜导出（Markdown / HTML / PNG） ----------

from urllib.parse import quote

from fastapi.responses import Response


def _download_headers(fname: str) -> dict:
    """构造 Content-Disposition：filename 用 ASCII 兜底，filename* 用 RFC 5987 携带中文名。"""
    try:
        fname.encode("latin-1")
        ascii_fallback = fname
    except UnicodeEncodeError:
        ascii_fallback = "leaderboard." + fname.rsplit(".", 1)[-1]
    return {
        "Content-Disposition": f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{quote(fname)}'
    }


def _export_response(db: Session, contest: Contest, fmt: str, user: Optional[User]) -> Response:
    """生成对应格式的导出响应，尊重封榜（非管理员掩码）。"""
    from app.exporters import (
        export_filename,
        export_html,
        export_markdown,
        export_png,
    )

    mask = bool(not (user and user.is_admin))  # 封榜时仅管理员可见真实成绩
    fname = export_filename(contest, fmt)

    if fmt == "markdown":
        content = export_markdown(db, contest, mask=mask)
        return Response(
            content=content,
            media_type="text/markdown; charset=utf-8",
            headers=_download_headers(fname),
        )
    if fmt == "html":
        content = export_html(db, contest, mask=mask)
        return Response(
            content=content,
            media_type="text/html; charset=utf-8",
            headers=_download_headers(fname),
        )
    # image -> PNG，内存渲染后返回
    png = export_png(db, contest, mask=mask, out_path=None)
    return Response(
        content=png,
        media_type="image/png",
        headers=_download_headers(fname),
    )


@app.get("/contests/{contest_id}/leaderboard/export")
def leaderboard_export(
    contest_id: int,
    request: Request,
    format: str = "markdown",
    db: Session = Depends(get_db),
):
    """导出排行榜：format = markdown | html | image(-> PNG)。"""
    contest = db.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="赛事不存在")
    fmt = (format or "markdown").lower()
    if fmt in ("md",):
        fmt = "markdown"
    if fmt in ("img", "png"):
        fmt = "image"
    if fmt not in ("markdown", "html", "image"):
        raise HTTPException(status_code=400, detail="不支持的导出格式")
    user = current_user(request, db)
    return _export_response(db, contest, fmt, user)
