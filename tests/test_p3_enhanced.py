"""P3 增强能力测试：专业筛选、异常检测、data 结构化输出。

与 test_pipelines.py 一样零密钥可跑：真实 MinerU 解析结果 + Mock LLM。
"""
from __future__ import annotations

import json
import os

from contracts.interfaces import AnswerType

def _expected_drawing_count(workspace) -> int:
    return int(os.getenv("DRAWAGENT_EXPECTED_DRAWINGS")
               or len(workspace.drawings()))


def _run_p3(workspace, task_request_factory, prompt, **extra):
    from src.pipelines.p3_batch_parse.handler import BatchParse
    req = task_request_factory(prompt, **extra)
    return BatchParse().run(workspace, req)


def test_p3_discipline_param_filter(workspace, task_request_factory):
    """显式 discipline 参数筛选：只返回目标专业。"""
    expected = sum(1 for d in workspace.drawings()
                   if d.discipline.value == "结构")
    if expected == 0:
        import pytest
        pytest.skip("local corpus has no structure drawings")
    result = _run_p3(workspace, task_request_factory,
                     "统计图纸的解析情况", discipline="结构")

    assert result.ok, f"P3 失败：{result.error}"
    assert result.answer_type == AnswerType.MARKDOWN_LIST
    assert "筛选：结构" in result.content, "筛选生效时标题应注明筛选条件"
    assert result.data["filter"] == {"discipline": "结构", "source": "param"}
    assert result.data["totals"]["drawings"] == expected
    assert set(result.data["by_discipline"]) == {"结构"}
    # 其余专业的分组小标题不应出现
    for disc in ("建筑", "给排水", "电气"):
        assert f"### {disc}" not in result.content


def test_p3_prompt_keyword_filter(workspace, task_request_factory):
    """无显式参数时，从 prompt 识别专业关键词。"""
    expected = sum(1 for d in workspace.drawings()
                   if d.discipline.value == "给排水")
    if expected == 0:
        import pytest
        pytest.skip("local corpus has no plumbing drawings")
    result = _run_p3(workspace, task_request_factory,
                     "统计给排水专业图纸的解析结果")

    assert result.ok, f"P3 失败：{result.error}"
    assert result.data["filter"] == {"discipline": "给排水", "source": "prompt"}
    assert result.data["totals"]["drawings"] == expected
    assert set(result.data["by_discipline"]) == {"给排水"}


def test_p3_no_filter_keeps_full_scan(workspace, task_request_factory):
    """无参数、prompt 无专业关键词时全量统计。"""
    result = _run_p3(workspace, task_request_factory, "统计所有图纸的解析结果")
    expected = _expected_drawing_count(workspace)

    assert result.ok
    assert result.data["filter"] == {"discipline": None, "source": "none"}
    assert result.data["totals"]["drawings"] == expected
    assert set(result.data["by_discipline"]) == {
        d.discipline.value for d in workspace.drawings()}


def test_p3_anomalies_detected(workspace, task_request_factory):
    """异常检测：异常清单结构正确。"""
    result = _run_p3(workspace, task_request_factory, "统计所有图纸的解析结果")
    expected = _expected_drawing_count(workspace)

    anomalies = result.data["anomalies"]
    assert isinstance(anomalies, list), "异常清单应为 list"
    for a in anomalies:
        assert {"id", "name", "discipline", "reasons",
                "blocks", "chars", "pages"} <= set(a)
        assert isinstance(a["reasons"], list) and a["reasons"]
    # 报告正文包含异常小节
    assert "异常图纸检测" in result.content


def test_p3_data_completeness(workspace, task_request_factory):
    """data 字段完整且 JSON-safe，answer_type 保持 MARKDOWN_LIST。"""
    result = _run_p3(workspace, task_request_factory, "统计所有图纸的解析结果")
    expected = _expected_drawing_count(workspace)

    assert result.answer_type == AnswerType.MARKDOWN_LIST
    data = result.data
    assert set(data) == {"filter", "totals", "by_discipline",
                         "per_drawing", "anomalies"}
    assert set(data["totals"]) == {"drawings", "blocks", "tables", "chars"}
    assert len(data["per_drawing"]) == expected
    for r in data["per_drawing"]:
        assert {"id", "name", "discipline", "blocks", "tables",
                "chars", "pages", "ok"} <= set(r)
    json.dumps(data, ensure_ascii=False)  # JSON-safe，供 /api/export 消费


def test_p3_no_llm_key_notice(workspace, task_request_factory):
    """无 LLM_API_KEY 时：统计层完整输出 + 语义分析层给出说明行。"""
    result = _run_p3(workspace, task_request_factory, "统计所有图纸的解析结果")
    expected = _expected_drawing_count(workspace)

    assert result.ok
    assert "语义分析层需配置" in result.content
    # 规则/统计层不受影响
    assert str(expected) in result.content
    assert "总版面块" in result.content
