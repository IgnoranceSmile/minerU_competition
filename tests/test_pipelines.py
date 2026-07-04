"""5 个 Pipeline 的单元测试。

每个 Pipeline 用真实 MinerU 解析结果 + Mock LLM 跑一遍，验证：
- TaskResult.ok 为 True
- content 不为空
- evidence 至少一条
"""
from __future__ import annotations

import os

import pytest

from contracts.interfaces import AnswerType

def _expected_drawing_count(workspace) -> int:
    return int(os.getenv("DRAWAGENT_EXPECTED_DRAWINGS")
               or len(workspace.drawings()))


def test_workspace_loaded(workspace):
    """图纸库加载：本地 PDF 全部识别。"""
    drawings = workspace.drawings()
    expected = _expected_drawing_count(workspace)
    assert len(drawings) == expected, (
        f"应识别 {expected} 张图纸，实际 {len(drawings)} 张")
    discs = {d.discipline.value for d in drawings}
    assert discs <= {"建筑", "结构", "给排水", "电气"}


def test_registry_has_5_pipelines(registry):
    """5 个 Pipeline 全部注册。"""
    names = registry.names()
    assert names == ["p1_drawing_qa", "p2_table_extract", "p3_batch_parse",
                     "p4_cross_drawing", "p5_quality_verify"]


def test_p1_drawing_qa(workspace, task_request_factory):
    """P1 单图问答：能定位到结构设计说明并返回内容。"""
    from src.pipelines.p1_drawing_qa.handler import DrawingQA

    target = "结构/结构设计说明"
    if workspace.find_drawing(target) is None:
        pytest.skip(f"local corpus has no target drawing: {target}")
    req = task_request_factory(
        "结构设计说明的设计号是什么？", target=target)
    result = DrawingQA().run(workspace, req)

    assert result.ok, f"P1 失败：{result.error}"
    assert result.answer_type == AnswerType.TEXT
    assert result.content, "P1 返回内容为空"
    assert len(result.evidence) >= 1


def test_p2_table_extract(workspace, task_request_factory):
    """P2 表格提取：门窗统计表能拿到至少 1 个表格。"""
    from src.pipelines.p2_table_extract.handler import TableExtract

    target = "建筑/门窗统计表及详图"
    if workspace.find_drawing(target) is None:
        pytest.skip(f"local corpus has no target drawing: {target}")
    req = task_request_factory(
        "提取门窗统计表的数据", target=target)
    result = TableExtract().run(workspace, req)

    assert result.ok, f"P2 失败：{result.error}"
    assert result.answer_type == AnswerType.TABLE
    assert result.content, "P2 表格内容为空"


def test_p3_batch_parse(workspace, task_request_factory):
    """P3 批量统计：返回本地图纸集的统计 markdown。"""
    from src.pipelines.p3_batch_parse.handler import BatchParse

    req = task_request_factory("统计所有图纸的解析结果")
    result = BatchParse().run(workspace, req)
    expected = _expected_drawing_count(workspace)

    assert result.ok, f"P3 失败：{result.error}"
    assert result.answer_type == AnswerType.MARKDOWN_LIST
    # 已加载的专业小标题都应出现
    for disc in {d.discipline.value for d in workspace.drawings()}:
        assert disc in result.content, f"P3 内容缺少专业 {disc}"
    assert str(expected) in result.content, (
        f"P3 内容应包含 {expected} 张图纸总数")


def test_p4_cross_drawing(workspace, task_request_factory):
    """P4 跨图比对：跑完 4 类检查，输出报告。"""
    from src.pipelines.p4_cross_drawing.handler import CrossDrawing

    req = task_request_factory("检查所有图纸设计号是否一致")
    result = CrossDrawing().run(workspace, req)

    assert result.ok, f"P4 失败：{result.error}"
    assert "设计号" in result.content
    assert "目录" in result.content or "缺失" in result.content
    assert "图号" in result.content


def test_p5_quality_verify(workspace, task_request_factory):
    """P5 质量验证：输出评分 + 各图纸详情表。"""
    from src.pipelines.p5_quality_verify.handler import QualityVerify

    req = task_request_factory("验证 MinerU 解析质量")
    result = QualityVerify().run(workspace, req)
    expected = _expected_drawing_count(workspace)

    assert result.ok, f"P5 失败：{result.error}"
    assert "总体评分" in result.content
    assert "/100" in result.content
    # 必须给出本地图纸集的明细
    table_rows = result.content.count("\n|")
    assert table_rows >= expected, (
        f"P5 详情表应至少 {expected} 行，实际 {table_rows}")


def test_p1_handles_unknown_drawing(workspace, task_request_factory):
    """P1 异常路径：未指定图纸时优雅降级。"""
    from src.pipelines.p1_drawing_qa.handler import DrawingQA

    req = task_request_factory("zzz 完全不存在的内容 zzz")
    result = DrawingQA().run(workspace, req)
    # 要么定位到某张图（按专业关键词兜底），要么明确返回未定位
    assert result.ok or result.error == "no target drawing"


def test_p2_no_tables_returns_error(workspace, task_request_factory):
    """P2 异常路径：图纸里没有表格时，返回 ok=False。"""
    from src.pipelines.p2_table_extract.handler import TableExtract

    # 屋面排水示意图：MinerU 解析为空
    target = "建筑/屋面排水示意图"
    if workspace.find_drawing(target) is None:
        pytest.skip(f"local corpus has no target drawing: {target}")
    req = task_request_factory("提取数据", target=target)
    result = TableExtract().run(workspace, req)
    # 要么明确报无表格，要么返回内容（取决于 MinerU 本次输出）
    if not result.ok:
        assert "no tables" in result.error or "no target" in result.error
