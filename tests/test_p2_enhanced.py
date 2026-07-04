"""P2 增强测试：DataFrame 结构化层 + JSON-safe data + 无 LLM 降级说明。

用真实 MinerU 解析结果（门窗统计表及详图）+ Mock LLM 验证：
- data 结构化：至少 1 个表，records 非空且含门窗关键列
- JSON-safe：NaN 已转 None，json.dumps(allow_nan=False) 不抛异常
- 无表格图纸行为不变（ok=False + "no tables"）
- 无 LLM_API_KEY 时 content 含降级说明行，确定性结果完整
"""
from __future__ import annotations

import json

import pytest

from contracts.interfaces import AnswerType

TABLE_TARGET = "建筑/门窗统计表及详图"
NO_TABLE_TARGET = "建筑/屋面排水示意图"


def _run_p2(workspace, task_request_factory, prompt: str, target: str):
    from src.pipelines.p2_table_extract.handler import TableExtract
    req = task_request_factory(prompt, target=target)
    return TableExtract().run(workspace, req)


def test_p2_data_structured(workspace, task_request_factory):
    """门窗统计表：data 至少 1 个表，records 非空且含门窗数据关键列。"""
    if workspace.find_drawing(TABLE_TARGET) is None:
        pytest.skip(f"local corpus has no target drawing: {TABLE_TARGET}")
    result = _run_p2(workspace, task_request_factory,
                     "提取门窗统计表的数据", TABLE_TARGET)

    assert result.ok, f"P2 失败：{result.error}"
    assert result.answer_type == AnswerType.TABLE
    assert result.content, "content 为空"

    assert isinstance(result.data, list), "data 应为 list"
    assert len(result.data) >= 1, "data 应至少含 1 个表"
    for entry in result.data:
        for key in ("caption", "page", "n_rows", "n_cols", "records"):
            assert key in entry, f"data 条目缺少字段 {key}"
        assert entry["n_rows"] == len(entry["records"])

    # 门窗表：关键列 + 门/窗编号数据都在
    door = next((t for t in result.data if t["caption"] == "门窗表"), None)
    assert door is not None, "data 中未找到『门窗表』"
    assert door["records"], "门窗表 records 为空"
    rec = door["records"][0]
    for col in ("类型", "设计编号", "洞口尺寸(mm)", "数量"):
        assert col in rec, f"门窗表 records 缺少关键列 {col}"
    assert isinstance(rec["数量"], int), "数量列应为原生 int"
    codes = {r.get("设计编号") for r in door["records"]}
    assert "M-1" in codes and "C-1" in codes, "门窗表应同时含门（M-1）与窗（C-1）数据"
    # rowspan 合并单元格已展开：类型列每行都有值
    assert all(r.get("类型") in ("门", "窗") for r in door["records"])


def test_p2_data_json_safe(workspace, task_request_factory):
    """data 必须 JSON-safe：NaN 已转 None，json.dumps(allow_nan=False) 不抛异常。"""
    if workspace.find_drawing(TABLE_TARGET) is None:
        pytest.skip(f"local corpus has no target drawing: {TABLE_TARGET}")
    result = _run_p2(workspace, task_request_factory,
                     "提取门窗统计表的数据", TABLE_TARGET)

    assert result.ok
    # allow_nan=False：任何残留 NaN/Inf 都会 ValueError
    json.dumps(result.data, ensure_ascii=False, allow_nan=False)

    # 性能指标表『保温性』行等级为空 → 应为 None 而非 NaN
    perf = next((t for t in result.data if "性能" in t["caption"]), None)
    assert perf is not None, "data 中未找到『门窗主要性能指标』表"
    assert any(None in r.values() for r in perf["records"]), \
        "性能指标表应含空单元格转成的 None"


def test_p2_content_has_markdown_tables(workspace, task_request_factory):
    """content 人读友好：含「### 表格 N」小节标题与 markdown 表格。"""
    if workspace.find_drawing(TABLE_TARGET) is None:
        pytest.skip(f"local corpus has no target drawing: {TABLE_TARGET}")
    result = _run_p2(workspace, task_request_factory,
                     "提取门窗统计表的数据", TABLE_TARGET)

    assert result.ok
    assert "### 表格 1" in result.content
    assert "设计编号" in result.content
    assert "|" in result.content, "content 应含 markdown 表格"


def test_p2_mock_has_llm_notice(workspace, task_request_factory):
    """无 LLM_API_KEY（Mock）时：content 含降级说明行，确定性结果照常输出。"""
    if workspace.find_drawing(TABLE_TARGET) is None:
        pytest.skip(f"local corpus has no target drawing: {TABLE_TARGET}")
    result = _run_p2(workspace, task_request_factory,
                     "提取门窗统计表的数据", TABLE_TARGET)

    assert result.ok
    assert "需配置 LLM_API_KEY" in result.content, "缺少 LLM 降级说明行"
    assert "DataFrame 结构化结果" in result.content


def test_p2_no_tables_behavior_unchanged(workspace, task_request_factory):
    """无表格图纸（屋面排水示意图）：行为不变，ok=False 且 error 含 no tables。"""
    if workspace.find_drawing(NO_TABLE_TARGET) is None:
        pytest.skip(f"local corpus has no target drawing: {NO_TABLE_TARGET}")
    result = _run_p2(workspace, task_request_factory,
                     "提取数据", NO_TABLE_TARGET)

    assert not result.ok
    assert "no tables" in result.error
