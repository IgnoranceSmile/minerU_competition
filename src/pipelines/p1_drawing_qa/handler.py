"""P1 单张图纸内容问答。

先确定性抽取图签结构化字段（图号/图别/版本/日期/项目名/图名/各角色负责人/设计号），
再连同 OCR 纠错后的正文一起喂 LLM 作答。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from contracts.interfaces import AnswerType, Source, TaskResult  # noqa: E402
from src.pipelines.base import DEFAULT_SCHEMA, llm_qa, resolve_drawing  # noqa: E402
from src.pipelines._common import extract_title_block, ocr_correct  # noqa: E402

_FIELD_CN = [
    ("design_no", "设计号"), ("discipline", "图别"), ("drawing_number", "图号"),
    ("drawing_title", "图纸名称"), ("project_name", "项目名称"),
    ("version", "版本"), ("date", "日期"),
    ("project_director", "项目总负责人"), ("authorized_by", "审定人"),
    ("discipline_lead", "专业负责人"), ("checked_by", "校对人"),
    ("designer", "设计人"), ("drawn_by", "制图人"),
]


class DrawingQA:
    name = "p1_drawing_qa"
    description = ("回答关于单张图纸的事实性问题：图号、图别、版本、出图日期、"
                   "项目名称、设计号、各角色负责人（专业负责人/审定人/设计人等）、"
                   "层高、材料、电算程序等。答案来自图签与图纸正文。")
    input_schema = DEFAULT_SCHEMA

    def run(self, ctx, req) -> TaskResult:
        d = resolve_drawing(ctx, req)
        if d is None:
            return TaskResult(
                AnswerType.TEXT,
                "未能定位目标图纸，请点选图纸或在问题中写明图纸名称。",
                ok=False, error="no target drawing")

        md = ocr_correct(ctx.parsed(d.id).markdown)
        tb = extract_title_block(md)
        tb_text = "\n".join(f"- {cn}：{tb[k]}" for k, cn in _FIELD_CN if tb.get(k))
        context = (
            f"【图签信息（确定性抽取）】\n{tb_text or '（未识别到标准图签）'}\n\n"
            f"【图纸正文】\n{md[:7000]}"
        )
        answer = llm_qa(ctx, req.prompt, context)
        return TaskResult(
            AnswerType.TEXT, answer,
            evidence=[Source(drawing=d.id, note=f"来源：{d.name}（图签+正文）")])
