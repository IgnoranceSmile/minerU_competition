"""P4 结构化输出 + Agent plan 事件的增强测试。

两组用例：
1. P4：TaskResult.data 的结构完整性，以及 data 与 content 报告结论的一致性
   （content 说发现 N 种设计号，data["design_nos"] 长度必须是 N，以此类推）；
   无 LLM_API_KEY 时修复建议层降级说明行必须存在。
2. Agent：mock 路由下，每轮工具执行前必须出现 status=="executing" 的 plan
   事件，steps 如实列出本轮将调用的 Pipeline，且与随后的 tool_start 一致。

全部用例零密钥、零网络可跑（Mock LLM + 真实 MinerU 解析结果）。
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture(scope="module")
def p4_result(workspace):
    """跑一次 P4，供本文件多个用例共享（P4 全量比对较重，避免重复执行）。"""
    from contracts.interfaces import TaskRequest
    from src.pipelines.p4_cross_drawing.handler import CrossDrawing

    req = TaskRequest(prompt="检查所有图纸设计号是否一致", target_drawing=None,
                      region=None)
    return CrossDrawing().run(workspace, req)


# ===== P4 结构化 data =====

def test_p4_data_structure_complete(p4_result):
    """data 包含全部约定字段，且为 JSON-safe。"""
    assert p4_result.ok, f"P4 失败：{p4_result.error}"
    data = p4_result.data
    assert isinstance(data, dict), "P4 应输出结构化 data"
    for key in ("design_nos", "design_no_consistent", "missing", "conflicts",
                "atlas_refs", "ref_total", "checked_drawings"):
        assert key in data, f"data 缺少字段 {key}"
    # JSON-safe：/api/export 直接序列化
    json.dumps(data, ensure_ascii=False)


def test_p4_data_matches_content_design_no(p4_result):
    """设计号：data 与 content 结论一致。"""
    data, content = p4_result.data, p4_result.content
    nos = data["design_nos"]
    assert data["design_no_consistent"] == (len(nos) <= 1)
    if data["design_no_consistent"]:
        assert "设计号一致性" in content
    else:
        # content 说发现 N 种设计号 → data["design_nos"] 长度必须是 N
        assert f"发现 {len(nos)} 种设计号" in content
        for no in nos:
            assert no in content, f"设计号 {no} 未出现在报告中"


def test_p4_data_matches_content_missing(p4_result):
    """目录缺图：data 与 content 结论一致，条目字段完整。"""
    data, content = p4_result.data, p4_result.content
    missing = data["missing"]
    if missing:
        assert f"缺失 {len(missing)} 项" in content
        for item in missing:
            assert set(item) == {"number", "name", "source"}
            assert item["number"] in content
    else:
        assert "均能在图纸包中找到对应图签" in content


def test_p4_data_matches_content_conflicts(p4_result):
    """图号冲突：data 与 content 结论一致，每项冲突至少 2 个占用方。"""
    data, content = p4_result.data, p4_result.content
    conflicts = data["conflicts"]
    if conflicts:
        assert f"{len(conflicts)} 个图号被多张图纸占用" in content
        for c in conflicts:
            assert set(c) == {"number", "owners"}
            assert len(c["owners"]) >= 2
            assert c["number"] in content
    else:
        assert "未发现同专业图号冲突" in content


def test_p4_data_matches_content_refs(p4_result, workspace):
    """引用统计：data 与 content 结论一致，检查覆盖全部图纸。"""
    data, content = p4_result.data, p4_result.content
    assert f"共检出 **{data['ref_total']}** 条跨图纸引用关系" in content
    for ref in data["atlas_refs"]:
        assert ref in content
    assert data["checked_drawings"] == len(workspace.drawings())


def test_p4_no_llm_fallback_note(p4_result):
    """无 LLM_API_KEY 时：修复建议层降级说明行存在，规则层输出完整。"""
    content = p4_result.content
    assert "需配置 LLM_API_KEY" in content
    # 降级不影响规则层四类检查的输出
    for section in ("设计号", "缺失图纸检查", "图号冲突检查"):
        assert section in content


# ===== Agent plan 事件 =====

def test_planner_emits_executing_plan(workspace, registry):
    """每轮工具执行前必须有 status==executing 的 plan 事件，
    且 steps[0]["pipeline"] 与随后的 tool_start 名字一致。"""
    from src.harness.agent import TaskPlanner

    planner = TaskPlanner(workspace, registry)
    events = list(planner.run("检查所有图纸的设计号是否一致"))

    plans = [e for e in events if e["type"] == "plan"]
    assert plans, "缺少 plan 事件"

    # 向后兼容：所有 plan 事件都有 type/status/message 三个基础字段
    for p in plans:
        for key in ("type", "status", "message"):
            assert key in p, f"plan 事件缺少字段 {key}"

    # 开场 analyzing 事件保留
    assert plans[0]["status"] == "analyzing"

    # 存在 executing plan，steps 如实列出本轮调用
    executing = [e for e in events if e["type"] == "plan"
                 and e["status"] == "executing"]
    assert executing, "缺少 status==executing 的 plan 事件"
    plan = executing[0]
    assert plan["iteration"] >= 1
    assert plan["steps"], "executing plan 的 steps 不能为空"
    step = plan["steps"][0]
    assert "pipeline" in step and "arguments" in step

    # 该 plan 事件之后的第一个 tool_start 必须与 steps[0] 一致
    idx = events.index(plan)
    tool_starts_after = [e for e in events[idx:] if e["type"] == "tool_start"]
    assert tool_starts_after, "executing plan 后缺少 tool_start"
    assert tool_starts_after[0]["name"] == step["pipeline"]
    # mock 关键词路由：『一致』→ p4_cross_drawing
    assert step["pipeline"] == "p4_cross_drawing"


def test_planner_plan_before_tool_start(workspace, registry):
    """事件顺序：executing plan 先于其对应的 tool_start。"""
    from src.harness.agent import TaskPlanner

    planner = TaskPlanner(workspace, registry)
    events = list(planner.run("提取门窗统计表的数据",
                              target_drawing="建筑/门窗统计表及详图"))
    types = [e["type"] for e in events]
    first_exec_plan = next(i for i, e in enumerate(events)
                           if e["type"] == "plan"
                           and e.get("status") == "executing")
    first_tool_start = types.index("tool_start")
    assert first_exec_plan < first_tool_start
    assert (events[first_exec_plan]["steps"][0]["pipeline"]
            == events[first_tool_start]["name"])
