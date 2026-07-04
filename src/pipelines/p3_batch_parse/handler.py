"""P3 批量解析结果统计与异常检测。

汇总全部图纸的 MinerU 解析结果（版面块 / 表格 / 文字量），按专业分组，
支持按专业筛选（显式 discipline 参数 > prompt 专业关键词 > 全量），并在
统计之上做确定性异常检测（空内容 / 零版面块 / 无页图 / 文字量偏低）。
配置 LLM_API_KEY 后追加「语义分析」小节；未配置时统计层照常完整输出。

注：真正触发 MinerU 解析的是 adapters/mineru_real.py 的 parse_folder，
本流水线只读取并统计既有解析结果，不执行解析。
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from contracts.interfaces import AnswerType, Source, TaskResult  # noqa: E402
from src.pipelines.base import (DEFAULT_SCHEMA, _DISC_KW,  # noqa: E402
                                llm_analyze, llm_ready)

# 可筛选的专业（与 data/origin 子目录名一致）
_DISC_VALUES = ("建筑", "结构", "给排水", "电气")

# 异常检测阈值
_EMPTY_MD_CHARS = 50      # 空内容：markdown 去空白后少于 50 字
_LOW_TEXT_RATIO = 0.3     # 文字量偏低：低于本专业中位数的 30%
_LOW_TEXT_MIN_N = 3       # 该专业至少 3 张图纸才做中位数比较

_LLM_ROLE = "你是工程图纸解析质量分析师，熟悉 MinerU 与建筑各专业图纸特点"


def _resolve_filter(req) -> tuple[str | None, str]:
    """三级筛选：显式 discipline 参数 > prompt 专业关键词 > 全量。

    prompt 同时命中多个专业时视为对比场景，不筛选。
    返回 (专业名 | None, 来源标记 param/prompt/none)。
    """
    explicit = str((req.extra or {}).get("discipline") or "").strip()
    if explicit in _DISC_VALUES:
        return explicit, "param"
    matched = {disc.value for kw, disc in _DISC_KW.items()
               if kw in (req.prompt or "")}
    if len(matched) == 1:
        return matched.pop(), "prompt"
    return None, "none"


def _detect_anomalies(per_drawing: list[dict]) -> list[dict]:
    """确定性异常检测，规则见 PIPELINE.md。"""
    medians: dict[str, float] = {}
    for disc in {r["discipline"] for r in per_drawing}:
        chars = [r["chars"] for r in per_drawing
                 if r["discipline"] == disc and r["ok"]]
        if len(chars) >= _LOW_TEXT_MIN_N:
            medians[disc] = statistics.median(chars)

    anomalies = []
    for r in per_drawing:
        reasons = []
        if not r["ok"]:
            reasons.append("解析结果读取失败")
        else:
            is_empty = r["stripped_chars"] < _EMPTY_MD_CHARS
            if is_empty:
                reasons.append("空内容")
            if r["blocks"] == 0:
                reasons.append("零版面块")
            if r["pages"] == 0:
                reasons.append("无页图")
            median = medians.get(r["discipline"], 0)
            if (not is_empty and median > 0
                    and r["chars"] < median * _LOW_TEXT_RATIO):
                reasons.append("文字量偏低（<本专业中位数30%）")
        if reasons:
            anomalies.append({
                "id": r["id"], "name": r["name"],
                "discipline": r["discipline"], "reasons": reasons,
                "blocks": r["blocks"], "chars": r["chars"],
                "pages": r["pages"],
            })
    return anomalies


class BatchParse:
    name = "p3_batch_parse"
    description = ("批量解析结果统计与异常检测：汇总所有图纸的 MinerU 解析结果，"
                   "包括版面块数量、表格数量、文字内容量，并检出空内容/零版面块"
                   "等异常图纸。支持按专业筛选（discipline 参数或问题中的专业"
                   "关键词）。涉及『批量/统计/全部图纸/解析结果/总览/异常』时调用。")
    input_schema = {
        "type": "object",
        "properties": {
            **DEFAULT_SCHEMA["properties"],
            "discipline": {
                "type": "string", "enum": list(_DISC_VALUES),
                "description": "按专业筛选统计范围，可选",
            },
        },
        "required": ["prompt"],
    }

    def run(self, ctx, req) -> TaskResult:
        disc_filter, filter_source = _resolve_filter(req)
        drawings = ctx.drawings()
        if disc_filter:
            drawings = [d for d in drawings
                        if d.discipline.value == disc_filter]
        if not drawings:
            return TaskResult(
                AnswerType.TEXT,
                f"未找到{'『' + disc_filter + '』专业' if disc_filter else '任何'}图纸。",
                ok=False, error="no drawings")

        # ---- 统计层（确定性，永远完整输出）----
        per_drawing: list[dict] = []
        for d in drawings:
            row = {"id": d.id, "name": d.name,
                   "discipline": d.discipline.value,
                   "blocks": 0, "tables": 0, "chars": 0,
                   "stripped_chars": 0, "pages": 0, "ok": True}
            try:
                pd_ = ctx.parsed(d.id)
                row.update(
                    blocks=len(pd_.blocks), tables=len(pd_.tables()),
                    chars=len(pd_.markdown),
                    stripped_chars=len(pd_.markdown.strip()),
                    pages=len(pd_.page_images))
            except Exception:
                row["ok"] = False
            per_drawing.append(row)

        by_disc: dict[str, dict] = {}
        for r in per_drawing:
            g = by_disc.setdefault(r["discipline"], {
                "drawings": 0, "blocks": 0, "tables": 0, "chars": 0})
            g["drawings"] += 1
            g["blocks"] += r["blocks"]
            g["tables"] += r["tables"]
            g["chars"] += r["chars"]

        totals = {
            "drawings": len(per_drawing),
            "blocks": sum(r["blocks"] for r in per_drawing),
            "tables": sum(r["tables"] for r in per_drawing),
            "chars": sum(r["chars"] for r in per_drawing),
        }
        anomalies = _detect_anomalies(per_drawing)

        # ---- 报告 ----
        title = "## 批量图纸解析统计"
        if disc_filter:
            title += f"（筛选：{disc_filter} 专业）"
        lines = [title, ""]
        lines.append(f"共 **{totals['drawings']}** 张图纸，按专业分组：\n")
        lines.append(f"- 总版面块：**{totals['blocks']:,}**")
        lines.append(f"- 总表格数：**{totals['tables']:,}**")
        lines.append(f"- 总文字量：**{totals['chars']:,}** 字\n")

        for disc in sorted(by_disc):
            lines.append(f"### {disc}（{by_disc[disc]['drawings']} 张）")
            lines.append("| 图纸 | 版面块 | 表格 | 文字量 |")
            lines.append("|---|---|---|---|")
            for r in per_drawing:
                if r["discipline"] != disc:
                    continue
                if r["ok"]:
                    lines.append(f"| {r['name']} | {r['blocks']} | "
                                 f"{r['tables']} | {r['chars']:,} 字 |")
                else:
                    lines.append(f"| {r['name']} | 解析失败 | — | — |")
            lines.append(f"| **小计** | **{by_disc[disc]['blocks']}** | | |\n")

        lines.append("### 异常图纸检测")
        if anomalies:
            lines.append(f"共检出 **{len(anomalies)}** 张异常图纸：\n")
            lines.append("| 图纸 | 专业 | 异常 | 版面块 | 文字量 |")
            lines.append("|---|---|---|---|---|")
            for a in anomalies:
                lines.append(f"| {a['name']} | {a['discipline']} | "
                             f"{'、'.join(a['reasons'])} | {a['blocks']} | "
                             f"{a['chars']:,} 字 |")
        else:
            lines.append("未检出异常图纸。")
        lines.append("\n> 异常规则：空内容（Markdown<50 字）/ 零版面块 / 无页图 / "
                     "文字量低于本专业中位数 30%（该专业≥3 张时判定）。\n")

        # ---- 语义分析层（仅在配置 LLM 时启用）----
        if llm_ready():
            payload_lines = ["【各专业统计】"]
            for disc in sorted(by_disc):
                g = by_disc[disc]
                payload_lines.append(
                    f"{disc}：{g['drawings']} 张，版面块 {g['blocks']}，"
                    f"表格 {g['tables']}，文字量 {g['chars']}")
            payload_lines.append("【异常图纸】")
            for a in anomalies:
                payload_lines.append(
                    f"{a['discipline']}/{a['name']}：{'、'.join(a['reasons'])}"
                    f"（版面块 {a['blocks']}，文字量 {a['chars']}）")
            task = (f"用户问题：{req.prompt}。基于统计与异常清单给出："
                    "1) 各专业解析特征对比；2) 异常图纸归因假设；"
                    "3) 对下游任务（图纸问答/表格提取/跨图审查）的可用性提示。")
            try:
                insight = llm_analyze(ctx, _LLM_ROLE, task,
                                      "\n".join(payload_lines))
                lines.append("### 语义分析")
                lines.append(insight)
            except Exception as e:
                lines.append(f"（语义分析调用失败：{e}；以上统计结果完整可用）")
        else:
            lines.append("（语义分析层需配置 LLM_API_KEY 后启用；"
                         "以上统计结果完整可用）")

        data = {
            "filter": {"discipline": disc_filter, "source": filter_source},
            "totals": totals,
            "by_discipline": by_disc,
            "per_drawing": [{k: v for k, v in r.items()
                             if k != "stripped_chars"} for r in per_drawing],
            "anomalies": anomalies,
        }
        return TaskResult(
            AnswerType.MARKDOWN_LIST, "\n".join(lines),
            evidence=[Source(drawing=d.id) for d in drawings[:10]],
            data=data)
