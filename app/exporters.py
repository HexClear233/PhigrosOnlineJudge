"""排行榜导出：Markdown / HTML / PNG 三种格式。

与 /app/leaderboard.py 的 build_leaderboard() 输出配合，将榜单数据结构渲染为
可分享的产物（群内粘贴、独立网页、图片分享）。

HTML 与 PNG 尽量复刻站点直接展示的榜单样式（见 app/templates/leaderboard.html
与 app/static/style.css）：
- 课题曲赛事：每首课题曲一列，表头带曲绘背景 + 歌名 + 难度 + 定数；单元格显示 RKS。
- 自选曲赛事：A/B/C... 固定列，每个单元格清楚显示「曲名 / 难度 / RKS」（附曲绘背景）。

导出时尊重封榜：当 contest.is_sealed 且非管理员时，得分单元格用 ? 占位。
"""

from __future__ import annotations

import base64
import html
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from sqlalchemy.orm import Session

from app.jackets import JACKET_DIR, jacket_filename
from app.leaderboard import build_leaderboard
from app.models import Contest

# 中文字体候选（Windows 常见字体，按可用情况取第一个）
FONT_CANDIDATES = [
    ("C:/Windows/Fonts/msyh.ttc", "微软雅黑"),
    ("C:/Windows/Fonts/msyhbd.ttc", "微软雅黑 Bold"),
    ("C:/Windows/Fonts/simhei.ttf", "黑体"),
    ("C:/Windows/Fonts/NotoSansCJKsc-Regular.otf", "Noto Sans CJK SC"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "Noto Sans CJK"),
    ("/System/Library/Fonts/PingFang.ttc", "苹方"),
]


def _masked(contest: Contest, mask: bool) -> bool:
    """是否需要对得分进行掩码处理（封榜且非管理员）。"""
    return mask and contest.is_sealed


def _jacket_binary(song_name: str) -> Optional[bytes]:
    """读取歌名对应曲绘文件字节；无匹配返回 None。"""
    fname = jacket_filename(song_name or "")
    if not fname:
        return None
    p = JACKET_DIR / fname
    if not p.is_file():
        return None
    try:
        return p.read_bytes()
    except OSError:
        return None


def _jacket_data_uri(song_name: str) -> str:
    """把曲绘编码为可直接用于 HTML 背景的内联 data URI（让导出文件自包含可分享）。"""
    raw = _jacket_binary(song_name)
    if not raw:
        return ""
    mime = "image/webp" if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP" else "image/png"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _cell_text(cell: Optional[Dict[str, Any]], mask: bool) -> str:
    """把单元格转成显示文本：掩码时一律 ?。"""
    if mask:
        return "?"
    if cell is None:
        return "--"
    rks = cell.get("rks")
    if rks is None:
        return "--"
    return f"{rks:.4f}"


def _total_text(total: Optional[float], mask: bool) -> str:
    if mask:
        return "?"
    if total is None:
        return "--"
    return f"{total:.4f}"


def _cell_class(song_name: str | None, rks: Optional[float], chart_level: Optional[float],
                acc: Optional[float], mask: bool, has_jacket: bool) -> str:
    """按设计书 §4.5.2 返回单元格配色类：gray / normal / good / perfect。"""
    if mask or rks is None:
        return "cell gray"
    if acc is not None and acc >= 100.0:
        return "cell perfect"
    if chart_level and rks >= chart_level * 0.9:
        return "cell good"
    return "cell"


# ---------- Markdown ----------


def export_markdown(db: Session, contest: Contest, *, mask: bool = True) -> str:
    """导出 ACM 风格矩阵榜单为 Markdown 表格。

    课题曲：每曲一列（表头为「曲名（难度）」，定数写进副标题）。
    自选曲：A/B/C... 列，每个单元格为「歌曲 · 难度 : RKS」，清楚体现曲目与成绩。
    """
    mask = _masked(contest, mask)
    data = build_leaderboard(db, contest)
    lines: List[str] = []
    lines.append(f"# 排行榜：{contest.name}\n")
    type_label = "自选曲赛事" if contest.contest_type == "free_choice" else "课题曲赛事"
    lines.append(
        f"> {type_label}"
        + (f" · v{contest.version}" if contest.version else "")
        + (f" · Top {contest.top_n}" if contest.contest_type == "free_choice" and contest.top_n else "")
    )
    lines.append(f"> 导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    if not data["rows"]:
        lines.append("暂无成绩。")
        return "\n".join(lines) + "\n"

    def md_cell(cell: Optional[Dict[str, Any]]) -> str:
        if mask:
            return "?"
        if cell is None or cell.get("rks") is None:
            return "--"
        if contest.contest_type == "free_choice":
            song = cell.get("song_name") or "?"
            diff = cell.get("difficulty") or "?"
            return f"{song} · {diff} : {cell['rks']:.4f}"
        return f"{cell['rks']:.4f}"

    if contest.contest_type == "topic":
        header = ["排名", "选手"] + [f"{s['name']} ({s['difficulty']})" for s in data["songs"]] + ["总分"]
        levels = " | ".join([str(s["chart_level"]) if s["weight"] == 1 else f"{s['chart_level']}×{s['weight']}"
                             for s in data["songs"]])
        lines.append(f"> 定数：|  | | {levels} |  |")
        lines.append("> (每列难度标记于表头)")
        rows = [
            [str(r["rank"]), r["user"].display_name]
            + [md_cell(c) for c in r["cells"]]
            + [_total_text(r.get("total"), mask)]
            for r in data["rows"]
        ]
    else:
        header = ["排名", "选手"] + list(data.get("columns", [])) + ["总分"]
        rows = [
            [str(r["rank"]), r["user"].display_name]
            + [md_cell(c) for c in r["cells"]]
            + [_total_text(r.get("total"), mask)]
            for r in data["rows"]
        ]
        lines.append("> 每列单元格格式：歌曲名 · 难度 : RKS")

    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))
    for row in rows:
        lines.append("| " + " | ".join(str(v).replace("|", "\\|") for v in row) + " |")
    return "\n".join(lines) + "\n"


# ---------- HTML ----------


def _html_meta(contest: Contest, mask: bool) -> str:
    type_label = "自选曲赛事" if contest.contest_type == "free_choice" else "课题曲赛事"
    meta = (
        type_label
        + (f" · v{contest.version}" if contest.version else "")
        + (f" · Top {contest.top_n}" if contest.contest_type == "free_choice" and contest.top_n else "")
    )
    if contest.is_sealed:
        view = "非管理员视角" if mask else "管理员视角"
        meta += f" · <span class='sealed'>榜单已封（{view}）</span>"
    return meta


def export_html(db: Session, contest: Contest, *, mask: bool = True) -> str:
    """导出为独立可分享的 HTML 页面，样式与站点榜单一致（含曲绘背景）。"""
    mask = _masked(contest, mask)
    data = build_leaderboard(db, contest)
    # 为课题曲表头补充自包含的曲绘 data URI，便于导出文件离线分享
    for s in data.get("songs", []):
        s["jacket_uri"] = _jacket_data_uri(s["name"]) if s.get("jacket") else ""
    type_label = "自选曲赛事" if contest.contest_type == "free_choice" else "课题曲赛事"
    title = f"排行榜：{contest.name} - Phigros OJ"

    _CSS = R"""
    body { font-family: -apple-system, "Microsoft YaHei", "PingFang SC", sans-serif;
           margin: 20px; color: #222; background: #fff; }
    h1   { font-size: 20px; margin-bottom: 4px; }
    .meta { color: #666; font-size: 13px; margin-bottom: 14px; }
    .sealed { color: #b45309; font-weight: 600; }
    .table-wrap { overflow-x: auto; border: 1px solid #d8deea; border-radius: 8px; }
    table { border-collapse: collapse; font-size: 13.5px; width: 100%; background:#fff; }
    th, td { border-bottom: 1px solid #e3e8f1; padding: 9px 12px; white-space: nowrap; }
    thead th { background: #f0f3f9; font-weight: 600; color: #465069; }
    th.jacket-head { min-width: 116px; text-align:center; vertical-align:middle;
                     background-size: cover; background-position: center 25%; }
    th.jacket-head.with-jacket { color: #fff; }
    th.jacket-head > span { text-shadow: 0 1px 2px rgba(0,0,0,.8); }
    th.jacket-head small, td small { color: inherit; opacity: .82; font-size: 11px; }
    td.cell { text-align:center; }
    td.cell.gray  { color: #aaa; }
    td.cell.good  { background: #e6f3ff; }
    td.cell.perfect { background: #fff3cd; font-weight:600; color:#8a6d00; }
    td.jacket-cell { position: relative; min-width: 128px; height: 72px; text-align:center;
                     vertical-align:middle; color:#fff; background-color:#111;
                     background-size: contain; background-repeat:no-repeat; background-position:center; }
    td.jacket-cell .tint { position:absolute; inset:0; background: rgba(0,0,0,.5); }
    td.jacket-cell .inner { position:relative; z-index:1; text-shadow:0 1px 2px rgba(0,0,0,.85); }
    td.jacket-cell .inner small { color:#d5e3ff; font-size: 11px; }
    td.total { font-weight:700; color:#0e7c86; }
    .muted { color:#999; }
    """

    if not data["rows"]:
        body = '<p class="muted">暂无成绩。</p>'
    elif contest.contest_type == "topic":
        def _topic_th(s: Dict[str, Any]) -> str:
            bg = ""
            if s.get("jacket_uri"):
                bg = ' style="background-image:url(&quot;%s&quot;)"' % s["jacket_uri"]
            cls = "jacket-head" + (" with-jacket" if s.get("jacket_uri") else "")
            w = (" ×%s" % s["weight"]) if s.get("weight") != 1 else ""
            title = "定数 %s%s" % (s["chart_level"], w)
            inner = "<span>%s<br><small>%s · Lv.%s</small></span>" % (
                html.escape(s["name"]), s["difficulty"], s["chart_level"])
            return (
                '<th class="%s"%s title="%s">%s</th>' % (cls, bg, title, inner)
            )

        thead = (
            "<tr><th>排名</th><th>选手</th>"
            + "".join(_topic_th(s) for s in data["songs"])
            + "<th>总分</th></tr>"
        )
        trows = []
        for r in data["rows"]:
            cells = ""
            for s, c in zip(data["songs"], r["cells"]):
                cls = _cell_class(None, c.get("rks") if c else None, s["chart_level"], None, mask, False)
                cells += f"<td class='{cls}'>{_cell_text(c, mask)}</td>"
            trows.append(
                f"<tr><td>{r['rank']}</td><td>{html.escape(r['user'].display_name)}</td>"
                f"{cells}<td class='total'>{_total_text(r.get('total'), mask)}</td></tr>"
            )
        body = f"<div class='table-wrap'><table><thead>{thead}</thead><tbody>{''.join(trows)}</tbody></table></div>"
    else:
        # 自选曲：每列显示 曲名/难度 + RKS（附曲绘背景），复刻站点 jacktell-cell 样式
        thead = (
            "<tr><th>排名</th><th>选手</th>"
            + "".join(f"<th class='jacket-head-title'>{c}</th>" for c in data.get("columns", []))
            + "<th>总分</th></tr>"
        )
        trows = []
        for r in data["rows"]:
            cells = ""
            for c in r["cells"]:
                uri = _jacket_data_uri(c["song_name"]) if (c and c.get("song_name")) else ""
                inner = "?"
                if not (mask or c is None or c.get("rks") is None):
                    inner = (
                        "<div class='inner'>%s<br><small>难度 %s · RKS %.4f</small></div>" % (
                            html.escape(c["song_name"] or "?"),
                            html.escape(c["difficulty"] or "?"),
                            c["rks"],
                        )
                    )
                elif mask and c is not None:
                    inner = "<div class='inner'>?</div>"
                bg = ""
                if uri:
                    bg = ' style="background-image:url(&quot;%s&quot;)"' % uri
                cls = "jacket-cell" + (" with-jacket" if uri else "")
                cell_html = '<td class="%s"%s>%s</td>' % (cls, bg, inner)
                cells += cell_html
            trows.append(
                f"<tr><td>{r['rank']}</td><td>{html.escape(r['user'].display_name)}</td>"
                f"{cells}<td class='total'>{_total_text(r.get('total'), mask)}</td></tr>"
            )
        body = (
            f"<div class='table-wrap'><table><thead>{thead}</thead><tbody>{''.join(trows)}</tbody></table></div>"
            "<p class='muted' style='margin-top:8px;'>总分 = 左侧 A~"
            f"{data.get('columns', [])[-1] if data.get('columns') else ''}"
            " 各曲 RKS 之和；每个选手按成绩从高到低填左空右，单元格显示「曲名 / 难度 / RKS」。</p>"
        )

    meta = _html_meta(contest, mask)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<h1>排行榜：{html.escape(contest.name)}</h1>
<div class="meta">{meta}</div>
{body}
</body>
</html>
"""


# ---------- PNG ----------


def _load_font(size: int):
    """加载可用的中文字体；找不到时退化为 PIL 默认字体。"""
    from PIL import ImageFont

    for path, _name in FONT_CANDIDATES:
        try:
            f = ImageFont.truetype(path, size)
            _load_font._used = (_name, path)  # type: ignore[attr-defined]
            return f
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


_load_font._used = ("默认字体", None)  # type: ignore[attr-defined]


def _text_size(font, s: str) -> int:
    bb = font.getbbox(s)
    return bb[2] - bb[0]


def _wrap_text(font, s: str, max_w: int) -> List[str]:
    """按像素宽度近似换行（中英文混合粗略处理，1 中文字符≈2 半角宽）。"""
    lines: List[str] = []
    cur = ""
    cur_w = 0.0
    for ch in s:
        w = 2.0 if ord(ch) > 0x2E80 else 1.0
        if cur_w + w > max_w:
            lines.append(cur)
            cur, cur_w = ch, w
        else:
            cur += ch
            cur_w += w
    if cur:
        lines.append(cur)
    return lines


def export_png(db: Session, contest: Contest, *, mask: bool = True, out_path: Optional[str] = None) -> Union[bytes, None]:
    """用 Pillow 渲染榜单为 PNG 图片，样式与站点榜单一致。

    课题曲：表头为曲名+难度（带曲绘缩略背景），单元格显示 RKS 与总分。
    自选曲：A/B/C... 列，每个单元格显示「曲名 / 难度 / RKS」，并绘制曲绘缩略图。
    无法加载中文字体时，PIL 默认字体无法渲染中文，会以替代字显示。
    """
    from PIL import Image, ImageDraw

    mask = _masked(contest, mask)
    data = build_leaderboard(db, contest)
    type_label = "自选曲赛事" if contest.contest_type == "free_choice" else "课题曲赛事"

    font_h = 15
    head_font = _load_font(font_h + 2)
    cell_font = _load_font(font_h)
    small_font = _load_font(11)
    pad = 10
    is_free = contest.contest_type == "free_choice"
    row_h = 56 if is_free else 38
    head_h = 66 if not is_free else 40

    # 列宽方案
    def text_w(font, s): return _text_size(font, s)

    if not is_free:
        # 表头含换行歌名、难度和定数 + 曲绘缩略
        headers = ["排名", "选手"] + [s["name"] for s in data["songs"]] + ["总分"]
        col_w = [52, 110] + [max(140, min(_text_size(head_font, s["name"]) + 30, 240)) for s in data["songs"]] + [78]
    else:
        headers = ["排名", "选手"] + list(data.get("columns", [])) + ["总分"]
        col_w = [52, 110] + [168] * len(data.get("columns", [])) + [78]
    n_col = len(headers)

    # 选手名 & 总分宽度自适应
    if data["rows"]:
        name_w = max(text_w(cell_font, r["user"].display_name) for r in data["rows"]) + 2 * pad
        col_w[1] = max(col_w[1], name_w)

    img_w = sum(col_w) + pad * (n_col + 1)
    title_h = font_h + pad + 8
    img_h = title_h + head_h + row_h * len(data["rows"]) + pad * 2

    img = Image.new("RGB", (img_w, img_h), "white")
    draw = ImageDraw.Draw(img)

    yy = pad
    draw.text((pad, yy), f"排行榜：{contest.name}", fill="black", font=head_font)
    meta = (
        f"{type_label}"
        + (f" · v{contest.version}" if contest.version else "")
        + (f" · Top {contest.top_n}" if contest.contest_type == "free_choice" and contest.top_n else "")
        + (" · 榜单已封" if contest.is_sealed else "")
        + f" · 导出 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    draw.text((pad, yy + 22), meta, fill="#666", font=small_font)

    y = yy + title_h

    # ---------- 表头 ----------
    x = pad
    for c, head in enumerate(headers):
        rect = [x, y, x + col_w[c], y + head_h]
        draw.rectangle(rect, fill="#f0f3f9", outline="#d8deea")
        if not is_free and 2 <= c <= 1 + len(data["songs"]):
            song = data["songs"][c - 2]
            # 绘制曲绘缩略背景（右侧）
            jraw = _jacket_binary(song["name"])
            if jraw:
                try:
                    from PIL import Image as PILImage
                    import io as _io
                    jm = PILImage.open(_io.BytesIO(jraw)).convert("RGB")
                    jm.thumbnail((56, 56))
                    ix = x + col_w[c] - 54
                    iy = y + (head_h - jm.size[1]) // 2
                    img.paste(jm, (ix, iy))
                    draw.rectangle([ix, iy, ix + jm.size[0], iy + jm.size[1]], outline="#bbb")
                except Exception:  # noqa: BLE001
                    pass
            tx = x + pad
            head_lines = _wrap_text(head_font, head, col_w[c] - 68)
            ty = y + 6
            for ln in head_lines:
                draw.text((tx, ty), ln, fill="#465069", font=head_font)
                ty += font_h + 3
            draw.text((tx, ty), f"{song['difficulty']} · Lv.{song['chart_level']}", fill="#465069", font=small_font)
        else:
            draw.text((x + pad, y + (head_h - font_h) // 2), head, fill="#465069", font=head_font)
        x += col_w[c]
    y += head_h

    # ---------- 数据行 ----------
    for r in data["rows"]:
        x = pad
        total = _total_text(r.get("total"), mask)
        # 排名
        draw.text((x + pad, y + (row_h - font_h) // 2), str(r["rank"]), fill="black", font=cell_font)
        x += col_w[0]
        # 选手
        draw.text((x + pad, y + (row_h - font_h) // 2), r["user"].display_name, fill="black", font=cell_font)
        x += col_w[1]

        for ci, cell in enumerate(r["cells"]):
            cw = col_w[ci + 2]
            rect = [x, y, x + cw, y + row_h]
            if cell is None or cell.get("rks") is None:
                draw.rectangle(rect, outline="#d8deea", fill="#fafbfd")
                if not mask:
                    draw.text((x + cw // 2 - 9, y + (row_h - font_h) // 2), "--", fill="#aaa", font=cell_font)
                else:
                    draw.text((x + cw // 2 - 5, y + (row_h - font_h) // 2), "?", fill="#aaa", font=cell_font)
                x += cw
                continue

            jraw = _jacket_binary(cell.get("song_name") or cell.get("name") or "")
            if contest.contest_type == "free_choice" and jraw:
                fill = "#111"
            else:
                fill = "#ffffff"
            draw.rectangle(rect, outline="#d8deea", fill=fill)

            if mask:
                draw.text((x + cw // 2 - 5, y + (row_h - font_h) // 2), "?", fill="black", font=cell_font)
            elif contest.contest_type == "free_choice":
                # 左侧曲绘缩略 + 右侧「曲名 / 难度 · RKS」
                if jraw:
                    try:
                        from PIL import Image as PILImage
                        import io as _io
                        jm = PILImage.open(_io.BytesIO(jraw)).convert("RGB")
                        jm.thumbnail((46, 46))
                        img.paste(jm, (x + 4, y + (row_h - jm.size[1]) // 2))
                    except Exception:  # noqa: BLE001
                        pass
                tx = x + pad + 4
                song_lines = _wrap_text(cell_font, cell.get("song_name") or "?", cw - 56)
                ty = y + 5
                draw.text((tx, ty), song_lines[0], fill="white", font=cell_font)
                draw.text((tx, y + row_h - 18),
                          f"Lv.{cell.get('difficulty','?')} · RKS {cell['rks']:.4f}",
                          fill="#d5e3ff", font=small_font)
            else:
                # topic：单元格 RKS，优秀成绩浅蓝背景
                lv = data["songs"][ci]["chart_level"] if ci < len(data["songs"]) else None
                if lv and cell["rks"] >= lv * 0.9:
                    draw.rectangle(rect, outline="#d8deea", fill="#e6f3ff")
                draw.text((x + cw // 2 - 12, y + (row_h - font_h) // 2),
                          _cell_text(cell, mask), fill="black", font=cell_font)
            x += cw

        draw.text((x + pad, y + (row_h - font_h) // 2), total, fill="#0e7c86", font=head_font)
        y += row_h

    img = img.convert("RGB")
    if out_path:
        img.save(out_path, format="PNG")
        return None
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _export_ext(fmt: str) -> str:
    return {  # type: ignore[return-value]
        "markdown": ".md",
        "html": ".html",
        "image": ".png",
    }[fmt]


def export_filename(contest: Contest, fmt: str) -> str:
    ext = _export_ext(fmt)
    safe = "".join(c for c in contest.name if c.isalnum() or c in " _-().") or "leaderboard"
    return f"{safe}{ext}"
