"""推理模型网关（OpenAI 兼容协议，厂商无关）。

默认走 DeepSeek（deepseek-chat），换厂商只改 .env 的 LLM_* 三项即可。
chat() 是生成器，逐事件 yield：reasoning / content / done。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL  # noqa: E402


class OpenAICompatLLM:
    """满足 contracts.LLMGateway 接口。"""

    def __init__(self) -> None:
        import httpx
        from openai import OpenAI
        # 国内 API：trust_env=False 绕开环境里的 SOCKS 代理
        self.client = OpenAI(
            api_key=LLM_API_KEY, base_url=LLM_BASE_URL,
            http_client=httpx.Client(trust_env=False, timeout=180))
        self.model = LLM_MODEL

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             stream: bool = True):
        kwargs: dict = {"model": self.model, "messages": messages, "stream": True}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        resp = self.client.chat.completions.create(**kwargs)

        content, reasoning = "", ""
        tool_acc: dict[int, dict] = {}
        for chunk in resp:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            # 思考过程：DeepSeek 为 reasoning_content，部分厂商为 reasoning
            rc = (getattr(delta, "reasoning_content", None)
                  or getattr(delta, "reasoning", None))
            if rc is None:
                extra = getattr(delta, "model_extra", None) or {}
                rc = extra.get("reasoning_content") or extra.get("reasoning")
            if rc:
                reasoning += rc
                yield {"type": "reasoning", "delta": rc}

            if delta.content:
                content += delta.content
                yield {"type": "content", "delta": delta.content}

            for tc in (delta.tool_calls or []):
                slot = tool_acc.setdefault(
                    tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    slot["arguments"] += tc.function.arguments

        tool_calls = []
        for slot in tool_acc.values():
            try:
                args = json.loads(slot["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append({"id": slot["id"], "name": slot["name"],
                               "arguments": args})

        yield {"type": "done", "content": content,
               "reasoning": reasoning, "tool_calls": tool_calls}
