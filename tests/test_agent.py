"""TaskPlanner 集成测试：工具调用循环 / trace 写出 / 异常恢复。"""
from __future__ import annotations


def _run(planner, prompt: str, target: str | None = None) -> list[dict]:
    """收集 planner.run 的所有事件。"""
    return list(planner.run(prompt, target_drawing=target))


def test_planner_tool_call_loop(workspace, registry):
    """对话能完整跑通 LLM → 工具调用 → 结果回写 → done 的循环。"""
    from src.harness.agent import TaskPlanner

    planner = TaskPlanner(workspace, registry)
    events = _run(planner, "提取门窗统计表的数据", target="建筑/门窗统计表及详图")

    types = [e["type"] for e in events]
    assert "tool_start" in types, "缺少 tool_start 事件"
    assert "tool_result" in types, "缺少 tool_result 事件"
    assert types[-1] == "done", "最后事件必须是 done"

    # mock LLM 关键词路由 → p2_table_extract
    tool_starts = [e for e in events if e["type"] == "tool_start"]
    assert tool_starts[0]["name"] == "p2_table_extract"


def test_planner_trace_persisted(workspace, registry):
    """planner.last_trace() 正确记录每一步。"""
    from src.harness.agent import TaskPlanner

    planner = TaskPlanner(workspace, registry)
    _run(planner, "验证 MinerU 解析质量")
    trace = planner.last_trace()

    assert len(trace) >= 1
    step = trace[0]
    assert step["pipeline"] == "p5_quality_verify"
    assert step["ok"] is True
    assert "elapsed_ms" in step
    assert step["elapsed_ms"] >= 0


def test_planner_routes_to_p3(workspace, registry):
    """关键词『统计/全部』路由到 P3。"""
    from src.harness.agent import TaskPlanner

    planner = TaskPlanner(workspace, registry)
    events = _run(planner, "统计所有图纸的解析数据")
    tool_starts = [e for e in events if e["type"] == "tool_start"]
    assert tool_starts[0]["name"] == "p3_batch_parse"


def test_planner_routes_to_p4(workspace, registry):
    """关键词『一致/比对/冲突』路由到 P4。"""
    from src.harness.agent import TaskPlanner

    planner = TaskPlanner(workspace, registry)
    events = _run(planner, "检查所有图纸的设计号是否一致")
    tool_starts = [e for e in events if e["type"] == "tool_start"]
    assert tool_starts[0]["name"] == "p4_cross_drawing"
