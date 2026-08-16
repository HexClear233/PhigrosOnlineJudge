"""
从 Phigros 结算截图提取曲目与成绩信息。

设计要点
--------
1. 所有区域坐标均为屏幕宽高百分比（见 ``../ocr_templates/regions_default.json``），
   运行时按实际图片尺寸换算像素，天然适配不同分辨率。
2. 按宽高比选择布局桶（16:9 / 16:10 / 手机 2.2:1），当前以 ./Test_sample 为校准基准。
3. 曲名与难度显示在曲绘底部的灰度层之上（白字），而曲绘内部可能含有美术文字；
   因此对曲名/难度区域先做"高亮度白字"二值化再送 OCR，以排除曲绘纹理的干扰。
4. 已知限制：PaddleOCR 通用识别模型对游戏斜体中文曲名存在稳定的形近字误读，
   无法用通用模型根除；输出同时保留原始识别文本与置信度，供词典校正/人工确认。

用法示例::

    from ocr.settle_extract import extract_settlement
    result = extract_settlement("shot.png")
    # -> {"song_name": ..., "difficulty": "IN", "chart_level": 16.0,
    #     "score": 728875, "accuracy": 80.54, "max_combo": 24, ...}
"""

from __future__ import annotations

import argparse
import difflib
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

from ocr.ocr_data_parse import get_ocr

REGIONS_PATH = Path(__file__).resolve().parent.parent / "ocr_templates" / "regions_default.json"

DIFFICULTY_RE = re.compile(r"(EZ|HD|IN|AT|SP)[^\d]{0,5}(\d+(?:\.\d+)?)", re.IGNORECASE)
LV_ONLY_RE = re.compile(r"Lv?\.?\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
NUMBER_RE = re.compile(r"^\d{1,7}$")
ACC_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")


def _load_regions() -> Dict[str, Any]:
    """加载百分比区域配置。"""
    with open(REGIONS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _select_bucket(width: int, height: int, buckets: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """按宽高比选择最近的布局桶。"""
    ratio = width / height
    best_name, best_bucket, best_delta = None, None, None
    for name, bucket in buckets.items():
        delta = abs(ratio - bucket["aspect"])
        if best_delta is None or delta < best_delta:
            best_name, best_bucket, best_delta = name, bucket, delta
    return best_name, best_bucket


def _to_rgb_image(image: Any) -> Image.Image:
    """把路径或 PIL.Image 统一成 RGB 图片。"""
    if isinstance(image, Image.Image):
        img = image
    elif isinstance(image, (str, Path)):
        img = Image.open(image)
    elif isinstance(image, (bytes, bytearray)):
        img = Image.open(io.BytesIO(bytes(image)))
    else:
        raise TypeError(f"image 应为路径、bytes 或 PIL.Image，实际为 {type(image)!r}")
    return img.convert("RGB") if img.mode != "RGB" else img


def _crop_box(img: Image.Image, region: Dict[str, List[float]]) -> Tuple[int, int, int, int]:
    """百分比区域 -> 像素裁剪框 (left, top, right, bottom)。"""
    w, h = img.size
    x0, x1 = region["x"]
    y0, y1 = region["y"]
    return (int(w * x0 / 100), int(h * y0 / 100), int(w * x1 / 100), int(h * y1 / 100))


def _binarize(gray: np.ndarray, threshold: int, scale: int = 2) -> np.ndarray:
    """高亮度白字二值化：保留灰度层上的白色文字，去除曲绘纹理。"""
    binary = np.where(gray > threshold, 255, 0).astype(np.uint8)
    if scale > 1:
        pil = Image.fromarray(binary)
        binary = np.asarray(pil.resize((binary.shape[1] * scale, binary.shape[0] * scale), Image.LANCZOS))
    return np.repeat(binary[:, :, None], 3, axis=2)


def _upscale_rgb(array: np.ndarray, scale: int = 2) -> np.ndarray:
    """放大彩色裁剪区域，提升小字号数字/字母的检测率。"""
    if scale <= 1:
        return array
    pil = Image.fromarray(array).resize((array.shape[1] * scale, array.shape[0] * scale), Image.LANCZOS)
    return np.asarray(pil)


def _ocr_batch(arrays: Sequence[np.ndarray], ocr_kwargs: Optional[Dict[str, Any]] = None) -> List[List[Dict[str, Any]]]:
    """批量 OCR，返回每张图的 [{text, conf, x_c, y_c, x0, y0, x1, y1}, ...]。"""
    kwargs = {"enable_mkldnn": False}
    if ocr_kwargs:
        kwargs.update(ocr_kwargs)
    results = list(get_ocr(**kwargs).predict(arrays))
    out: List[List[Dict[str, Any]]] = []
    for res in results:
        items = []
        for text, conf, poly in zip(
            res.get("rec_texts") or [],
            res.get("rec_scores") or [],
            res.get("rec_polys") or [],
        ):
            if not text or not text.strip():
                continue
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            items.append(
                {
                    "text": text,
                    "conf": float(conf),
                    "x_c": (min(xs) + max(xs)) / 2,
                    "y_c": (min(ys) + max(ys)) / 2,
                    "x0": min(xs),
                    "y0": min(ys),
                    "x1": max(xs),
                    "y1": max(ys),
                }
            )
        out.append(items)
    return out


def _pick_numeric(items: List[Dict[str, Any]], pattern: re.Pattern = NUMBER_RE) -> Optional[str]:
    """从 OCR 条目中取第一个纯数字文本。"""
    for it in items:
        if pattern.fullmatch(it["text"].strip()):
            return it["text"].strip()
    return None


def _parse_score(items: List[Dict[str, Any]]) -> Tuple[Optional[int], float]:
    """分数：6-7 位数字（容忍千分位逗号），取字符最长者。"""
    cands = []
    for it in items:
        cleaned = re.sub(r"[,\s]", "", it["text"].strip())
        if re.fullmatch(r"\d{5,7}", cleaned):
            cands.append((it, cleaned))
    if not cands:
        return None, 0.0
    it, cleaned = max(cands, key=lambda pair: (pair[0]["y1"] - pair[0]["y0"], len(pair[1])))
    return int(cleaned), it["conf"]


def _parse_accuracy(items: List[Dict[str, Any]]) -> Tuple[Optional[float], float]:
    """ACC：NN.NN%。"""
    for it in items:
        m = ACC_RE.search(it["text"])
        if m:
            return float(m.group(1)), it["conf"]
    return None, 0.0


def _parse_notes(items: List[Dict[str, Any]]) -> Tuple[Dict[str, Optional[int]], List[str]]:
    """Perfect/Good/Bad/Miss 数值行：按 x 坐标从左到右取最多 4 个数字。"""
    nums = [it for it in items if NUMBER_RE.fullmatch(it["text"].strip())]
    nums.sort(key=lambda i: i["x_c"])
    fields = ["perfect", "good", "bad", "miss"]
    notes: Dict[str, Optional[int]] = {f: None for f in fields}
    warnings: List[str] = []
    for field, it in zip(fields, nums):
        notes[field] = int(it["text"])
    if len(nums) < 4:
        warnings.append(f"音符数识别不全（{len(nums)}/4）：{ [i['text'] for i in nums] }")
    if len(nums) > 4:
        warnings.append("音符数识别多于 4 个，已按 x 取前 4 个")
    return notes, warnings


def _best_notes(groups: Sequence[List[Dict[str, Any]]]) -> Tuple[Dict[str, Optional[int]], List[str]]:
    """
    多倍数音符行识别结果合并：取识别出数字个数最多、总置信度最高的一组，
    按 x 坐标从左到右映射 Perfect/Good/Bad/Miss。
    """
    best_group = max(
        groups,
        key=lambda items: (
            sum(1 for it in items if NUMBER_RE.fullmatch(it["text"].strip())),
            sum(it["conf"] for it in items),
        ),
    )
    return _parse_notes(best_group)


def _parse_difficulty(items: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[float], float]:
    """难度：形如 'IN Lv.16' / 'ATLv.16' / 'INEv.15'。"""
    for it in items:
        m = DIFFICULTY_RE.search(it["text"])
        if m:
            return m.group(1).upper(), float(m.group(2)), it["conf"]
    for it in items:
        m = LV_ONLY_RE.search(it["text"])
        if m:
            return None, float(m.group(1)), it["conf"]
    return None, None, 0.0


def _parse_difficulty_multi(groups: Sequence[List[Dict[str, Any]]]) -> Tuple[Optional[str], Optional[float], float]:
    """多路（原始/多阈值二值化）难度识别结果合并，取置信度最高的有效匹配。"""
    best: Tuple[Optional[str], Optional[float], float] = (None, None, 0.0)
    for items in groups:
        diff, level, conf = _parse_difficulty(items)
        if diff is not None and conf > best[2]:
            best = (diff, level, conf)
    if best[0] is None:
        for items in groups:
            diff, level, conf = _parse_difficulty(items)
            if level is not None and conf > best[2]:
                best = (diff, level, conf)
    return best


def _parse_song_name(items: List[Dict[str, Any]], difficulty_text: Optional[str]) -> Tuple[Optional[str], float]:
    """曲名：灰度层白字区域中最长的非数字文本（剔除难度字样）。"""
    cands = []
    for it in items:
        text = it["text"].strip()
        if not text or NUMBER_RE.fullmatch(text):
            continue
        if difficulty_text and re.search(re.escape(difficulty_text), text, re.IGNORECASE):
            continue
        cands.append(it)
    if not cands:
        return None, 0.0
    it = max(cands, key=lambda i: (len(i["text"]), i["conf"]))
    return re.sub(r"\s+", " ", it["text"]).strip(), it["conf"]


def _pick_best_song(groups: Sequence[Tuple[Optional[str], float]]) -> Tuple[Optional[str], float]:
    """多路曲名结果择优：优先更完整（更长）的识别，其次置信度。"""
    best: Tuple[Optional[str], float] = (None, 0.0)
    for name, conf in groups:
        if not name:
            continue
        if best[0] is None or (len(name), conf) > (len(best[0]), best[1]):
            best = (name, conf)
    return best


def _load_song_database(path: Optional[Path] = None) -> List[str]:
    """
    加载已知曲目库（默认 Illu_Scrapy/songlist.json），用于 OCR 结果校对。
    文件不存在或格式不符时返回空列表（跳过校对）。
    """
    path = path or (Path(__file__).resolve().parent.parent / "Illu_Scrapy" / "songlist.json")
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return []
    names: List[str] = []
    for row in data:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            names.append(str(row[1]))
        elif isinstance(row, str):
            names.append(row)
    return names


def _normalize_song(text: str) -> str:
    """曲名规范化：去空白/标点/大小写，便于模糊匹配。"""
    return re.sub(r"[\W_]+", "", text).lower()


def _match_song(raw: Optional[str], database: Sequence[str]) -> Optional[str]:
    """
    将 OCR 曲名与已知曲目库做归一化模糊匹配：
    - 归一化后完全一致 -> 直接返回库中规范名；
    - 长度 >= 3 且相似度 >= 0.85 -> 返回最相似规范名；
    - 否则保留 OCR 原始结果。
    """
    if not raw or not database:
        return raw
    target = _normalize_song(raw)
    if not target:
        return raw
    best, best_ratio = raw, 0.0
    for name in database:
        cand = _normalize_song(name)
        if not cand:
            continue
        if cand == target:
            return name
        if len(target) >= 3:
            ratio = difflib.SequenceMatcher(None, target, cand).ratio()
            if ratio > best_ratio:
                best, best_ratio = name, ratio
    return best if best_ratio >= 0.85 else raw


def _parse_rank(items: List[Dict[str, Any]]) -> Tuple[Optional[str], float]:
    """结算评级：大号字母/符号（OCR 可能误读，保留原始文本）。"""
    for it in items:
        text = it["text"].strip()
        if text and 1 <= len(text) <= 3 and not re.fullmatch(r"\d{4,}", text):
            return text.upper(), it["conf"]
    return None, 0.0


def extract_settlement(
    image: Any,
    regions_config: Optional[Dict[str, Any]] = None,
    ocr_kwargs: Optional[Dict[str, Any]] = None,
    song_database: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """
    从一张 Phigros 结算截图提取曲目与成绩信息。

    Args:
        image: 图片路径或 PIL.Image。
        regions_config: 区域配置（默认读取 ocr_templates/regions_default.json）。
        ocr_kwargs: 透传给 PaddleOCR 的初始化参数。
        song_database: 已知曲目名列表；缺省自动加载 Illu_Scrapy/songlist.json，
            用于把 OCR 曲名校对为规范名。

    Returns:
        结构化结果字典，包含曲名、难度、分数、ACC、Max Combo、音符数、评级与告警。
    """
    img = _to_rgb_image(image)
    width, height = img.size
    cfg = regions_config or _load_regions()
    bucket_name, bucket = _select_bucket(width, height, cfg["buckets"])
    regions = bucket["regions"]

    # ---- 成绩面板：按区域裁剪后批量 OCR（一次推理） ----
    crop_keys = ["score", "rank", "max_combo", "accuracy", "notes"]
    crops = {key: np.asarray(img.crop(_crop_box(img, regions[key]))) for key in crop_keys}

    # ---- 曲名/难度区域：白字二值化（多阈值） + 原始图，多路识别取优 ----
    song_box = _crop_box(img, regions["song_name"])
    song_gray = np.asarray(img.convert("L").crop(song_box))
    song_variants = [
        _binarize(song_gray, 180),
        _binarize(song_gray, 210),
        np.asarray(img.crop(song_box)),
    ]
    diff_box = _crop_box(img, regions["difficulty"])
    diff_gray = np.asarray(img.convert("L").crop(diff_box))
    diff_variants = [
        _binarize(diff_gray, 180),
        _binarize(diff_gray, 210),
        np.asarray(img.crop(diff_box)),
    ]

    # 音符行小字号数字：原始/2x/3x 多倍数识别后择优合并
    batch = [crops[k] for k in crop_keys]
    batch[1] = _upscale_rgb(batch[1])  # rank 放大
    notes_variants = [
        crops["notes"],
        _upscale_rgb(crops["notes"], 2),
        _upscale_rgb(crops["notes"], 3),
    ]
    batch = batch + notes_variants + song_variants + diff_variants
    results = _ocr_batch(batch, ocr_kwargs)
    score_items, rank_items, combo_items, acc_items, _notes_base = results[:5]
    notes_v1, notes_v2, notes_v3 = results[5:8]
    song_bin180, song_bin210, song_raw = results[8:11]
    diff_bin180, diff_bin210, diff_raw = results[11:14]

    # ---- 解析 ----
    score, score_conf = _parse_score(score_items)
    accuracy, acc_conf = _parse_accuracy(acc_items)
    max_combo = _pick_numeric(combo_items)
    notes, notes_warnings = _best_notes([notes_v1, notes_v2, notes_v3])
    difficulty, chart_level, diff_conf = _parse_difficulty_multi([diff_bin180, diff_bin210, diff_raw])
    rank, rank_conf = _parse_rank(rank_items)

    # 曲名：优先二值化结果，其次原始图；剔除难度字样
    diff_label = f"{difficulty} Lv.{chart_level}" if difficulty else None
    song_name, song_conf = _pick_best_song(
        [
            _parse_song_name(song_bin180, diff_label),
            _parse_song_name(song_bin210, diff_label),
            _parse_song_name(song_raw, diff_label),
        ]
    )
    song_name_raw = song_name
    if song_database is None:
        song_database = _load_song_database()
    song_name = _match_song(song_name, song_database)

    warnings: List[str] = list(notes_warnings)
    if score is None:
        warnings.append("未识别到分数")
    if accuracy is None:
        warnings.append("未识别到 ACC")
    if max_combo is None:
        warnings.append("未识别到 Max Combo")
    if difficulty is None:
        warnings.append("未识别到难度")
    if song_name is None:
        warnings.append("未识别到曲名")

    return {
        "resolution": [width, height],
        "bucket": bucket_name,
        "song_name": song_name,
        "song_name_raw": song_name_raw,
        "song_name_confidence": round(song_conf, 3),
        "difficulty": difficulty,
        "chart_level": chart_level,
        "difficulty_confidence": round(diff_conf, 3),
        "score": score,
        "score_confidence": round(score_conf, 3),
        "accuracy": accuracy,
        "accuracy_confidence": round(acc_conf, 3),
        "max_combo": int(max_combo) if max_combo else None,
        "perfect": notes["perfect"],
        "good": notes["good"],
        "bad": notes["bad"],
        "miss": notes["miss"],
        "rank": rank,
        "rank_confidence": round(rank_conf, 3),
        "warnings": warnings,
    }


def _print_result(result: Dict[str, Any]) -> None:
    print(f"[{result['resolution'][0]}x{result['resolution'][1]} | {result['bucket']}]")
    print(f"  曲名     : {result['song_name']!r}  (conf={result['song_name_confidence']})")
    print(
        f"  难度     : {result['difficulty']} Lv.{result['chart_level']}  (conf={result['difficulty_confidence']})"
    )
    print(f"  分数     : {result['score']}  (conf={result['score_confidence']})")
    print(f"  ACC      : {result['accuracy']}%  (conf={result['accuracy_confidence']})")
    print(f"  Max Combo: {result['max_combo']}")
    print(
        "  音符     : P={perfect} G={good} B={bad} M={miss}".format(
            perfect=result["perfect"], good=result["good"], bad=result["bad"], miss=result["miss"]
        )
    )
    print(f"  评级     : {result['rank']}  (conf={result['rank_confidence']})")
    if result["warnings"]:
        print("  告警     : " + "；".join(result["warnings"]))


def main(argv: Optional[Sequence[str]] = None) -> None:
    try:
        import sys as _sys

        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="从 Phigros 结算截图提取曲目与成绩信息")
    parser.add_argument("paths", nargs="*", help="截图路径；缺省扫描 ../Test_sample")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    if args.paths:
        paths = [Path(p) for p in args.paths]
    else:
        sample_dir = Path(__file__).resolve().parent.parent / "Test_sample"
        paths = sorted(p for p in sample_dir.glob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
        if not paths:
            parser.error(f"未找到测试截图，请把截图放到 {sample_dir} 或显式传入路径")

    for path in paths:
        result = extract_settlement(path)
        if args.json:
            print(json.dumps({"file": str(path), **result}, ensure_ascii=False, indent=2))
        else:
            print(f"\n===== {path} =====")
            _print_result(result)


if __name__ == "__main__":
    main()
