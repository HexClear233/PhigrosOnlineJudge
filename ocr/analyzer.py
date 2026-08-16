"""
成绩截图分析接口：输入 Phigros 结算截图，输出曲目信息、成绩与 RKS。

用法::

    from ocr.analyzer import analyze_settlement

    result = analyze_settlement("shot.png")          # 支持路径 / bytes / PIL.Image
    print(result.song_name, result.score, result.rks)
    print(result.to_dict())                          # 结构化字典，便于入库/JSON

命令行::

    python -m ocr.analyzer shot.png [shot2.png ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ocr.rks import calculate_rks
from ocr.settle_extract import extract_settlement


@dataclass
class SettlementResult:
    """一张结算截图的完整分析结果。"""

    # 曲目信息
    song_name: Optional[str] = None
    song_name_raw: Optional[str] = None
    difficulty: Optional[str] = None
    chart_level: Optional[float] = None
    # 成绩信息
    score: Optional[int] = None
    accuracy: Optional[float] = None
    max_combo: Optional[int] = None
    perfect: Optional[int] = None
    good: Optional[int] = None
    bad: Optional[int] = None
    miss: Optional[int] = None
    rank: Optional[str] = None
    # 计算结果
    rks: Optional[float] = None
    # 元信息
    resolution: Optional[Tuple[int, int]] = None
    bucket: Optional[str] = None
    confidences: Dict[str, float] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """转换为结构化字典，便于 JSON 序列化 / 入库。"""
        return {
            "song": {
                "name": self.song_name,
                "name_raw": self.song_name_raw,
                "difficulty": self.difficulty,
                "chart_level": self.chart_level,
            },
            "performance": {
                "score": self.score,
                "accuracy": self.accuracy,
                "max_combo": self.max_combo,
                "perfect": self.perfect,
                "good": self.good,
                "bad": self.bad,
                "miss": self.miss,
                "rank": self.rank,
            },
            "rks": round(self.rks, 6) if self.rks is not None else None,
            "meta": {
                "resolution": list(self.resolution) if self.resolution else None,
                "bucket": self.bucket,
                "confidences": self.confidences,
            },
            "warnings": self.warnings,
        }


def analyze_settlement(
    image: Any,
    *,
    song_database: Optional[Sequence[str]] = None,
    ocr_kwargs: Optional[Dict[str, Any]] = None,
) -> SettlementResult:
    """
    分析一张 Phigros 结算截图，返回曲目信息、成绩与计算出的 RKS。

    Args:
        image: 图片路径（str/Path）、原始字节（bytes）或 PIL.Image。
        song_database: 已知曲目名列表；缺省自动加载 Illu_Scrapy/songlist.json，
            用于把 OCR 曲名校对为规范名。
        ocr_kwargs: 透传给 PaddleOCR 的初始化参数。

    Returns:
        SettlementResult，含 to_dict() 便于序列化。
    """
    data = extract_settlement(image, song_database=song_database, ocr_kwargs=ocr_kwargs)

    warnings = list(data["warnings"])
    rks: Optional[float] = None
    if data["accuracy"] is None or data["chart_level"] is None:
        warnings.append("缺少 ACC 或谱面定数，无法计算 RKS")
    else:
        rks = round(calculate_rks(data["accuracy"], data["chart_level"]), 6)

    return SettlementResult(
        song_name=data["song_name"],
        song_name_raw=data["song_name_raw"],
        difficulty=data["difficulty"],
        chart_level=data["chart_level"],
        score=data["score"],
        accuracy=data["accuracy"],
        max_combo=data["max_combo"],
        perfect=data["perfect"],
        good=data["good"],
        bad=data["bad"],
        miss=data["miss"],
        rank=data["rank"],
        rks=rks,
        resolution=tuple(data["resolution"]),
        bucket=data["bucket"],
        confidences={
            "song_name": data["song_name_confidence"],
            "difficulty": data["difficulty_confidence"],
            "score": data["score_confidence"],
            "accuracy": data["accuracy_confidence"],
            "rank": data["rank_confidence"],
        },
        warnings=warnings,
    )


def main(argv: Optional[Sequence[str]] = None) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="分析 Phigros 结算截图，输出曲目/成绩/RKS")
    parser.add_argument("paths", nargs="+", help="结算截图路径")
    args = parser.parse_args(argv)

    for path in args.paths:
        result = analyze_settlement(path)
        print(json.dumps({"file": path, **result.to_dict()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
