"""
从裁剪后的 Phigros 截图中提取文字信息。

依赖（TTE 虚拟环境）:
    paddleocr (PaddleOCR 3.x), paddlepaddle, Pillow, numpy

用法示例:
    from ocr.ocr_data_parse import cut_pic, get_info, get_crop_info

    # 单区域识别
    texts = get_info(cut_pic("shot.png", position="ld"))

    # 左下 1/4 与右下 1/4 分别识别（一次推理完成两个区域）
    result = get_crop_info("shot.png")
    # -> {'ld': ['...'], 'rd': ['...']}
"""

import os
from pathlib import Path

import numpy as np
from PIL import Image

# 模型已缓存时跳过 PaddleOCR 的模型源联网检查，避免离线时每次启动都卡在检查上。
# 首次运行仍会自动联网下载模型；如需指定源可设置 PADDLE_PDX_MODEL_SOURCE。
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

# 全局复用的 OCR 引擎（模型加载较慢，只初始化一次）
_ocr_engine = None


def get_ocr(**kwargs):
    """惰性创建并复用全局 PaddleOCR 引擎。"""
    global _ocr_engine
    if _ocr_engine is None:
        from paddleocr import PaddleOCR

        params = {
            # 游戏截图方向固定且无畸变，关闭这三个附加模型可加快速度、减少模型体积
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            # 当前环境（Paddle 3.x + oneDNN）下启用 mkldnn 会触发
            # ConvertPirAttribute2RuntimeAttribute 报错，默认关闭以稳定运行
            "enable_mkldnn": False,
        }
        params.update(kwargs)
        _ocr_engine = PaddleOCR(**params)
    return _ocr_engine


def _to_image(pic):
    """把图片路径或 PIL.Image 统一成 PIL.Image。"""
    if isinstance(pic, Image.Image):
        return pic
    if isinstance(pic, (str, os.PathLike)):
        return Image.open(pic)
    raise TypeError("pic 应为图片路径或 PIL.Image 对象，实际为 %r" % type(pic))


def _sort_by_reading_order(items):
    """
    按阅读顺序排序识别结果：先按行（y 中心聚类），行内按 x。

    items: [(text, score, poly), ...]，poly 为 4x2 的四边形顶点。
    """
    if len(items) < 2:
        return items

    boxes = np.asarray([poly for _, _, poly in items], dtype=float)
    tops = boxes[:, :, 1].min(axis=1)
    xs = boxes[:, :, 0].min(axis=1)
    heights = boxes[:, :, 1].max(axis=1) - tops
    # 行容差取平均行高的 60%，同一行内略有错位的文本框不会被拆成多行
    row_tol = max(float(np.mean(heights)) * 0.6, 5.0)
    order = sorted(range(len(items)), key=lambda i: (round(tops[i] / row_tol), xs[i]))
    return [items[i] for i in order]


def _parse_result(res, with_score=False):
    """从单张图片的 PaddleOCR 结果中提取文本列表。"""
    texts = list(res.get("rec_texts") or [])
    scores = list(res.get("rec_scores") or [])
    polys = list(res.get("rec_polys") or [])

    items = [
        (text, float(score), poly)
        for text, score, poly in zip(texts, scores, polys)
        if text and text.strip()
    ]
    items = _sort_by_reading_order(items)

    if with_score:
        return [(text, score) for text, score, _ in items]
    return [text for text, _, _ in items]


def get_info(pic, with_score=False, **ocr_kwargs):
    """
    从图片中提取文字信息。

    Args:
        pic: 图片路径或 PIL.Image 对象。
        with_score: 为 True 时返回 [(text, score), ...]；否则返回 [text, ...]。
        **ocr_kwargs: 透传给 PaddleOCR 的初始化参数（仅首次创建引擎时生效）。

    Returns:
        按阅读顺序排列的文本列表；未识别到文字时返回空列表。
    """
    image = _to_image(pic)
    if image.mode != "RGB":
        image = image.convert("RGB")

    results = list(get_ocr(**ocr_kwargs).predict(np.asarray(image)))
    if not results:
        return []
    return _parse_result(results[0], with_score=with_score)


def cut_pic(pic_path, position="ld"):
    """
    裁剪图片，保留左下 1/4 或右下 1/4 区域。

    Args:
        pic_path: 图片路径或 PIL.Image 对象。
        position: 'ld' 表示左下 1/4，'rd' 表示右下 1/4。

    Returns:
        裁剪后的 PIL.Image。
    """
    pic = _to_image(pic_path)
    width, height = pic.size

    if position == "ld":
        box = (0, height // 2, width // 2, height)
    elif position == "rd":
        box = (width // 2, height // 2, width, height)
    else:
        raise ValueError(f"Invalid position: {position!r}，可选 'ld'（左下）或 'rd'（右下）")

    return pic.crop(box)


def get_crop_info(pic_path, positions=("ld", "rd"), with_score=False, **ocr_kwargs):
    """
    裁剪指定 1/4 区域并分别识别，返回 {position: 文本列表}。

    多个区域会合并成一次 PaddleOCR 推理，避免重复加载和多次检测的开销。
    """
    images = []
    for pos in positions:
        crop = cut_pic(pic_path, position=pos)
        if crop.mode != "RGB":
            crop = crop.convert("RGB")
        images.append(np.asarray(crop))

    results = list(get_ocr(**ocr_kwargs).predict(images))
    out = {}
    for pos, res in zip(positions, results):
        out[pos] = _parse_result(res, with_score=with_score)
    return out


def main():
    """用 Test_sample 目录下最新的截图演示左下/右下 1/4 的识别。"""
    samples = sorted(Path("../Test_sample").glob("*"), key=os.path.getmtime)
    if not samples:
        print("未找到测试图片，请把截图放到 Test_sample 目录。")
        return

    pic_path = str(samples[-1])
    print(f"处理图片: {pic_path}\n")

    result = get_crop_info(pic_path)
    for pos, texts in result.items():
        label = "左下 1/4" if pos == "ld" else "右下 1/4"
        print(f"[{label}] 识别结果:")
        for text in texts:
            print("  -", text)
        print()


if __name__ == "__main__":
    main()
