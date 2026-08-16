# Illu_Scrapy 使用说明

目标：获取 Phigros Wiki 歌曲列表中每首歌的曲绘（jacket），包含换过的
旧版/新版、April Fools（afd）、locked 等全部版本，由使用者挑选。

## 为什么不能用 requests 直接抓页面

Fandom 全站由 Cloudflare 保护，直接 GET `https://phigros.fandom.com/wiki/<歌曲>`
会返回 **403 "Just a moment..."** 挑战页，页面里没有曲绘信息，解析就报 Error。

## 工作流（三步）

```powershell
cd Illu_Scrapy

# 1. 调查：从 Wiki 把所有歌曲的曲绘版本汇总成 jackets_urls.json
#    （再次运行会刷新，并保留你改过的 selected 标记和手动添加的 URL）
python build_jackets_urls.py

# 2. 挑选：直接编辑 jackets_urls.json
#    - 删除某个条目，或把 selected 改为 false -> 不下载该版本
#    - 修改 url -> 下载你给的地址

# 3. 下载：按 JSON 把曲绘文件下载到 jackets/
python singleSong_web_scrapy.py
```

## jackets_urls.json 结构

```json
{
  "songs": [
    {
      "index": 143,
      "display": "Doppelganger",
      "title": "Doppelganger",
      "jackets": [
        {"source_file": "DoppelgangerBG (New Version).png",
         "url": "https://static.wikia.nocookie.net/...",
         "label": "current", "selected": true},
        {"source_file": "DoppelgangerBG.png",
         "url": "https://static.wikia.nocookie.net/...",
         "label": "v1", "selected": true}
      ]
    }
  ]
}
```

- `label`：`current` = 歌曲页信息框当前展示的曲绘；`old`/`new`/`afd`/`locked` =
  文件名里的版本信息；`vN` = 其他版本。
- `selected: false` 的条目不会下载。

## 下载脚本参数

```powershell
python singleSong_web_scrapy.py --dry-run       # 只列出将下载的文件
python singleSong_web_scrapy.py --limit 20      # 只处理前 20 首
python singleSong_web_scrapy.py --force         # 覆盖已下载文件
python singleSong_web_scrapy.py --only-missing  # 只下载还没有文件的歌曲
python singleSong_web_scrapy.py --workers 2     # 降低并发
```

## 输出

- `jackets/`：`序号_歌名_jacket.webp`（仅 1 个版本）或
  `序号_歌名_标签.webp`（多个版本）；重复收录的歌曲共用同一组文件。
- `jackets_report.json`：songlist 每一行对应的下载结果（URL、来源文件名、标签）。

## 其他

- 下载脚本只读 `jackets_urls.json`，不访问 Wiki；下载走
  `static.wikia.nocookie.net`（CDN），目前会把原图以 WebP 格式返回，
  脚本按文件真实格式命名。
- 调查脚本（`build_jackets_urls.py`）需要访问 `phigros.fandom.com`；
  若连续运行触发限流，等几分钟再重跑。
