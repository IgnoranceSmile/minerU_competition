"""pytest fixtures：构造 Workspace（本地图纸语料 + Mock LLM）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def workspace():
    """加载本地 data/origin 图纸与 data/mineru 解析结果，LLM 用 Mock。"""
    if not any((ROOT / "data" / "origin").glob("*/*.pdf")):
        pytest.skip("local drawing corpus is not available")
    # 强制走 Mock LLM（即使有 .env 中的 API key 也不调真实模型，保证 CI 可重放）
    os.environ.pop("LLM_API_KEY", None)
    os.environ["USE_MOCK_MINERU"] = "0"  # 用真实 MinerU 解析结果

    from src.harness.context import build_context
    return build_context(str(ROOT / "data" / "origin"))


@pytest.fixture(scope="session")
def registry():
    from src.harness.registry import PipelineRegistry
    from src.pipelines import all_pipelines
    reg = PipelineRegistry()
    for p in all_pipelines():
        reg.register(p)
    return reg


@pytest.fixture
def task_request_factory():
    """生成 TaskRequest 的工厂。"""
    from contracts.interfaces import TaskRequest

    def _make(prompt: str, target: str | None = None, **extra):
        return TaskRequest(prompt=prompt, target_drawing=target,
                           region=None, extra=extra)
    return _make
