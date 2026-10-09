"""LLM 客户端的唯一决策点。

整个后端只有这里知道「现在用的是真实模型还是 Mock」。
其余代码通过 get_llm_client() 拿客户端，拿到什么就用什么。
"""

from __future__ import annotations

import logging
from functools import lru_cache

from app.ai.protocol import LLMClient
from app.ai.providers.mock_provider import MockProvider
from app.core.config import settings
from app.core.database import SessionLocal

logger = logging.getLogger(__name__)

_probe_result: tuple[bool, str] | None = None


def _build() -> LLMClient:
    global _probe_result

    if not settings.llm_should_use_openai:
        logger.info("LLM 模式：Mock（未配置 OPENAI_API_KEY）")
        return MockProvider(session_factory=SessionLocal)

    from app.ai.providers.openai_provider import OpenAIProvider, probe

    api_key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
    ok, message = probe(api_key, settings.openai_model)
    _probe_result = (ok, message)

    if ok:
        logger.info("LLM 模式：OpenAI（%s）", settings.openai_model)
        return OpenAIProvider(api_key=api_key, model=settings.openai_model)

    # ★ 探活失败自动降级，而不是让演示在第一位客户那里挂掉。
    #   但必须大声记日志 —— 静默降级会让人以为在用真模型。
    logger.error(
        "OpenAI 探活失败，已自动降级为 Mock。原因：%s\n"
        "如果这是意料之外的，检查 OPENAI_API_KEY、网络和账户额度。",
        message,
    )
    return MockProvider(session_factory=SessionLocal)


@lru_cache
def get_llm_client() -> LLMClient:
    return _build()


def reset_llm_client() -> None:
    """配置变更后重建。测试和 /api/settings/llm 会用到。"""
    get_llm_client.cache_clear()


def current_mode() -> dict:
    client = get_llm_client()
    return {
        "mode": client.mode,
        "model": client.name,
        "configured_mode": settings.llm_mode,
        "has_api_key": bool(
            settings.openai_api_key and settings.openai_api_key.get_secret_value().strip()
        ),
        "probe": {"ok": _probe_result[0], "message": _probe_result[1]} if _probe_result else None,
    }
