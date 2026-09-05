"""LLM 客户端（OpenAI 兼容 chat completions）。

配置只从环境变量读（不落盘）：MERIDIAN_LLM_BASE_URL / MERIDIAN_LLM_API_KEY /
MERIDIAN_LLM_MODEL。未配置或调用失败时上层 SummaryAgent 降级为"无 AI 摘要"，
不影响规则报告本身（AI 负责理解，不负责评分——架构红线 1/2）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests

_DEFAULT_TIMEOUT = 60


@dataclass(frozen=True)
class LlmConfig:
    base_url: str
    api_key: str
    model: str
    timeout: int = _DEFAULT_TIMEOUT

    @classmethod
    def from_env(cls) -> "LlmConfig | None":
        """环境变量齐全返回配置，否则 None（未启用 AI 摘要）。"""
        base_url = os.environ.get("MERIDIAN_LLM_BASE_URL", "").strip()
        api_key = os.environ.get("MERIDIAN_LLM_API_KEY", "").strip()
        model = os.environ.get("MERIDIAN_LLM_MODEL", "").strip()
        if not (base_url and api_key and model):
            return None
        timeout = int(os.environ.get("MERIDIAN_LLM_TIMEOUT", str(_DEFAULT_TIMEOUT)))
        return cls(base_url.rstrip("/"), api_key, model, timeout)


def chat_completion(cfg: LlmConfig, system: str, user: str) -> str:
    """一次性 chat completion（无会话状态）。HTTP 失败/非 200 → DataError 语义的 RuntimeError。"""
    url = f"{cfg.base_url}/chat/completions"
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {cfg.api_key}"},
        json={
            "model": cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.3,
        },
        timeout=cfg.timeout,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"LLM 接口返回 {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    try:
        return str(data["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"LLM 报文结构异常: {exc}") from exc
