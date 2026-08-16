# -*- coding: utf-8 -*-
"""
按 jackets_urls.json 下载歌曲曲绘文件到 jackets/。

jackets_urls.json 由 build_jackets_urls.py 调查生成，也可以直接手工编辑：
- 删除某个条目 -> 不下载该版本；
- 把 selected 改为 false -> 不下载该版本；
- 修改 url -> 下载你给的地址。
本脚本只读 JSON，不访问 Wiki。

用法：
    python singleSong_web_scrapy.py              # 按 JSON 下载全部
    python singleSong_web_scrapy.py --dry-run    # 只列出将下载的文件
    python singleSong_web_scrapy.py --limit 20   # 只处理前 20 首
    python singleSong_web_scrapy.py --force      # 覆盖已下载文件
    python singleSong_web_scrapy.py --only-missing  # 只下载还没有文件的歌曲

结果：
    jackets/            下载的曲绘文件
    jackets_report.json 每首歌的下载结果（含 URL 与来源文件名）
"""

import argparse
import concurrent.futures
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

BASE_DIR = Path(__file__).resolve().parent
URLS_PATH = BASE_DIR / "jackets_urls.json"
SONGLIST_PATH = BASE_DIR / "songlist.json"
JACKET_DIR = BASE_DIR / "jackets"
REPORT_PATH = BASE_DIR / "jackets_report.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
    ),
}

RETRIES = 3
REQUEST_DELAY = 0.1


def _get_session():
    local = threading.local()
    session = getattr(local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)
        local.session = session
    return session


def ext_for(url):
    """从静态 URL 推断扩展名（如 .../Der_Schneid_jacket.png/revision/latest）。"""
    path = urllib.parse.urlparse(url).path
    parts = [p for p in path.split("/") if p]
    if "revision" in parts:
        parts = parts[:parts.index("revision")]
    seg = parts[-1] if parts else ""
    return os.path.splitext(seg)[1].lower() or ".img"


def detect_image_ext(path):
    """按文件头判断真实图片格式，避免 CDN 把 PNG 转成 WebP 后扩展名不一致。"""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return None
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if head[8:12] == b"WEBP":
        return ".webp"
    if head[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    return None


def sanitize(name, max_len=120):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(name)).strip(" .")
    return name[:max_len] or "song"


def download_file(session, url, dest):
    last_exc = None
    for attempt in range(RETRIES):
        try:
            with session.get(url, stream=True, timeout=60) as resp:
                resp.raise_for_status()
                tmp = str(dest) + ".part"
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_content(65536):
                        f.write(chunk)
            os.replace(tmp, str(dest))
            return
        except (requests.RequestException, OSError) as exc:
            last_exc = exc
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"下载失败: {last_exc}")


def plan_filenames(index, display, jackets):
    """为每首歌的所有选中曲绘规划本地文件名（不含扩展名）。

    1 个版本：<序号>_<歌名>_jacket
    多个版本：<序号>_<歌名>_<标签>，标签重复时追加 _2、_3
    """
    display = sanitize(display)
    plans = []
    if len(jackets) == 1:
        j = jackets[0]
        return [{"url": j["url"], "stem": f"{index:03d}_{display}_jacket",
                 "label": j.get("label", ""), "source_file": j.get("source_file", "")}]
    used = {}
    for j in jackets:
        label = str(j.get("label") or "v1")
        used[label] = used.get(label, 0) + 1
        suffix = "" if used[label] == 1 else f"_{used[label]}"
        plans.append({"url": j["url"], "stem": f"{index:03d}_{display}_{label}{suffix}",
                      "label": label, "source_file": j.get("source_file", "")})
    return plans


def process_song(song, out_dir, force, dry_run):
    index = song["index"]
    display = song["display"]
    title = song["title"]
    result = {
        "index": index,
        "display": display,
        "title": title,
        "status": "ok",
        "files": [],
    }
    jackets = [j for j in song.get("jackets", []) if j.get("selected", True) and j.get("url")]
    if not jackets:
        result["status"] = "no_jacket"
        return result
    try:
        session = _get_session()
        plans = plan_filenames(index, display, jackets)
        for p in plans:
            url = p["url"]
            dest = out_dir / (p["stem"] + ext_for(url))
            entry = {
                "file": dest.name,
                "url": url,
                "source_file": p.get("source_file", ""),
                "label": p.get("label", ""),
            }
            if dry_run:
                entry["status"] = "ok"
                result["files"].append(entry)
                continue
            if dest.exists() and dest.stat().st_size > 0 and not force:
                entry["status"] = "skipped"
                result["files"].append(entry)
                continue
            download_file(session, url, dest)
            real_ext = detect_image_ext(dest)
            if real_ext and real_ext != dest.suffix:
                new_dest = dest.with_suffix(real_ext)
                os.replace(dest, new_dest)
                dest = new_dest
            entry["file"] = dest.name
            entry["status"] = "ok"
            result["files"].append(entry)
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
    finally:
        time.sleep(REQUEST_DELAY)
    return result


def main():
    parser = argparse.ArgumentParser(description="按 jackets_urls.json 下载曲绘文件")
    parser.add_argument("--dry-run", action="store_true", help="只列出将下载的文件")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 首（默认全部）")
    parser.add_argument("--force", action="store_true", help="覆盖已下载文件")
    parser.add_argument("--workers", type=int, default=3, help="并发线程数")
    parser.add_argument("--only-missing", action="store_true", help="只处理还没有曲绘文件的歌曲")
    args = parser.parse_args()

    data = json.loads(URLS_PATH.read_text(encoding="utf-8"))
    songs = data["songs"]
    if args.limit > 0:
        songs = songs[:args.limit]
    if args.only_missing:
        songs = [s for s in songs if not list(Path(JACKET_DIR).glob(f"{s['index']:03d}_*"))]

    out_dir = Path(JACKET_DIR)
    out_dir.mkdir(exist_ok=True)

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(process_song, s, out_dir, args.force, args.dry_run) for s in songs]
        done = 0
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
            done += 1
            if done % 25 == 0 or done == len(futures):
                print(f"进度：{done}/{len(futures)}", flush=True)

    results.sort(key=lambda r: r["index"])
    by_title = {r["title"]: r for r in results}

    # 展开回 songlist 的每一行（重复收录的歌曲共用同一组文件）
    items = []
    if SONGLIST_PATH.exists():
        rows = json.loads(SONGLIST_PATH.read_text(encoding="utf-8"))
        for i, row in enumerate(rows):
            url_path, display = (row.get("url", row.get("url_path", "")), row.get("songname", row.get("song_name", ""))) \
                if isinstance(row, dict) else (row[0], row[1])
            title = urllib.parse.unquote(url_path[len("/wiki/"):]).split("#", 1)[0]
            r = by_title.get(title)
            items.append({
                "index": i,
                "display": display,
                "title": title,
                "status": r["status"] if r else "missing",
                "files": r["files"] if r else [],
                **({"error": r["error"]} if r and r["status"] == "failed" else {}),
            })
    else:
        items = results

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump({"source": str(URLS_PATH), "items": items}, f, ensure_ascii=False, indent=2)

    ok = sum(1 for r in results if r["status"] == "ok")
    failed = [r for r in results if r["status"] == "failed"]
    file_count = sum(len(r["files"]) for r in results)
    print(f"共 {len(results)} 首唯一歌曲：成功 {ok}，失败 {len(failed)}，文件 {file_count} 个")
    if failed:
        print("失败明细（也见 jackets_report.json）：")
        for r in failed:
            print(f"  [{r['index']:03d}] {r['display']} -> {r.get('error', '')}")


if __name__ == "__main__":
    main()
