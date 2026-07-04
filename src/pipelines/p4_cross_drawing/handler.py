"""P4 跨图纸比对。

提取全部图签 + 目录条目，做四类确定性规则检查——
设计号一致性、目录缺图、图号冲突、引用完整性。
规则层零 LLM 参与、结果可复现；检查结论同时写入 TaskResult.data
（结构化 findings，供导出与测试消费）。

配置 LLM_API_KEY 后追加「按优先级排序的修复建议」小节
（把规则检查报告交给 LLM 做审查顾问式分析）；
未配置时该层跳过并注明，规则检查结果始终完整输出。
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from contracts.interfaces import AnswerType, Source, TaskResult  # noqa: E402
from src.pipelines.base import DEFAULT_SCHEMA, llm_analyze, llm_ready  # noqa: E402
from src.pipelines._common import (discipline_of, extract_catalog_entries,  # noqa: E402
                                    extract_title_block, ocr_correct)

_DISC_MAP = {"建筑": "建施", "结构": "结施", "给排水": "水施", "电气": "电施"}

# 引用模式
_P_INTERNAL = re.compile(r"(?:见|参见|做法见|按|施工见|详见图?)\s*图\s*(\d+)(?!\d)")
_P_CROSS = re.compile(r"(?:详见|见|参见)\s*((?:结施|建施|电施|水施)\s*[-–]?\s*\d+)")
_P_ATLAS = re.compile(r"[《]?\s*(\d{2}\s*[GJSG]\s*\d{3}\s*[-–—]\s*\d+)\s*[》]?")

_ADVISOR_ROLE = "你是施工图审查顾问，熟悉设计文件管理与图纸目录规范"
_ADVISOR_TASK = ("基于以下跨图一致性检查结果，输出按优先级排序的修复建议，"
                 "说明每项的影响面与处理顺序依据")
_NO_LLM_NOTE = "（修复建议层需配置 LLM_API_KEY 后启用；以上规则检查结果完整可用）"


def _digits(s: str) -> str:
    m = re.search(r"(\d+)", s or "")
    return m.group(1).lstrip("0") or "0" if m else ""


class CrossDrawing:
    name = "p4_cross_drawing"
    description = ("跨图纸比对：检查全部图纸的设计号一致性、目录与实际图纸对应、"
                   "图号冲突、引用完整性。涉及『一致/冲突/核对/缺失/齐全/引用/比对』时调用。")
    input_schema = DEFAULT_SCHEMA

    def run(self, ctx, req) -> TaskResult:
        # 全部图签
        tbs: dict[str, dict] = {}
        catalogs: dict[str, list] = {}
        for d in ctx.drawings():
            md = ocr_correct(ctx.parsed(d.id).markdown)
            tbs[d.id] = extract_title_block(md)
            if "目录" in d.name:
                catalogs[d.id] = extract_catalog_entries(md)

        lines = ["## 跨图纸比对报告", ""]

        # 检查1 设计号
        nos = sorted({tb["design_no"] for tb in tbs.values() if tb.get("design_no")})
        if len(nos) <= 1:
            lines.append(f"### 设计号一致性\n已比对 {len(tbs)} 张图，"
                         f"设计号统一：{nos[0] if nos else '未识别'}")
        else:
            lines.append(f"### 设计号冲突\n发现 {len(nos)} 种设计号："
                         f"{'、'.join(nos)}")
            by_no: dict[str, list] = defaultdict(list)
            for did, tb in tbs.items():
                if tb.get("design_no"):
                    by_no[tb["design_no"]].append(did.split("/")[0])
            for no in nos:
                discs = sorted(set(by_no[no]))
                lines.append(f"- `{no}`　涉及专业：{'、'.join(discs)}")
        lines.append("")

        # 实际图签集合
        actual: set[tuple[str, str]] = set()
        num_owners: dict[tuple[str, str], list] = defaultdict(list)
        for d in ctx.drawings():
            tb = tbs[d.id]
            disc = _DISC_MAP.get(d.discipline.value, "")
            num = tb.get("drawing_number", "")
            if disc and num:
                key = (disc, _digits(num))
                actual.add(key)
                num_owners[key].append(d.name)

        # 检查2 目录缺图
        missing: list[dict] = []
        for cat_id, entries in catalogs.items():
            for e in entries:
                disc = discipline_of(e["number"])
                key = (disc, _digits(e["number"]))
                if disc and key not in actual:
                    missing.append({"number": e["number"], "name": e["name"],
                                    "source": cat_id.split("/")[-1]})
        lines.append("### 缺失图纸检查")
        if missing:
            lines.append(f"目录列出但图纸包中缺失 {len(missing)} 项：\n")
            lines.append("| 图号 | 图名 | 目录来源 |")
            lines.append("|---|---|---|")
            for item in missing:
                lines.append(f"| {item['number']} | {item['name'] or '—'} "
                             f"| {item['source']} |")
        else:
            lines.append("目录所列图纸均能在图纸包中找到对应图签。")
        lines.append("")

        # 检查3 图号冲突
        conflicts: list[dict] = [
            {"number": f"{disc}-{num.zfill(2)}", "owners": owners}
            for (disc, num), owners in num_owners.items() if len(owners) > 1]
        lines.append("### 图号冲突检查")
        if conflicts:
            lines.append(f"{len(conflicts)} 个图号被多张图纸占用：")
            for c in conflicts:
                lines.append(f"- {c['number']}：{'、'.join(c['owners'])}")
        else:
            lines.append("未发现同专业图号冲突。")
        lines.append("")

        # 检查4 引用关系
        atlas: set[str] = set()
        ref_total = 0
        for d in ctx.drawings():
            md = ocr_correct(ctx.parsed(d.id).markdown)
            cross = set(_P_CROSS.findall(md))
            std = set(re.sub(r"\s", "", x) for x in _P_ATLAS.findall(md))
            atlas |= std
            ref_total += len(cross) + len(std)

        if atlas:
            lines.append(f"### 国标图集引用（{len(atlas)} 个）")
            lines.append("、".join(f"`{a}`" for a in sorted(atlas)))

        lines.append(f"\n共检出 **{ref_total}** 条跨图纸引用关系。")

        # 结构化 findings（与上文报告结论一一对应，JSON-safe）
        data = {
            "design_nos": nos,
            "design_no_consistent": len(nos) <= 1,
            "missing": missing,
            "conflicts": conflicts,
            "atlas_refs": sorted(atlas),
            "ref_total": ref_total,
            "checked_drawings": len(tbs),
        }

        # 修复建议层（可选 LLM）：规则检查报告 → 审查顾问式优先级建议。
        # 任何情况下不影响上文规则层输出。
        rule_report = "\n".join(lines)
        if llm_ready():
            try:
                advice = llm_analyze(ctx, role=_ADVISOR_ROLE,
                                     task=_ADVISOR_TASK, payload=rule_report)
                lines.append("\n### 按优先级排序的修复建议\n")
                lines.append(advice)
            except Exception as e:
                lines.append(f"\n（修复建议层调用失败：{e}；"
                             f"以上规则检查结果完整可用）")
        else:
            lines.append(f"\n{_NO_LLM_NOTE}")

        return TaskResult(
            AnswerType.MARKDOWN_LIST, "\n".join(lines),
            evidence=[Source(drawing=cid) for cid in catalogs],
            data=data)
