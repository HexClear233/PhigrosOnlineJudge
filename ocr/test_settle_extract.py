"""
settle_extract 端到端验证：对 ./Test_sample 全部截图执行提取，
核对已知成绩字段（分数/ACC/难度/定数/Max Combo），并统计字段覆盖率。

运行::

    python -m ocr.test_settle_extract
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ocr.settle_extract import extract_settlement

SAMPLE_DIR = ROOT / "Test_sample"

# 与全图 OCR 原始结果核对过的关键字段（文件相对名 -> 期望值）
KNOWN_FIELDS = {
    "MuMu-20260815-135623-524.png": {
        "score": 728875, "accuracy": 80.54, "max_combo": 24,
        "difficulty": "HD", "chart_level": 9.0,
        "song_name": "\u661f\u62c2\u4e91\u9526 feat. koi",
    },
    "MuMu-20260815-135934-813.png": {
        "score": 748337, "accuracy": 82.3, "max_combo": 35,
        "difficulty": "EZ", "chart_level": 7.0,
        "song_name": "Der Schneid",
    },
    "MuMu-20260815-140918-253.png": {
        "score": 850952, "accuracy": 91.59, "max_combo": 84,
        "difficulty": "HD", "chart_level": 7.0,
        "song_name": "\u5149",
    },
    "MuMu-20260815-141401-291.png": {
        "score": 850112, "accuracy": 91.91, "max_combo": 92,
        "difficulty": "EZ", "chart_level": 5.0,
    },
    "Screenshot_20260715_225056_com.PigeonGames.Phigr..jpg": {
        "score": 1000000, "accuracy": 100.0, "max_combo": 689,
        "difficulty": "IN", "chart_level": 14.0,
        "song_name": "000 -Ain Soph Aur-",
    },
    "Screenshot_20260815_131610_com.PigeonGames.Phigr..jpg": {
        "score": 932377, "accuracy": 97.43, "max_combo": 690,
        "difficulty": "IN", "chart_level": 16.0,
        "song_name": "\u5922\u306e\u964d\u308b\u65e5\u306b",
    },
    "Screenshot_20260815_131900_com.PigeonGames.Phigr..jpg": {
        "score": 956632, "accuracy": 98.7, "max_combo": 653,
        "difficulty": "IN", "chart_level": 15.0,
        "song_name": "\u767e\u9b3c\u058e\u591c\u884c",
    },
    "Screenshot_20260815_132343_com.PigeonGames.Phigr..jpg": {
        "score": 901834, "accuracy": 98.12, "max_combo": 222,
        "difficulty": "IN", "chart_level": 15.0,
        "song_name": "Re\uff1aEnd of a Dream",
    },
    "Screenshot_20260815_132854_com.PigeonGames.Phigr..jpg": {
        "score": 876384, "accuracy": 95.68, "max_combo": 187,
        "difficulty": "AT", "chart_level": 16.0,
        "song_name": "\u03a0\u03bf\u03c3\u03b5\u03b9\u03b4\u03ce\u03bd",
    },
    "Screenshot_20260815_133200_com.PigeonGames.Phigr..jpg": {
        "score": 964496, "accuracy": 98.04, "max_combo": 855,
        "difficulty": "IN", "chart_level": 14.0,
    },
    "Screenshot_20260815_133509_com.PigeonGames.Phigr..jpg": {
        "score": 952495, "accuracy": 98.47, "max_combo": 616,
        "difficulty": "IN", "chart_level": 15.0,
        "song_name": "energy trixxx",
    },
}


def run() -> int:
    samples = sorted(p for p in SAMPLE_DIR.glob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
    if not samples:
        print(f"未找到测试截图：{SAMPLE_DIR}")
        return 1

    failures = 0
    stats = {"score": 0, "accuracy": 0, "max_combo": 0, "difficulty": 0, "notes": 0}
    for path in samples:
        result = extract_settlement(path)
        name = path.name
        known = KNOWN_FIELDS.get(name, {})
        problems = []

        for key in ("score", "accuracy", "max_combo", "difficulty"):
            if result[key] is not None:
                stats[key] += 1
            if key in known and result[key] != known[key]:
                problems.append(f"{key}: got {result[key]!r}, expected {known[key]!r}")
        if known.get("chart_level") is not None and abs((result["chart_level"] or -1) - known["chart_level"]) > 1e-6:
            problems.append(f"chart_level: got {result['chart_level']!r}, expected {known['chart_level']!r}")
        if known.get("song_name") is not None and result["song_name"] != known["song_name"]:
            problems.append(f"song_name: got {result['song_name']!r}, expected {known['song_name']!r}")
        if all(result[k] is not None for k in ("perfect", "good", "bad", "miss")):
            stats["notes"] += 1

        status = "OK " if not problems else "FAIL"
        print(f"[{status}] {name}: song={result['song_name']!r} diff={result['difficulty']} "
              f"score={result['score']} acc={result['accuracy']} combo={result['max_combo']} "
              f"notes=({result['perfect']},{result['good']},{result['bad']},{result['miss']})")
        for p in problems:
            print(f"       {p}")
        failures += len(problems)

    total = len(samples)
    print(f"\n覆盖率: score {stats['score']}/{total}, accuracy {stats['accuracy']}/{total}, "
          f"max_combo {stats['max_combo']}/{total}, difficulty {stats['difficulty']}/{total}, "
          f"notes {stats['notes']}/{total}")
    print("已知字段核对失败数:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
