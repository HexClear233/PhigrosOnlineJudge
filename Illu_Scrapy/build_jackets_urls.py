# -*- coding: utf-8 -*-
"""
调查 Phigros Wiki 中每首歌曲的曲绘（含所有版本），汇总为 jackets_urls.json。

生成后由使用者直接编辑该 JSON：删除不要的条目、改 URL，或把 selected 置为
false；singleSong_web_scrapy.py 会按 JSON 里的内容下载。

再次运行本脚本会刷新调查结果，并保留上次 JSON 中已有的 selected 标记和
使用者新增的条目（按 URL 合并）。

用法：
    python build_jackets_urls.py
"""

import json
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
API_URL = "https://phigros.fandom.com/api.php"
SONGLIST_PATH = BASE_DIR / "songlist.json"
OUT_PATH = BASE_DIR / "jackets_urls.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

RETRIES = 3
REQUEST_DELAY = 0.3

EXCLUDE_KEYWORDS = (
    "result", "pattern", "grade", "cutscene", "bga", "screenshot", "gameplay",
    "chart", "menu", "icon", "logo", "preview", "thumb", "qzk", "frame",
    "banner", "spoiler",
)
JACKET_KEYWORDS = (
    "jacket", "afd", "bg", "cover", "artwork",
    "old", "new", "original", "locked", "legacy",
)

INFOBOX_RE = re.compile(r'<table class="wikitable centre-text".*?</table>', re.S)
INFOBOX_IMG_RE = re.compile(
    r'<a href="(https://static\.wikia\.nocookie\.net/[^"]+)" class="mw-file-description[^"]*"><img'
)
TRIGGER_IMG_RE = re.compile(
    r'<a href="(https://static\.wikia\.nocookie\.net/[^"]+)" class="mw-file-description[^"]*"><img[^>]*alt="([^"]*)"',
    re.S,
)


def load_songs():
    rows = json.loads(SONGLIST_PATH.read_text(encoding="utf-8"))
    unique = {}
    for i, row in enumerate(rows):
        url_path, display = (row.get("url", row.get("url_path", "")), row.get("songname", row.get("song_name", ""))) \
            if isinstance(row, dict) else (row[0], row[1])
        title = urllib.parse.unquote(url_path[len("/wiki/"):]).split("#", 1)[0]
        if title not in unique:
            unique[title] = {"index": i, "display": display}
    return [{"index": v["index"], "display": v["display"], "title": t} for t, v in unique.items()]


def _get_session():
    local = threading.local()
    session = getattr(local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)
        local.session = session
    return session


def _request_json(session, params):
    last_exc = None
    for attempt in range(RETRIES):
        try:
            resp = session.get(API_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise RuntimeError(data["error"].get("info", data["error"]))
            return data
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_exc = exc
            time.sleep(2.0 * (2 ** attempt))
    raise RuntimeError(f"API 请求失败: {last_exc}")


def fetch_images(session, title):
    """页面内全部图片文件：[{file_title, url, mime, width, height}]"""
    data = _request_json(session, {
        "action": "query", "titles": title, "generator": "images", "gimlimit": "100",
        "prop": "imageinfo", "iiprop": "url|mime|size",
        "format": "json", "formatversion": "2", "redirects": "1",
    })
    out = []
    for page in data.get("query", {}).get("pages", []):
        info = page.get("imageinfo")
        if info:
            out.append({
                "file_title": page["title"],
                "url": info[0].get("url", ""),
                "mime": info[0].get("mime", ""),
                "width": info[0].get("width"),
                "height": info[0].get("height"),
            })
    return out


def fetch_infobox(session, title):
    data = _request_json(session, {
        "action": "parse", "page": title, "prop": "text",
        "format": "json", "formatversion": "2", "redirects": "1",
    })
    html = data["parse"]["text"]
    table = INFOBOX_RE.search(html)
    if not table:
        return None
    match = INFOBOX_IMG_RE.search(table.group(0))
    return match.group(1) if match else None


def fetch_trigger_images(session, title):
    sub_title = title + "/Trigger content"
    try:
        data = _request_json(session, {
            "action": "parse", "page": sub_title, "prop": "text",
            "format": "json", "formatversion": "2", "redirects": "1",
        })
    except RuntimeError:
        return []
    html = data["parse"]["text"]
    return [(url, alt) for url, alt in TRIGGER_IMG_RE.findall(html)]


def _norm(s):
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


def _base(file_title):
    return file_title[len("File:"):] if file_title.startswith("File:") else file_title


def _usable(im):
    if im["mime"] and not im["mime"].startswith("image/"):
        return False
    w, h = im.get("width"), im.get("height")
    if w and h:
        if not (0.5 <= w / h <= 4.0):
            return False
        if min(w, h) < 200:
            return False
    return True


def select_candidates(images, title, infobox_url):
    t = _norm(title)
    candidates = []
    seen = set()
    for im in images:
        base = _base(im["file_title"]).lower()
        if not _usable(im):
            continue
        if base.endswith(".gif"):
            continue
        if any(k in base for k in EXCLUDE_KEYWORDS):
            continue
        keep = any(k in base for k in JACKET_KEYWORDS) or (t and t in _norm(base))
        if im["url"] and im["url"] not in seen:
            seen.add(im["url"])
            candidates.append(im)
    if infobox_url and infobox_url not in seen:
        candidates.insert(0, {
            "file_title": "", "url": infobox_url, "mime": "",
            "width": None, "height": None, "infobox": True,
        })
    return candidates


def classify_label(file_title):
    low = _base(file_title).lower()
    if "new" in low:
        return "new"
    if "old" in low:
        return "old"
    if "locked" in low:
        return "locked"
    if "afd" in low or " af " in low:
        return "afd"
    if "original" in low:
        return "original"
    if "jacket 3" in low:
        return "v3"
    return None


def find_trigger_titles(session, songs):
    """批量探测哪些歌曲存在 <歌名>/Trigger content 子页。"""
    titles = [s["title"] for s in songs]
    found = set()
    for i in range(0, len(titles), 50):
        chunk = titles[i:i + 50]
        data = _request_json(session, {
            "action": "query",
            "titles": "|".join(t + "/Trigger content" for t in chunk),
            "prop": "info",
            "format": "json",
            "formatversion": "2",
        })
        for page in data.get("query", {}).get("pages", []):
            if not page.get("missing"):
                found.add(page["title"].rsplit("/", 1)[0])
        time.sleep(REQUEST_DELAY)
    return found


def build_song(song, session, has_trigger):
    title = song["title"]
    images = fetch_images(session, title)
    infobox_url = fetch_infobox(session, title)
    candidates = select_candidates(images, title, infobox_url)
    if title in has_trigger:
        for url, alt in fetch_trigger_images(session, title):
            if not any(c["url"] == url for c in candidates):
                candidates.append({
                    "file_title": "", "url": url, "mime": "",
                    "width": None, "height": None, "alt": alt,
                })
    jackets = []
    seen_labels = {}
    for c in candidates:
        label = classify_label(c.get("file_title", ""))
        if c.get("infobox") or (infobox_url and c["url"] == infobox_url):
            label = "current"
        if not label and c.get("alt"):
            alt = c["alt"].lower()
            if "present" in alt or "new" in alt:
                label = "current"
            elif "old" in alt:
                label = "old"
        if not label:
            n = len([j for j in jackets if j["label"].startswith("v")]) + 1
            label = f"v{n}"
        source_file = c.get("file_title", "")
        if not source_file:
            # 从 URL 里取文件名（Trigger 子页等没有 file_title 的情况）
            from urllib.parse import unquote
            path = unquote(urllib.parse.urlparse(c["url"]).path)
            parts = [p for p in path.split("/") if p]
            if "revision" in parts:
                parts = parts[:parts.index("revision")]
            if parts:
                source_file = parts[-1]
        jackets.append({
            "source_file": source_file,
            "url": c["url"],
            "label": label,
            "width": c.get("width"),
            "height": c.get("height"),
            "selected": True,
            **({"alt": c["alt"]} if c.get("alt") else {}),
        })
    return {"index": song["index"], "display": song["display"], "title": title, "jackets": jackets}


def main():
    songs = load_songs()
    print(f"唯一歌曲：{len(songs)}")
    has_trigger = find_trigger_titles(_get_session(), songs)
    print(f"存在 Trigger content 子页的歌曲：{len(has_trigger)}")

    old_data = {}
    if OUT_PATH.exists():
        try:
            old_data = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old_data = {}
    old_flags = {}
    for song in old_data.get("songs", []):
        for j in song.get("jackets", []):
            if j.get("url"):
                old_flags[j["url"]] = j.get("selected", True)
    new_urls = set()

    results = []
    lock = threading.Lock()

    def work(song):
        try:
            r = build_song(song, _get_session(), has_trigger)
            with lock:
                results.append(r)
        except Exception as exc:
            with lock:
                results.append({**song, "jackets": [], "error": str(exc)})
        time.sleep(REQUEST_DELAY)

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(work, s) for s in songs]
        done = 0
        for future in concurrent.futures.as_completed(futures):
            future.result()
            done += 1
            if done % 25 == 0 or done == len(futures):
                print(f"进度：{done}/{len(futures)}", flush=True)

    results.sort(key=lambda r: r["index"])
    for song in results:
        for j in song["jackets"]:
            new_urls.add(j["url"])
            if j["url"] in old_flags:
                j["selected"] = old_flags[j["url"]]

    # 保留上次手动新增的条目：URL 不在本次调查结果里的原样保留
    preserved = []
    for song in old_data.get("songs", []):
        for j in song.get("jackets", []):
            if j.get("url") and j["url"] not in new_urls:
                preserved.append({"index": song.get("index"), "display": song.get("display"),
                                  "title": song.get("title"), "jackets": [dict(j)]})
    results.extend(preserved)

    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "https://phigros.fandom.com/wiki/Songs",
        "note": (
            "每首歌的 jackets 数组列出已调查到的曲绘版本（current=信息框当前曲绘，"
            "new/old/afd/locked/vN=其他版本）。请直接编辑本文件：删除不要的条目、"
            "修改 url，或将 selected 改为 false；singleSong_web_scrapy.py 按此文件下载。"
        ),
        "songs": results,
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(len(s["jackets"]) for s in results)
    print(f"已写入 {OUT_PATH.name}：{len(results)} 首，共 {total} 个曲绘条目")


if __name__ == "__main__":
    main()
