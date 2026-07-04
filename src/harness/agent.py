"""DrawAgent 任务规划器（Agent 工具调用循环）。

run() 是生成器，逐事件 yield，供 server.py 转成 SSE 推给前端：
  reasoning    思考过程增量
  content      正文增量
  plan         执行计划。两类：开场 status=analyzing 一条；
               此后每轮拿到 tool_calls 后、执行前发 status=executing 一条，
               steps 字段如实列出本轮将调用的 Pipeline 及参数
  progress     执行进度
  tool_start   开始调用某 Pipeline
  tool_result  Pipeline 返回
  done         本轮结束

核心能力：多步任务自动分解、工具调用循环、异常恢复。
LLM 在 content/reasoning 里写的文字计划照旧流式输出，
plan 事件是 harness 层对每轮实际执行计划的结构化记录，两者互补。
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from contracts.interfaces import Region, RegionKind, BBox, TaskRequest  # noqa: E402
from src.harness.registry import PipelineRegistry  # noqa: E402

MAX_ITERS = 12

SYSTEM_PROMPT = """你是 DrawAgent — 基于 MinerU 的工程图纸智能解析智能体。
你处理按专业（建筑/结构/给排水/电气）分类的工程图纸，通过调用 Pipeline 完成复杂任务。

## 可用 Pipeline

- p1_drawing_qa    单张图纸内容问答（图签信息、标注说明、文字内容提取）
- p2_table_extract  表格精准提取（门窗表、材料表、构件统计表等结构化提取）
- p3_batch_parse    批量解析结果统计与异常检测（支持按专业筛选，可传 discipline 参数）
- p4_cross_drawing  跨图纸比对（图号/设计号一致性、目录与实际图纸对应）
- p5_quality_verify 解析质量验证（完整性检查、数据准确性验证、异常识别）

## 任务规划规则（必须严格遵守）

1. **简单任务**（单张图纸、单一问题）→ 直接调用对应 Pipeline，一次完成
2. **复合任务**（涉及多张图纸、多步骤、多维度分析）→ 必须按以下流程：
   a. 在思考中先输出执行计划：列出需要的步骤和每个步骤调用的 Pipeline
   b. 按计划逐步执行，每步完成后评估结果
   c. 如果某步结果异常，调整后续步骤
   d. 最后综合所有步骤结果，给出完整结论

3. **任务分解示例**：
   - "帮我做一次完整的图纸审查" →
     Step 1: p4_cross_drawing（跨图一致性检查）
     Step 2: p5_quality_verify（解析质量验证）
     Step 3: 综合两份报告给出审查结论
   - "给排水专业解析得怎么样？有什么问题？" →
     Step 1: p3_batch_parse（获取统计数据）
     Step 2: p5_quality_verify（验证质量，重点关注给排水）
     Step 3: 分析问题并给出建议
   - "哪些图纸解析结果异常？" →
     Step 1: p3_batch_parse（获取全局统计）
     Step 2: p5_quality_verify（检查每张图纸质量）
     Step 3: 分类汇总异常图纸

## 异常处理

- 如果工具返回错误，分析错误原因，尝试换一种方式完成（如换参数重试、换 Pipeline）
- 如果某个 Pipeline 执行失败，不要放弃，向用户说明失败原因并给出已获取的部分结果

## 输出要求

- 必须调用工具获取数据，禁止凭空作答
- 用中文回答，技术名词保留英文
- 先给结论，再给分析过程
- 涉及数据时用表格呈现"""


class TaskPlanner:
    def __init__(self, ctx, registry: PipelineRegistry) -> None:
        self.ctx = ctx
        self.registry = registry
        self._trace: list[dict] = []

    def run(self, user_prompt: str, target_drawing: str | None = None,
            region_dict: dict | None = None):
        self._trace = []
        region = _parse_region(region_dict)
        turn = TaskRequest(prompt=user_prompt, target_drawing=target_drawing,
                           region=region)
        tools = self.registry.tools_schema()
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _augment(user_prompt, target_drawing,
                                                 region)},
        ]

        yield {"type": "plan", "status": "analyzing",
               "message": "正在分析任务..."}

        for i in range(MAX_ITERS):
            done = None
            for ev in self.ctx.llm.chat(messages, tools=tools, stream=True):
                if ev["type"] in ("reasoning", "content"):
                    yield ev
                elif ev["type"] == "done":
                    done = ev

            tool_calls = done.get("tool_calls") if done else None
            if not tool_calls:
                yield {"type": "done", "content": done["content"] if done else ""}
                self._flush_trace(user_prompt, done.get("content", "") if done else "")
                return

            # 本轮执行计划：如实反映 LLM 决定调用哪些 Pipeline、带什么参数
            yield {"type": "plan", "status": "executing",
                   "iteration": i + 1,
                   "steps": [{"pipeline": tc["name"],
                              "arguments": tc["arguments"]}
                             for tc in tool_calls],
                   "message": (f"第 {i + 1} 轮计划：调用 "
                               + ", ".join(tc["name"] for tc in tool_calls))}

            assistant_msg: dict = {
                "role": "assistant",
                "content": done.get("content") or None,
                "tool_calls": [{
                    "id": tc["id"], "type": "function",
                    "function": {"name": tc["name"],
                                 "arguments": json.dumps(tc["arguments"],
                                                         ensure_ascii=False)},
                } for tc in tool_calls],
            }
            if done.get("reasoning"):
                assistant_msg["reasoning_content"] = done["reasoning"]
            messages.append(assistant_msg)

            for tc in tool_calls:
                step_start = time.time()
                yield {"type": "tool_start", "name": tc["name"]}
                yield {"type": "progress",
                       "step": i + 1, "max_steps": MAX_ITERS,
                       "pipeline": tc["name"],
                       "message": f"执行 {tc['name']}..."}
                try:
                    result = self.registry.dispatch(tc["name"], tc["arguments"],
                                                     self.ctx, turn)
                except Exception as e:
                    from contracts.interfaces import TaskResult, AnswerType
                    result = TaskResult(answer_type=AnswerType.TEXT,
                                        content="", ok=False,
                                        error=f"Pipeline 执行异常: {e}")
                elapsed = time.time() - step_start
                yield {"type": "tool_result", "name": tc["name"],
                       "result": _result_dict(result)}

                self._trace.append({
                    "step": i + 1,
                    "pipeline": tc["name"],
                    "arguments": tc["arguments"],
                    "ok": result.ok,
                    "elapsed_ms": round(elapsed * 1000),
                    "content_preview": (result.content or "")[:200],
                    "error": result.error if not result.ok else "",
                })

                tool_msg = _tool_content(result)
                if not result.ok:
                    tool_msg += ("\n\n[系统提示：该 Pipeline 执行失败，请分析错误原因，"
                                 "考虑是否需要重试、调整参数、或换用其他 Pipeline 继续。]"
                                 )
                messages.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "content": tool_msg})

        yield {"type": "done", "content": "（达到最大工具调用轮数）"}
        self._flush_trace(user_prompt, "")

    def _flush_trace(self, prompt: str, answer: str):
        import logging
        logger = logging.getLogger("drawagent.trace")
        logger.info(json.dumps({
            "type": "execution_trace",
            "prompt": prompt,
            "answer_preview": answer[:300],
            "steps": len(self._trace),
            "trace": self._trace,
        }, ensure_ascii=False))

    def last_trace(self) -> list[dict]:
        return list(self._trace)


# ===== 辅助 =====

def _parse_region(d: dict | None) -> Region | None:
    if not d:
        return None
    kind = RegionKind(d["kind"])
    bbox = None
    if d.get("bbox"):
        b = d["bbox"]
        bbox = BBox(b[0], b[1], b[2], b[3], d.get("page", 0))
    return Region(kind=kind, drawing_id=d.get("drawing_id", ""),
                  page=d.get("page", 0), x=d.get("x"), y=d.get("y"), bbox=bbox)


def _augment(prompt: str, target: str | None, region: Region | None) -> str:
    parts = [prompt]
    if target:
        parts.append(f"[用户已指定图纸：{target}]")
    if region and region.kind == RegionKind.POINT:
        parts.append(f"[用户在图纸 {region.drawing_id} 上点选了一个位置]")
    if region and region.kind == RegionKind.BOX:
        parts.append(f"[用户在图纸 {region.drawing_id} 上框选了一个区域]")
    return " ".join(parts)


def _result_dict(r) -> dict:
    d = asdict(r)
    d["answer_type"] = r.answer_type.value
    return d


def _tool_content(r) -> str:
    if not r.ok:
        return f"工具执行失败：{r.error}"
    note = ""
    if r.extra_images:
        note = f"\n（已生成 {len(r.extra_images)} 张标注图，展示给用户）"
    return (r.content or "(无文字内容)") + note
