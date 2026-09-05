"""AI 预测模型（Phase 3 脚手架）：Python 模型经桥接注册进 Opportunity/Risk 通道。

架构约束（红线 1）：模型只产 score/direction/confidence 参与通道合成，
action 建议仍由 action_rules 规则匹配生成。
本文件提供两个参考实现：
- MomentumForecastModel：近 N 日对数收益 OLS 斜率外推（numpy 多项式拟合）——
  真正"从数据学"的最小可用预测模型，替换它即接入更复杂的 ML。
- FlatModel：恒中性哑模型（自检/占位）。
模型注册来自 config/models.yaml（channel/category 可配，红线 4）。
"""

from __future__ import annotations

import math


class MomentumForecastModel:
    """OLS 动量外推：对近 lookback 日收盘做 log 线性拟合，斜率年化外推 20 日预期收益。

    score = 50 + 250 × 预期20日收益（clamp 0-100，即 ±20% 预期映射满量程）；
    窗口不足 / 数据退化 → 中性 50。模型没有状态，纯函数式。
    """

    name = "momentum_forecast_v1"

    def __init__(self, lookback: int = 60, horizon: int = 20):
        self.lookback = lookback
        self.horizon = horizon

    def analyze(self, payload: dict) -> dict:
        closes = payload.get("closes") or []
        window = closes[-self.lookback:]
        if len(window) < 20:
            return {"score": 50.0, "direction": "neutral", "confidence": 0.0}

        try:
            import numpy as np

            logs = [math.log(float(c)) for c in window if float(c) > 0]
            if len(logs) < 20:
                return {"score": 50.0, "direction": "neutral", "confidence": 0.0}
            x = np.arange(len(logs), dtype=float)
            slope = float(np.polyfit(x, np.array(logs), 1)[0])  # 日 log 斜率
        except Exception:  # noqa: BLE001 —— 数值退化回中性
            return {"score": 50.0, "direction": "neutral", "confidence": 0.0}

        expected = slope * self.horizon  # 20 日预期 log 收益
        score = 50.0 + 250.0 * expected
        score = max(0.0, min(100.0, score))
        direction = "up" if expected > 0.005 else ("down" if expected < -0.005 else "neutral")
        # 置信度随 |斜率| 的 t 值近似（残差可忽略时封顶 0.8）
        confidence = min(0.8, abs(expected) * 5.0)
        return {"score": score, "direction": direction, "confidence": confidence}


class FlatModel:
    """恒中性哑模型（自检/占位用）。"""

    name = "flat_neutral"

    def analyze(self, payload: dict) -> dict:
        return {"score": 50.0, "direction": "neutral", "confidence": 0.0}
