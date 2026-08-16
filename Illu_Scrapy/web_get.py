# -*- coding: utf-8 -*-
"""
获取 Phigros Wiki 歌曲页（Songs）的 HTML 并保存，供 url_parse.py 解析。

注意：Fandom 由 Cloudflare 保护，直接 GET HTML 页面会返回
403 "Just a moment..." 挑战页。因此这里改用 MediaWiki API
（action=parse）获取页面正文 HTML，输出格式与之前一致。
"""

import urllib.parse
import time

import requests

API_URL = "https://phigros.fandom.com/api.php"
html_output_file = "songlist_output.html"
RETRIES = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
    )
}


def web_get(url):
    """从指定 URL 获取页面 HTML（经 API 解析）。"""
    # 形如 https://phigros.fandom.com/wiki/Songs#Single_Collection
    title = url.rsplit("/wiki/", 1)[-1].split("#", 1)[0]
    params = {
        "action": "parse",
        "page": urllib.parse.unquote(title),
        "prop": "text",
        "format": "json",
        "formatversion": "2",
    }
    last_exc = None
    for attempt in range(RETRIES):
        try:
            resp = requests.get(API_URL, params=params, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return data["parse"]["text"]
        except (requests.RequestException, ValueError, KeyError) as exc:
            last_exc = exc
            time.sleep(2.0 * (2 ** attempt))  # 2s / 4s / 8s 退避
    raise RuntimeError(f"获取页面失败（可能是 Fandom 限流，请稍后重试）：{last_exc}")


def write_file(html):
    """写入 HTML 内容到文件。"""
    with open(html_output_file, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML content saved to {html_output_file}")


def main():
    url = "https://phigros.fandom.com/wiki/Songs#Single_Collection"
    try:
        html = web_get(url)
        write_file(html)
        print(f"fetched {len(html)} chars")
    except RuntimeError as exc:
        print(f"Error: {exc}")


if __name__ == "__main__":
    main()
