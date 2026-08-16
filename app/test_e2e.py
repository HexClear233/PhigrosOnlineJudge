"""
端到端测试：自动启动 uvicorn，跑通 注册→创建赛事→手动/OCR 提交→审核→排行榜 全流程。

运行::

    python -m app.test_e2e
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PORT = 8011
BASE = f"http://127.0.0.1:{PORT}"
# 样本 1：星拂云锦 feat. koi，HD Lv.9，ACC 80.54（课题曲赛事大概率不匹配 -> 走审核）
SAMPLE = ROOT / "Test_sample" / "MuMu-20260815-135623-524.png"
# 样本 2：Der Schneid，EZ Lv.7，ACC 82.3（自选曲赛事可自动匹配 -> 直接入榜）
SAMPLE_MATCH = ROOT / "Test_sample" / "MuMu-20260815-135934-813.png"


def _wait_ready(client: httpx.Client, tries: int = 60) -> None:
    for _ in range(tries):
        try:
            r = client.get("/", timeout=2)
            if r.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError("服务器启动超时")


def _main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    e2e_db = ROOT / "data" / "e2e_test.db"
    e2e_db.unlink(missing_ok=True)
    os.environ["PHIGROS_OJ_DB"] = str(e2e_db)

    log_path = ROOT / "data" / "e2e_server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    srvlog = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(ROOT),
        env={**os.environ},
        stdout=srvlog,
        stderr=subprocess.STDOUT,
    )
    failures: list[str] = []

    def check(cond: bool, name: str) -> None:
        print(f"[{'OK ' if cond else 'FAIL'}] {name}")
        if not cond:
            failures.append(name)

    try:
        with httpx.Client(base_url=BASE, follow_redirects=True, timeout=60) as admin:
            _wait_ready(admin)
            check(admin.get("/").status_code == 200, "首页可访问")

            # 注册（首个用户自动成为管理员）
            r = admin.post(
                "/register",
                data={"username": "admin", "display_name": "管理员", "password": "admin1234"},
            )
            check(r.status_code == 200 and "赛事列表" in r.text, "注册并登录（管理员）")

            # 注册普通选手
            with httpx.Client(base_url=BASE, follow_redirects=True, timeout=60) as player:
                r = player.post(
                    "/register",
                    data={"username": "player1", "display_name": "玩家甲", "password": "player123"},
                )
                check(r.status_code == 200 and "赛事列表" in r.text, "注册选手账号")

                # 读取歌曲库，挑选有 HD 与 IN 定数的歌曲
                from app.db import SessionLocal
                from app.models import Song

                db = SessionLocal()
                song_hd = db.query(Song).filter(Song.hd_level.isnot(None)).first()
                song_in = db.query(Song).filter(Song.in_level.isnot(None)).first()
                db.close()
                check(song_hd is not None and song_in is not None, "歌曲库已导入")
                if song_hd is None or song_in is None:
                    return 1

                # 管理员创建课题曲赛事
                r = admin.post(
                    "/contests/new",
                    data={
                        "name": "测试课题曲赛",
                        "description": "端到端测试",
                        "contest_type": "topic",
                        "start_time": "2026-08-01T00:00",
                        "end_time": "2026-12-31T23:59",
                        "songs": [f"{song_hd.id}:HD", f"{song_in.id}:IN"],
                    },
                )
                check("测试课题曲赛" in r.text, "创建课题曲赛事")
                contest_id = int(str(r.url).rstrip("/").rsplit("/", 1)[1])

                from app.models import ContestSong, Submission

                db = SessionLocal()
                cses = db.query(ContestSong).filter(ContestSong.contest_id == contest_id).all()
                db.close()
                cs_hd, cs_in = cses[0], cses[1]

                # 管理员手动录入（立即生效）
                r = admin.post(
                    f"/contests/{contest_id}/submit/manual",
                    data={
                        "song_ref": str(cs_hd.id),
                        "score": "850000",
                        "accuracy": "95.0",
                        "perfect": "900",
                        "good": "10",
                        "bad": "0",
                        "miss": "0",
                        "max_combo": "900",
                        "rank": "S",
                    },
                )
                check("提交成功" in r.text, "管理员手动录入")

                # 选手尝试手动录入 -> 被拒绝
                r = player.post(
                    f"/contests/{contest_id}/submit/manual",
                    data={"song_ref": str(cs_hd.id), "score": "999999", "accuracy": "99.9"},
                )
                check("仅管理员或赛事组织人员可手动录入成绩" in r.text, "选手手动录入被拒绝")

                # 选手 OCR 上传 -> 只读确认页：展示曲名/难度/定数/ACC/计算 RKS，不展示分数细节
                raw = SAMPLE.read_bytes()
                image_hash = hashlib.sha256(raw).hexdigest()
                r = player.post(
                    f"/contests/{contest_id}/submit/ocr",
                    files={"file": ("shot.png", raw, "image/png")},
                )
                check("核对识别结果" in r.text or "确认成绩" in r.text, "选手 OCR 识别确认页")
                check("80.54" in r.text and "RKS" in r.text, "确认页展示 ACC 与 RKS")
                check("Max Combo" not in r.text and "评级" not in r.text, "选手确认页不展示分数细节（只读）")

                # 课题曲赛事中识别到不属于课题曲的曲目 -> 弹出警告 + 提供“申诉”
                check("未自动匹配" in r.text or "不属于本场课题曲" in r.text, "未匹配时提示走审核")
                check("不属于本场课题曲" in r.text, "识别到非课题曲时弹出警告")
                check("申诉：提交到审核队列" in r.text, "提供申诉入口进入审核队列")

                r = player.post(
                    f"/contests/{contest_id}/submit/ocr/confirm",
                    data={"image_hash": image_hash, "action": "review"},
                )
                check("进入审核队列" in r.text, "识别有误 -> 提交到审核队列")

                # 待审核成绩不计入榜单（该 submit 为人工预填，这里直接确认无有效成绩）
                db = SessionLocal()
                pending_subs = db.query(Submission).filter(
                    Submission.contest_id == contest_id,
                    Submission.status == "pending",
                ).count()
                db.close()
                check(pending_subs >= 1, "待审核提交已入库")

                # 管理员审核：为该 pending 提交人工指定谱面 + 修正 ACC 后通过
                db = SessionLocal()
                sub = db.query(Submission).filter(
                    Submission.contest_id == contest_id,
                    Submission.status == "pending",
                ).first()
                db.close()
                check(sub is not None, "审核队列有待审核提交")
                r = admin.get(f"/review/{sub.id}")
                check("审核提交" in r.text and "结算截图" in r.text, "审核详情页展示截图")
                r = admin.post(
                    f"/review/{sub.id}",
                    data={
                        "song_ref": str(cs_in.id),
                        "accuracy": "81.0",
                        "score": "728875",
                        "perfect": "467",
                        "good": "25",
                        "bad": "0",
                        "miss": "108",
                        "max_combo": "24",
                        "rank": "C",
                        "action": "approved",
                        "note": "OCR 识别有误，人工修正",
                    },
                )
                expected_approved = round(cs_in.chart_level * ((81.0 - 55.0) / 45.0) ** 2, 4)
                r = admin.get(f"/contests/{contest_id}/leaderboard")
                check(f"{expected_approved:.4f}" in r.text, "审核通过后计入榜单（修正后 RKS）")

                # 创建自选曲赛事并提交
                r = admin.post(
                    "/contests/new",
                    data={
                        "name": "测试自选曲赛",
                        "description": "Top 3",
                        "contest_type": "free_choice",
                        "start_time": "2026-08-01T00:00",
                        "end_time": "2026-12-31T23:59",
                        "top_n": "3",
                    },
                )
                check("测试自选曲赛" in r.text, "创建自选曲赛事")
                free_id = int(str(r.url).rstrip("/").rsplit("/", 1)[1])
                r = admin.post(
                    f"/contests/{free_id}/submit/manual",
                    data={
                        "song_ref": f"{song_in.id}|IN",
                        "score": "980000",
                        "accuracy": "98.5",
                        "perfect": "1000",
                        "good": "5",
                        "bad": "0",
                        "miss": "1",
                        "max_combo": "999",
                        "rank": "S",
                    },
                )
                check("提交成功" in r.text, "自选曲手动提交")
                r = admin.get(f"/contests/{free_id}/leaderboard")
                check(song_in.name in r.text and "管理员" in r.text, "自选曲排行榜正确")

                # 选手在自选曲赛事 OCR 上传（样本自动匹配库内歌曲）-> 直接登入榜单
                raw2 = SAMPLE_MATCH.read_bytes()
                hash2 = hashlib.sha256(raw2).hexdigest()
                r = player.post(
                    f"/contests/{free_id}/submit/ocr",
                    files={"file": ("shot.png", raw2, "image/png")},
                )
                check("82.3" in r.text, "确认页展示样本 ACC（Der Schneid EZ）")
                r = player.post(
                    f"/contests/{free_id}/submit/ocr/confirm",
                    data={"image_hash": hash2, "action": "confirm"},
                )
                check("提交成功" in r.text and "RKS" in r.text, "选手确认无误 -> 直接登入榜单")
                # Der Schneid EZ 定数 7.0, ACC 82.3 -> RKS = 7*((82.3-55)/45)^2 ≈ 2.5772
                expected_direct = round(7.0 * ((82.3 - 55.0) / 45.0) ** 2, 4)
                r = player.get(f"/contests/{free_id}/leaderboard")
                check(f"{expected_direct:.4f}" in r.text, "直接入榜成绩无需审核即计入榜单")

                # 选手尝试伪造确认（传入合法 but 不存在的 image_hash）-> 被拒绝
                r = player.post(
                    f"/contests/{free_id}/submit/ocr/confirm",
                    data={"image_hash": "deadbeef" * 8, "action": "confirm"},
                )
                check("请重新上传" in r.text, "伪造 image_hash 无法通过确认")

                # --- 识别日志：管理员可查阅，可撤回异常上榜成绩 ---
                from app.models import RecognitionLog

                db = SessionLocal()
                log = db.query(RecognitionLog).filter(
                    RecognitionLog.contest_id == free_id,
                    RecognitionLog.action == "confirm",
                ).first()
                fc_sub_id = log.submission_id if log else None
                db.close()
                check(log is not None, "确认后生成识别日志")

                r = admin.get(f"/contests/{free_id}/logs")
                check("识别日志" in r.text and "直接入榜" in r.text, "管理员可查阅识别日志")

                # 撤回该已上榜成绩 -> 从榜单消失
                r = admin.post(
                    f"/contests/{free_id}/logs/{log.id}/withdraw",
                    data={"reason": "异常成绩撤回测试"},
                )
                check("识别日志" in r.text, "执行撤回")
                r = admin.get(f"/contests/{free_id}/leaderboard")
                check(f"{expected_direct:.4f}" not in r.text, "撤回后成绩从榜单移除")

                # 恢复 -> 重新入榜
                r = admin.post(f"/contests/{free_id}/logs/{log.id}/restore")
                check("识别日志" in r.text, "恢复撤回成绩")
                r = admin.get(f"/contests/{free_id}/leaderboard")
                check(f"{expected_direct:.4f}" in r.text, "恢复后成绩重新计入榜单")

                # 普通选手无法访问识别日志（无权限）
                r = player.get(f"/contests/{free_id}/logs")
                check("需要管理员或赛事组织者权限" in r.text or r.status_code == 403, "普通选手无权查看识别日志")

                # 封榜后普通视角隐藏成绩
                r = admin.post(f"/contests/{contest_id}/seal")
                check("已封榜" in r.text, "封榜")
                with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as anon:
                    r = anon.get(f"/contests/{contest_id}/leaderboard")
                check("?" in r.text, "封榜后成绩隐藏")
                r = admin.post(f"/contests/{contest_id}/reveal")
                check("已封榜" not in r.text, "揭榜")

                # --- 封榜智能揭榜时间：到点自动揭榜 ---
                def _seal_anon_peek(reveal_time: str) -> str:
                    """封榜并让非管理员查看榜单，返回榜单 HTML 文本与封榜状态。"""
                    admin.post(
                        f"/contests/{contest_id}/seal",
                        data={"reveal_time": reveal_time},
                        follow_redirects=False,
                    )
                    with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as anon:
                        return anon.get(f"/contests/{contest_id}/leaderboard").text

                from app.db import SessionLocal as _SL
                from app.models import Contest as _Contest

                # 揭榜时间已过去 -> 访问即自动揭榜，非管理员能看到真实成绩（无 ? 掩码）
                past = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M")
                html = _seal_anon_peek(past)
                db1 = _SL()
                auto_revealed = not db1.get(_Contest, contest_id).is_sealed
                db1.close()
                check(auto_revealed, "揭榜时间已到自动解除封榜")
                # 封榜提示消失，且榜单出现具体 RKS 数值（形如 15.3929）
                check(
                    "榜单已封" not in html
                    and re.search(r"\d\.\d{4}", html) is not None,
                    "自动揭榜后成绩可见",
                )

                # 揭榜时间在未来 -> 保持封榜，非管理员看到 ?，页面提示自动揭榜时间
                future = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
                html_f = _seal_anon_peek(future)
                db2 = _SL()
                still_sealed = db2.get(_Contest, contest_id).is_sealed
                db2.close()
                check(still_sealed, "未来揭榜时间仍保持封榜")
                check("?" in html_f and "自动揭榜" in html_f, "封榜页面提示自动揭榜时间")

                # --- 排行榜导出（Markdown / HTML / PNG） ---
                r = admin.get(f"/contests/{contest_id}/leaderboard/export?format=markdown")
                check(r.status_code == 200 and "text/markdown" in r.headers.get("content-type", ""), "导出 Markdown")
                check("排行榜" in r.text, "Markdown 含标题")
                r = admin.get(f"/contests/{contest_id}/leaderboard/export?format=html")
                check(r.status_code == 200 and "text/html" in r.headers.get("content-type", ""), "导出 HTML")
                check("<table>" in r.text, "HTML 含表格")
                # 课题曲 HTML 表头体现 曲名+难度+定数
                check("· Lv." in r.text and "jacket-head" in r.text, "HTML 课题曲体现曲名/难度/定数")
                r = admin.get(f"/contests/{contest_id}/leaderboard/export?format=image")
                check(r.status_code == 200 and r.headers.get("content-type") == "image/png", "导出 PNG")
                check(r.content[:4] == b"\x89PNG", "PNG 为有效图片")

                # 自选曲导出：单元格体现 曲名 / 难度 / RKS
                r = admin.get(f"/contests/{free_id}/leaderboard/export?format=html")
                check(r.status_code == 200, "自选曲导出 HTML")
                check("jacket-cell" in r.text and "RKS" in r.text, "自选曲 HTML 单元格含曲名/难度/RKS")
                r = admin.get(f"/contests/{free_id}/leaderboard/export?format=image")
                check(r.status_code == 200 and r.content[:4] == b"\x89PNG", "自选曲导出 PNG")

                # 非法格式应 400
                r = admin.get(f"/contests/{contest_id}/leaderboard/export?format=pdf")
                check(r.status_code == 400, "非法导出格式返回 400")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        srvlog.close()

    try:
        from app.db import engine

        engine.dispose()
    except Exception:  # noqa: BLE001
        pass
    try:
        e2e_db.unlink(missing_ok=True)
    except OSError:
        pass
    print("失败数:", len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_main())
