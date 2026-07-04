"""P5 解析质量验证：四维打分 + 问题清单 + LLM 修复建议。

对每张图纸的 MinerU 解析结果做四个维度的 0-100 打分：
- 文字维：OCR 纠错后 markdown 字符量分级（工程图注记千字级、说明类图纸万字级）
- 版面维：版面块数量分级（占 70%）+ 页渲染图存在性（占 30%）
- 图签维：图签识别字段数 / 13 个标准字段（与 P1 的 _FIELD_CN 对齐）
- 表格维：可转换表格占比；无表格图纸记 N/A，权重按比例摊给其余三维

单图总分 = 四维加权（文字 30% / 版面 20% / 图签 30% / 表格 20%），
全集总分 = 各图单图总分的算术平均。原先的"解析成功张数占比"降级为参考指标，
仍在报告中展示并注明口径。打分层为纯规则实现，无密钥可离线复现；
配置 LLM_API_KEY 后追加"按优先级排序的修复建议"小节。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from contracts.interfaces import AnswerType, Source, TaskResult  # noqa: E402
from src.pipelines.base import DEFAULT_SCHEMA, llm_analyze, llm_ready  # noqa: E402
from src.pipelines._common import extract_title_block, ocr_correct  # noqa: E402
from src.harness.renderer import html_table_to_md  # noqa: E402


# ===== 打分层（模块级纯函数，零 LLM 依赖，便于单测与复现） =====

#: 四维权重。文字与图签是图纸问答/检索类下游任务的信息主体与身份锚点，各占 30%；
#: 版面块与表格反映解析结果的结构化程度与结构化数据可用性，各占 20%。
WEIGHTS = {"text": 0.30, "layout": 0.20, "title_block": 0.30, "table": 0.20}

#: 图签标准字段总数（设计号/图别/图号/图纸名称/项目名称/版本/日期/
#: 项目总负责人/审定人/专业负责人/校对人/设计人/制图人，见 P1 的 _FIELD_CN）。
TITLE_BLOCK_FIELD_COUNT = 13

#: 文字维分级：(字符数下限, 得分)，从高到低匹配。
_TEXT_GRADES = [(10000, 100.0), (3000, 90.0), (1000, 80.0),
                (300, 60.0), (1, 30.0)]

#: 判定单个表格"可转换"的最短 Markdown 长度（字符）。
_TABLE_MD_MIN_CHARS = 20


def score_text(chars: int) -> float:
    """文字维：OCR 纠错后 markdown 字符量分级评分。

    工程图纸正常图面注记在千字级（≥1000 字符 = 80 分档），
    说明类图纸达万字级（≥10000 = 100 分档）；千字以下按解析不完整程度
    递减给分（≥300 = 60，≥1 = 30），空内容记 0。
    """
    for lo, s in _TEXT_GRADES:
        if chars >= lo:
            return s
    return 0.0


def score_layout(blocks: int, has_pages: bool) -> float:
    """版面维：版面块数量分级（占 70%）+ 页渲染图存在性（占 30%）。

    块数分级：0 块 = 0（未检出任何版面）；1–2 块 = 60（整页单块，切分粗糙）；
    3–9 块 = 80（正常切分）；≥10 块 = 100（切分精细）。
    """
    if blocks >= 10:
        block_score = 100.0
    elif blocks >= 3:
        block_score = 80.0
    elif blocks >= 1:
        block_score = 60.0
    else:
        block_score = 0.0
    return block_score * 0.7 + (30.0 if has_pages else 0.0)


def score_title_block(fields: int) -> float:
    """图签维：识别字段数 / 13 个标准字段 × 100。"""
    return min(fields, TITLE_BLOCK_FIELD_COUNT) / TITLE_BLOCK_FIELD_COUNT * 100.0


def score_table(ok_tables: int, total_tables: int) -> float | None:
    """表格维：可转换表格占比 × 100；无表格图纸记 N/A（返回 None）。

    工程图未必有表格，无表格不应惩罚：N/A 时该图加权阶段把表格维权重
    按比例摊给其余三维（见 score_drawing）。
    """
    if total_tables == 0:
        return None
    return ok_tables / total_tables * 100.0


def score_drawing(detail: dict) -> dict:
    """单图四维打分 + 加权合成总分（纯函数）。

    入参 detail 需含 6 个键：chars / blocks / has_pages /
    title_block_fields / tables / ok_tables。
    返回 {"text", "layout", "title_block", "table", "total"}，
    各维四舍五入到 1 位小数；表格维无表格时为 None。
    总分先用未取整的各维分加权（表格维 N/A 时其权重按比例摊给
    其余三维，即剩余权重归一化），再取整到 1 位小数。
    """
    raw = {
        "text": score_text(detail["chars"]),
        "layout": score_layout(detail["blocks"], detail["has_pages"]),
        "title_block": score_title_block(detail["title_block_fields"]),
        "table": score_table(detail["ok_tables"], detail["tables"]),
    }
    valid = {k: v for k, v in raw.items() if v is not None}
    weight_sum = sum(WEIGHTS[k] for k in valid)
    total = sum(v * WEIGHTS[k] / weight_sum for k, v in valid.items())
    out = {k: (None if v is None else round(v, 1)) for k, v in raw.items()}
    out["total"] = round(total, 1)
    return out


def _fmt_score(v: float | None) -> str:
    """分数显示：N/A 或去尾零数字（100 而非 100.0）。"""
    return "N/A" if v is None else f"{v:g}"


class QualityVerify:
    name = "p5_quality_verify"
    description = ("解析质量验证：对 MinerU 解析结果按 文字量/版面块/图签识别/"
                   "表格可转换性 四维度打分（0-100），输出总分、各图评分详情、"
                   "问题清单。涉及『质量/验证/检查/完整性/解析效果/评分』时调用。")
    input_schema = DEFAULT_SCHEMA

    def run(self, ctx, req) -> TaskResult:
        drawings = ctx.drawings()
        if not drawings:
            return TaskResult(
                AnswerType.TEXT, "当前未加载任何图纸。",
                ok=False, error="no drawings")

        total = len(drawings)
        ok_count = 0
        empty_md: list[str] = []
        weak_title_block: list[tuple[str, int]] = []
        bad_tables: list[tuple[str, str]] = []
        per_drawing: list[dict] = []
        display_rows: list[dict] = []

        for d in drawings:
            pd_ = ctx.parsed(d.id)
            md = ocr_correct(pd_.markdown)

            # 参考指标口径：是否解析出内容（不反映质量）
            has_md = len(md.strip()) > 50
            if has_md and len(pd_.blocks) > 0:
                ok_count += 1
            if not has_md:
                empty_md.append(d.name)

            # 图签识别（空内容图纸不重复列入图签问题清单）
            tb = extract_title_block(md)
            tb_fields = sum(1 for v in tb.values() if v)
            if has_md and tb_fields < 3:
                weak_title_block.append((d.name, tb_fields))

            # 表格可转换性检查
            ok_tables = 0
            for block in pd_.tables():
                converted = ""
                if block.html:
                    try:
                        converted = html_table_to_md(block.html)
                    except Exception:
                        converted = ""
                if len(converted) >= _TABLE_MD_MIN_CHARS:
                    ok_tables += 1
                else:
                    bad_tables.append((d.name, block.id))

            detail = {
                "chars": len(md),
                "blocks": len(pd_.blocks),
                "has_pages": len(pd_.page_images) > 0,
                "title_block_fields": tb_fields,
                "tables": len(pd_.tables()),
                "ok_tables": ok_tables,
            }
            scores = score_drawing(detail)
            per_drawing.append({
                "name": d.name,
                "discipline": d.discipline.value,
                "scores": {k: scores[k] for k in
                           ("text", "layout", "title_block", "table")},
                "total": scores["total"],
            })
            display_rows.append({**detail, "name": d.name,
                                 "discipline": d.discipline.value,
                                 "scores": scores})

        overall = round(sum(p["total"] for p in per_drawing) / total, 1)
        success_rate = round(ok_count / total * 100, 1)
        w = WEIGHTS

        # 汇总
        lines = ["## MinerU 解析质量验证报告", ""]
        lines.append(f"### 总体评分：{overall:.0f}/100")
        lines.append(
            f"- 口径：单图总分 = 文字 {w['text']:.0%} + 版面 {w['layout']:.0%}"
            f" + 图签 {w['title_block']:.0%} + 表格 {w['table']:.0%} 加权"
            f"（无表格图纸表格维记 N/A，其权重按比例摊给其余三维）；"
            f"全集总分 = {total} 张图纸单图总分的算术平均，精确值 {overall}")
        lines.append(
            f"- 解析成功率（参考指标）：{success_rate:.0f}%（{ok_count}/{total} 张；"
            f"口径：markdown 超 50 字符且版面块非空，只反映『是否解析出内容』，"
            f"不反映解析质量）")
        lines.append(f"- 空内容：{len(empty_md)} 张")
        lines.append(f"- 图签识别不足（<3 字段，空内容图纸不重复计）："
                     f"{len(weak_title_block)} 张")
        lines.append(f"- 表格解析异常：{len(bad_tables)} 个\n")

        # 各图评分详情
        lines.append("### 各图纸评分详情（每维 0-100）")
        lines.append("| 图纸 | 专业 | 文字量 | 文字维 | 版面维 | 图签维 | 表格维 | 总分 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for r in display_rows:
            s = r["scores"]
            lines.append(
                f"| {r['name']} | {r['discipline']} | {r['chars']:,} | "
                f"{_fmt_score(s['text'])} | {_fmt_score(s['layout'])} | "
                f"{_fmt_score(s['title_block'])} | {_fmt_score(s['table'])} | "
                f"{_fmt_score(s['total'])} |")

        # 问题清单
        if empty_md:
            lines.append("\n### 内容为空的图纸（文字维 0 分主因）")
            for n in empty_md:
                lines.append(f"- {n}")
        if weak_title_block:
            lines.append("\n### 图签识别不足的图纸（拉低图签维）")
            for n, f in weak_title_block:
                lines.append(f"- {n}（仅识别 {f}/{TITLE_BLOCK_FIELD_COUNT} 个字段）")
        if bad_tables:
            lines.append("\n### 表格解析异常（拉低表格维）")
            for n, bid in bad_tables:
                lines.append(f"- {n} · {bid}")

        # 语义分析层：修复建议（可选，规则打分结果不依赖它）
        if llm_ready():
            payload_lines = [
                f"全集总分：{overall}/100；解析成功率（参考）：{success_rate}%",
                "各图四维评分（图纸/专业/文字/版面/图签/表格/总分）："]
            for p in per_drawing:
                s = p["scores"]
                payload_lines.append(
                    f"{p['name']}/{p['discipline']}/{_fmt_score(s['text'])}/"
                    f"{_fmt_score(s['layout'])}/{_fmt_score(s['title_block'])}/"
                    f"{_fmt_score(s['table'])}/{_fmt_score(p['total'])}")
            payload_lines.append(
                "空内容图纸：" + ("、".join(empty_md) or "无"))
            payload_lines.append(
                "图签识别不足：" + ("、".join(
                    f"{n}({f}字段)" for n, f in weak_title_block) or "无"))
            payload_lines.append(
                "表格解析异常：" + ("、".join(
                    f"{n}·{b}" for n, b in bad_tables) or "无"))
            try:
                advice = llm_analyze(
                    ctx,
                    role="你是工程图纸数据治理顾问，熟悉 MinerU 解析产物与 "
                         "CAD 转 PDF 的常见质量问题",
                    task="根据以下四维评分汇总与问题清单，给出按优先级排序的修复建议；"
                         "每条建议注明针对哪些图纸、预期改善哪个维度",
                    payload="\n".join(payload_lines))
                lines.append("\n### 修复建议（LLM 生成，按优先级排序）")
                lines.append(advice)
            except Exception as e:
                lines.append(f"\n（修复建议调用失败：{e}；以上评分结果完整可用）")
        else:
            lines.append("\n（语义分析层需配置 LLM_API_KEY 后启用；"
                         "以上评分结果完整可用）")

        lines.append(f"\n> 验证基于 MinerU 解析结果（content_list.json + Markdown），"
                     f"共检查 {total} 张图纸；打分层为纯规则实现，无密钥可复现。")

        data = {
            "overall_score": overall,
            "weights": dict(WEIGHTS),
            "success_rate": success_rate,
            "per_drawing": per_drawing,
            "issues": {
                "empty_content": empty_md,
                "weak_title_block": [{"name": n, "fields": f}
                                     for n, f in weak_title_block],
                "bad_tables": [{"name": n, "block_id": b}
                               for n, b in bad_tables],
            },
        }
        return TaskResult(
            AnswerType.MARKDOWN_LIST, "\n".join(lines),
            evidence=[Source(drawing=d.id) for d in drawings[:5]],
            data=data)
