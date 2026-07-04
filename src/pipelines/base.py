"""Pipeline 基础设施：统一 input schema、目标图纸定位、纯 QA 调用。

每个 Pipeline 是一个满足 contracts.Pipeline Protocol 的类：
有 name / description / input_schema 三个属性 + run(ctx, req) 方法。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from contracts.interfaces import Discipline  # noqa: E402

DEFAULT_SCHEMA = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string", "description": "用户的完整问题原文"},
        "target_drawing": {"type": "string",
                           "description": "目标图纸名称或 id，如『结构设计说明』，可选"},
    },
    "required": ["prompt"],
}

_DISC_KW = {"建筑": Discipline.BUILDING, "结构": Discipline.STRUCTURE,
            "给排水": Discipline.PLUMBING, "排水": Discipline.PLUMBING,
            "给水": Discipline.PLUMBING, "电气": Discipline.ELECTRICAL}


def resolve_drawing(ctx, req):
    """定位目标图纸：显式 target → 点选 region → 图名匹配 → 专业关键词兜底。"""
    if req.target_drawing:
        d = ctx.find_drawing(req.target_drawing)
        if d:
            return d
    if req.region and req.region.drawing_id:
        d = ctx.get_drawing(req.region.drawing_id)
        if d:
            return d
    best, best_score = None, 0
    for d in ctx.drawings():
        grams = {d.name[i:i + 2] for i in range(len(d.name) - 1)}
        score = sum(1 for g in grams if g in req.prompt)
        if score > best_score:
            best, best_score = d, score
    if best is not None and best_score >= 2:
        return best
    for kw, disc in _DISC_KW.items():
        if kw in req.prompt:
            for d in ctx.drawings():
                if d.discipline == disc and "说明" in d.name:
                    return d
    return None


def llm_ready() -> bool:
    """是否配置了真实 LLM。未配置时各 Pipeline 的语义分析层跳过（规则层照常输出）。"""
    from config import LLM_API_KEY
    return bool(LLM_API_KEY)


def llm_analyze(ctx, role: str, task: str, payload: str,
                max_chars: int = 8000) -> str:
    """用 ctx.llm 做一次分析类调用（角色设定与数据分离，无工具）。

    role    系统角色设定，如「你是表格数据提取专家…」
    task    本次分析任务（通常含用户原始问题）
    payload 待分析数据（统计表 / 表格 markdown / 检查报告等）
    """
    msgs = [
        {"role": "system", "content": role},
        {"role": "user",
         "content": f"【任务】{task}\n\n【数据】\n{payload[:max_chars]}"},
    ]
    out = ""
    for ev in ctx.llm.chat(msgs, tools=None, stream=True):
        if ev["type"] == "content":
            out += ev["delta"]
        elif ev["type"] == "done" and not out:
            out = ev.get("content", "")
    return out.strip()


def llm_qa(ctx, question: str, context: str) -> str:
    """用 ctx.llm 做一次无工具的纯问答（只依据给定图纸内容）。"""
    msgs = [
        {"role": "system",
         "content": "你是工程图纸解析助手。只依据给定的图纸内容回答，"
                    "简洁准确，不杜撰；信息缺失时明说『图纸中未提及』。"},
        {"role": "user",
         "content": f"【图纸内容】\n{context}\n\n【问题】{question}"},
    ]
    out = ""
    for ev in ctx.llm.chat(msgs, tools=None, stream=True):
        if ev["type"] == "content":
            out += ev["delta"]
        elif ev["type"] == "done" and not out:
            out = ev.get("content", "")
    return out.strip()
