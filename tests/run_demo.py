"""一键演示脚本：按 sample_queries.md 跑 5 个 Pipeline 的代表性示例。

默认不需要 LLM_API_KEY、不需要 GPU：
    使用 src/adapters/llm_mock.py 的关键词路由 LLM
    + 本地 data/origin/ 与 data/mineru/ 下已有的图纸和 MinerU 解析结果

每个示例的执行轨迹（pipeline / arguments / ok / elapsed_ms / content_preview / error）
都会写入本地 logs/trace.jsonl，便于查看可追溯性。

用法：
    python tests/run_demo.py            # 跑全部示例（默认）
    python tests/run_demo.py --quick    # 仅跑 5 个核心示例
    python tests/run_demo.py --real-llm # 启用 .env 中的真实 LLM（需要 LLM_API_KEY）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 14 个示例（来自 tests/sample_queries.md）
SCENARIOS: list[dict] = [
    # 单图问答（P1）
    {"id": "S1", "prompt": "结构设计说明的设计号是什么？专业负责人是谁？",
     "target": "结构/结构设计说明", "pipeline_hint": "p1_drawing_qa"},
    {"id": "S2", "prompt": "这张图的图号和版本号是什么？",
     "target": "建筑/平面图", "pipeline_hint": "p1_drawing_qa"},
    {"id": "S3", "prompt": "建筑设计说明里的内容简介是什么？",
     "target": "建筑/建筑设计说明-图纸目录", "pipeline_hint": "p1_drawing_qa"},

    # 表格提取（P2）
    {"id": "S4", "prompt": "提取门窗统计表及详图中所有表格的数据",
     "target": "建筑/门窗统计表及详图", "pipeline_hint": "p2_table_extract"},
    {"id": "S5", "prompt": "结构设计说明中有哪些表格？把每个表格的内容都提取出来",
     "target": "结构/结构设计说明", "pipeline_hint": "p2_table_extract"},

    # 批量统计（P3）
    {"id": "S6", "prompt": "统计所有图纸的解析结果",
     "target": None, "pipeline_hint": "p3_batch_parse"},
    {"id": "S7", "prompt": "给排水专业有几张图纸？批量解析统计一下",
     "target": None, "pipeline_hint": "p3_batch_parse"},

    # 跨图比对（P4）
    {"id": "S8", "prompt": "检查所有图纸的设计号是否一致",
     "target": None, "pipeline_hint": "p4_cross_drawing"},
    {"id": "S9", "prompt": "目录中的图纸和实际图纸是否对应？有缺失吗？",
     "target": None, "pipeline_hint": "p4_cross_drawing"},
    {"id": "S10", "prompt": "哪些图号有冲突？请逐一比对核对",
     "target": None, "pipeline_hint": "p4_cross_drawing"},

    # 质量验证（P5）
    {"id": "S11", "prompt": "验证 MinerU 解析质量，给出完整性检查",
     "target": None, "pipeline_hint": "p5_quality_verify"},
    {"id": "S12", "prompt": "哪些图纸的 MinerU 解析结果为空或内容很少？做完整性检查",
     "target": None, "pipeline_hint": "p5_quality_verify"},

    # 复合任务（多 Pipeline 联动）— Mock LLM 仅触发首个匹配 Pipeline，
    #     真实 LLM 会自动分解为多步
    {"id": "S13", "prompt": "做完整图纸审查：检查跨图一致性",
     "target": None, "pipeline_hint": "p4_cross_drawing"},
    {"id": "S14", "prompt": "给排水专业图纸解析得怎么样？做质量验证",
     "target": None, "pipeline_hint": "p5_quality_verify"},
]

QUICK_IDS = {"S1", "S4", "S8", "S11", "S6"}


def _build_planner(use_real_llm: bool):
    """构造 TaskPlanner：默认 Mock LLM，--real-llm 时走 .env 中的 OpenAI 兼容 API。"""
    if not use_real_llm:
        # 强制 Mock：清掉 LLM_API_KEY，让 build_context 选 MockLLMGateway
        os.environ.pop("LLM_API_KEY", None)

    from src.harness.context import build_context
    from src.harness.registry import PipelineRegistry
    from src.harness.agent import TaskPlanner
    from src.pipelines import all_pipelines

    ctx = build_context(str(ROOT / "data" / "origin"))
    reg = PipelineRegistry()
    for p in all_pipelines():
        reg.register(p)

    # 配置 trace logger：让 TaskPlanner._flush_trace() 写入 logs/trace.jsonl
    # （与 server.py 的配置等价，但仅在跑 demo 时绑定，避免与服务相互影响）
    import logging
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    trace_logger = logging.getLogger("drawagent.trace")
    trace_logger.setLevel(logging.INFO)
    target = str(log_dir / "trace.jsonl")
    if not any(isinstance(h, logging.FileHandler) and h.baseFilename == target
               for h in trace_logger.handlers):
        fh = logging.FileHandler(target, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(message)s"))
        trace_logger.addHandler(fh)

    return ctx, TaskPlanner(ctx, reg)


def _run_one(planner, scenario: dict) -> dict:
    """跑一个示例，收集事件，返回执行摘要。"""
    t0 = time.time()
    events = []
    final_content = ""
    pipeline_calls: list[str] = []
    errors: list[str] = []

    for ev in planner.run(scenario["prompt"], target_drawing=scenario["target"]):
        events.append(ev["type"])
        if ev["type"] == "tool_start":
            pipeline_calls.append(ev.get("name", ""))
        elif ev["type"] == "tool_result":
            r = ev.get("result") or {}
            if not r.get("ok", True):
                errors.append(f"{ev.get('name')}: {r.get('error', '')}")
        elif ev["type"] == "done":
            final_content = ev.get("content", "")

    elapsed = time.time() - t0
    return {
        "scenario_id": scenario["id"],
        "prompt": scenario["prompt"],
        "target_drawing": scenario["target"],
        "pipeline_hint": scenario["pipeline_hint"],
        "pipelines_called": pipeline_calls,
        "events_count": len(events),
        "elapsed_sec": round(elapsed, 2),
        "errors": errors,
        "final_answer_preview": final_content[:300],
        "trace": planner.last_trace(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="DrawAgent 一键演示脚本")
    ap.add_argument("--quick", action="store_true",
                    help="只跑 5 个核心示例")
    ap.add_argument("--real-llm", action="store_true",
                    help="使用 .env 中的真实 LLM（需 LLM_API_KEY）")
    args = ap.parse_args()

    scenarios = [s for s in SCENARIOS if not args.quick or s["id"] in QUICK_IDS]
    print(f"[demo] 将运行 {len(scenarios)} 个示例 "
          f"（{'真实 LLM' if args.real_llm else 'Mock LLM'}）")

    ctx, planner = _build_planner(args.real_llm)
    print(f"[demo] 加载图纸 {len(ctx.drawings())} 张，"
          f"Pipeline {len(planner.registry.names())} 个")

    summaries = []
    for i, sc in enumerate(scenarios, 1):
        print(f"\n[{i}/{len(scenarios)}] {sc['id']}  {sc['prompt'][:50]}")
        summary = _run_one(planner, sc)
        ok = bool(summary["pipelines_called"]) and not summary["errors"]
        flag = "✓" if ok else "✗"
        print(f"      {flag}  路由 → {summary['pipelines_called'] or '(无)'}  "
              f"耗时 {summary['elapsed_sec']}s  事件 {summary['events_count']} 条")
        if summary["errors"]:
            print(f"      ⚠  {summary['errors']}")
        summaries.append(summary)

    # 汇总到 logs/demo_summary.json，trace 已由 TaskPlanner 自动写入 logs/trace.jsonl
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    summary_path = log_dir / "demo_summary.json"
    summary_path.write_text(json.dumps({
        "scenarios": summaries,
        "total": len(summaries),
        "passed": sum(1 for s in summaries
                      if s["pipelines_called"] and not s["errors"]),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    trace_path = log_dir / "trace.jsonl"
    trace_lines = (trace_path.read_text(encoding="utf-8").count("\n")
                   if trace_path.exists() else 0)

    print(f"\n[demo] 完成。")
    print(f"  执行轨迹  → {trace_path}  （{trace_lines} 条记录）")
    print(f"  汇总报告  → {summary_path}")

    passed = sum(1 for s in summaries
                 if s["pipelines_called"] and not s["errors"])
    print(f"  通过率    → {passed}/{len(summaries)}")
    return 0 if passed == len(summaries) else 1


if __name__ == "__main__":
    sys.exit(main())
