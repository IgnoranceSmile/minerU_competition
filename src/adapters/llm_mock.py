"""Mock 推理网关。无 LLM_API_KEY 时用它跑通链路、做离线测试。

三种调用场景：
1. role=tool 的消息在末尾  → 工具结果已回来，复述为最终答案
2. 无 tools（Pipeline 内部 llm_qa）→ 抽取式兜底 QA
3. 有 tools                  → 关键词路由，模拟 LLM 选 Pipeline
"""
from __future__ import annotations

_ROUTES = [
    (("表格", "统计表", "门窗", "材料表", "提取"), "p2_table_extract"),
    (("批量", "全部", "统计", "总览"), "p3_batch_parse"),
    (("一致", "冲突", "核对", "对应", "比对"), "p4_cross_drawing"),
    (("质量", "验证", "检查", "完整性"), "p5_quality_verify"),
]


class MockLLMGateway:
    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             stream: bool = True):
        last = messages[-1]
        role = last.get("role")
        content = str(last.get("content", ""))

        # 1) 工具结果回来 → 复述
        if role == "tool":
            yield {"type": "reasoning", "delta": "[mock] 工具已返回，整理结果。"}
            yield {"type": "content", "delta": content}
            yield {"type": "done", "content": content,
                   "reasoning": "", "tool_calls": []}
            return

        # 2) 无工具 → 纯 QA 兜底
        if not tools:
            ans = _mock_qa(content)
            yield {"type": "content", "delta": ans}
            yield {"type": "done", "content": ans,
                   "reasoning": "", "tool_calls": []}
            return

        # 3) 有工具 → 关键词路由
        pipeline = "p1_drawing_qa"
        for kws, name in _ROUTES:
            if any(k in content for k in kws):
                pipeline = name
                break
        yield {"type": "reasoning", "delta": f"[mock] 意图识别 → 调用 {pipeline}。"}
        yield {"type": "done", "content": "", "reasoning": "",
               "tool_calls": [{"id": "call_mock", "name": pipeline,
                               "arguments": {"prompt": content}}]}


def _mock_qa(user_content: str) -> str:
    """[mock] 仅验证链路。"""
    q = user_content.split("【问题】")[-1].strip()
    body = (user_content.split("【图纸内容】")[-1]
            .split("【问题】")[0].strip())
    return (f"[mock-QA·未接入真实模型] 问题：「{q}」\n"
            f"相关图纸片段：{body[:160]}…")
