r"""Opt-in smoke tests for real cloud model calls.

Run explicitly with:
    $env:PDF_AI_READER_RUN_CLOUD_TESTS='1'
    C:/Users/WYK/.conda/envs/pdf_ai_reader_314/python.exe -m pytest tests/test_cloud_models.py -q
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.core.ai_engine import HybridModelRouter, LiteLLMClient, MockLLMClient, QAService
from src.core.model_providers import normalize_litellm_model
from src.core.models import AppConfig, BlockType, DocumentBlock
from src.data.config_manager import ConfigManager
from src.main import _is_configured_api_key


pytestmark = pytest.mark.skipif(
    os.environ.get("PDF_AI_READER_RUN_CLOUD_TESTS") != "1",
    reason="set PDF_AI_READER_RUN_CLOUD_TESTS=1 to call the configured cloud model",
)


def _configured_reasoning_client() -> LiteLLMClient:
    manager = ConfigManager(str(Path.cwd() / "config.yaml"))
    config = manager.get()
    model = normalize_litellm_model(config.model.cloud_reasoning or config.model.cloud)
    api_key = manager.get_api_key(model) or manager.get_api_key(config.model.cloud)
    if not _is_configured_api_key(api_key):
        pytest.fail("config.yaml does not contain a configured DeepSeek API key")
    return LiteLLMClient(model=model, api_key=api_key or "")


def test_configured_deepseek_reasoning_model_generates_text() -> None:
    client = _configured_reasoning_client()

    answer = client.generate(
        [
            {"role": "system", "content": "Return a short plain answer."},
            {"role": "user", "content": "Say OK in one word."},
        ],
        temperature=0,
        max_tokens=512,
        timeout=60,
    )

    assert answer.strip()


def test_qa_service_uses_configured_reasoning_model_for_real_answer() -> None:
    config = AppConfig()
    config.routing.qa = "cloud_only"
    reasoning = _configured_reasoning_client()
    router = HybridModelRouter(
        local_client=None,
        cloud_client=None,
        fallback_client=MockLLMClient(),
        config=config,
        reasoning_client=reasoning,
    )
    service = QAService(router)
    block = DocumentBlock(
        id="p0_b0",
        page_num=0,
        block_type=BlockType.PARAGRAPH,
        content="The Transformer uses attention to connect tokens across a sequence.",
        bbox=(0, 0, 100, 20),
    )

    answer = service.answer(
        "What mechanism connects tokens?",
        current_block=block,
        retrieved_blocks=[],
        stream=False,
    )

    assert isinstance(answer, str)
    assert answer.strip()
