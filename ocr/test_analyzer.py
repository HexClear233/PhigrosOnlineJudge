"""
analyzer 接口与 RKS 计算测试。

运行::

    python -m ocr.test_analyzer
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ocr.analyzer import analyze_settlement
from ocr.rks import calculate_rks

SAMPLE_DIR = ROOT / "Test_sample"


def test_rks_formula() -> None:
    # 满分：RKS = 定数
    assert calculate_rks(100.0, 16.0) == 16.0
    # ACC < 70%：0
    assert calculate_rks(69.9, 16.0) == 0.0
    # ACC = 70%：16 * (1/3)^2 ≈ 1.78
    assert math.isclose(calculate_rks(70.0, 16.0), 16.0 / 9.0, rel_tol=1e-9)
    # 文档用例：ACC=99.0, 定数 15.8 ≈ 15.11
    assert math.isclose(calculate_rks(99.0, 15.8), 15.8 * ((99.0 - 55.0) / 45.0) ** 2, rel_tol=1e-9)
    # 非法输入
    for acc, level in ((None, 16.0), (80.0, None), (101.0, 16.0), (80.0, 0.0)):
        try:
            calculate_rks(acc, level)  # type: ignore[arg-type]
        except ValueError:
            pass
        else:
            raise AssertionError(f"calculate_rks({acc!r}, {level!r}) 应抛出 ValueError")


def test_analyzer_sample_rks() -> None:
    """普通样本：星拂云锦 feat. koi，HD Lv.9，ACC 80.54% -> RKS ≈ 2.899。"""
    result = analyze_settlement(SAMPLE_DIR / "MuMu-20260815-135623-524.png")
    assert result.song_name == "\u661f\u62c2\u4e91\u9526 feat. koi"
    assert result.difficulty == "HD"
    assert result.chart_level == 9.0
    assert result.score == 728875
    assert result.accuracy == 80.54
    assert result.max_combo == 24
    assert result.perfect == 467
    expected_rks = 9.0 * ((80.54 - 55.0) / 45.0) ** 2
    assert result.rks is not None and math.isclose(result.rks, expected_rks, rel_tol=1e-6)
    d = result.to_dict()
    assert d["song"]["name"] == result.song_name
    assert d["rks"] == round(result.rks, 6)


def test_analyzer_perfect_sample() -> None:
    """满分样本：000 -Ain Soph Aur-，IN Lv.14，ACC 100% -> RKS = 14.0。"""
    result = analyze_settlement(SAMPLE_DIR / "Screenshot_20260715_225056_com.PigeonGames.Phigr..jpg")
    assert result.score == 1000000
    assert result.accuracy == 100.0
    assert result.rks == 14.0
    assert result.song_name == "000 -Ain Soph Aur-"


def _run_all() -> int:
    import inspect

    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and inspect.isfunction(fn):
            try:
                fn()
                print(f"[OK ] {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"[FAIL] {name}: {exc!r}")
    print("失败数:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    sys.exit(_run_all())
