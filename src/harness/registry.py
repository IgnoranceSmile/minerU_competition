"""Pipeline 注册表。

- register：登记一个 Pipeline
- tools_schema：产出 OpenAI function-calling 格式的工具清单
- dispatch：按 LLM 选定的工具名 + 参数，调用对应 Pipeline

LLM 只能提供文字类参数；点选/框选（region）与目标图纸来自 UI 当前轮，
dispatch 时与 LLM 参数合并成完整 TaskRequest。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from contracts.interfaces import (AnswerType, TaskRequest,  # noqa: E402
                                  TaskResult)


class PipelineRegistry:
    def __init__(self) -> None:
        self._pipelines: dict = {}

    def register(self, pipeline) -> None:
        self._pipelines[pipeline.name] = pipeline

    def names(self) -> list[str]:
        return list(self._pipelines)

    def tools_schema(self) -> list[dict]:
        return [{
            "type": "function",
            "function": {
                "name": p.name,
                "description": p.description,
                "parameters": p.input_schema,
            },
        } for p in self._pipelines.values()]

    def dispatch(self, name: str, arguments: dict,
                 ctx, turn: TaskRequest) -> TaskResult:
        pipeline = self._pipelines.get(name)
        if pipeline is None:
            return TaskResult(AnswerType.TEXT, "", ok=False,
                              error=f"未注册的 Pipeline：{name}")
        # 合并：LLM 文字参数 + UI 当前轮的 region/target
        req = TaskRequest(
            prompt=arguments.get("prompt") or turn.prompt,
            target_drawing=arguments.get("target_drawing") or turn.target_drawing,
            region=turn.region,
            extra={k: v for k, v in arguments.items()
                   if k not in ("prompt", "target_drawing")})
        try:
            return pipeline.run(ctx, req)
        except Exception as e:
            return TaskResult(AnswerType.TEXT, "", ok=False,
                              error=f"{name} 执行异常：{e}")
