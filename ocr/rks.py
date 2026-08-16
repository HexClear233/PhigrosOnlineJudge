"""
Phigros RKS 计算引擎。

社区公认公式（指数为 2）::

    ACC < 70%            -> RKS = 0
    ACC >= 70%           -> RKS = chart_level * ((ACC - 55) / 45) ** 2
    ACC = 100%           -> RKS = chart_level

其中 ACC 为结算准确率（0.0 ~ 100.0），chart_level 为谱面定数。
"""

from __future__ import annotations

from typing import Optional


def calculate_rks(accuracy: Optional[float], chart_level: Optional[float]) -> float:
    """
    计算单谱面 RKS。

    Args:
        accuracy: 准确率（0.0 ~ 100.0，百分数）。
        chart_level: 谱面定数（如 15.8）。

    Returns:
        RKS 值。

    Raises:
        ValueError: accuracy / chart_level 为空或取值非法。
    """
    if accuracy is None or chart_level is None:
        raise ValueError("accuracy 与 chart_level 不能为空")
    if not (0.0 <= accuracy <= 100.0):
        raise ValueError(f"accuracy 应在 0.0 ~ 100.0 之间，实际为 {accuracy!r}")
    if chart_level <= 0.0:
        raise ValueError(f"chart_level 应为正数，实际为 {chart_level!r}")

    if accuracy < 70.0:
        return 0.0
    if accuracy >= 100.0:
        return chart_level
    return chart_level * ((accuracy - 55.0) / 45.0) ** 2
