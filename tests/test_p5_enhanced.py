"""P5 四维打分的增强测试。

覆盖：打分纯函数的公式正确性（含无表格图纸的权重摊派）、
四维分值域 [0, 100]、data 结构化完整性、逐图评分、
无 LLM_API_KEY 时的降级说明行。全部用 Mock LLM，零密钥可跑。
"""
from __future__ import annotations

import os

import pytest

from contracts.interfaces import AnswerType
from src.pipelines.p5_quality_verify.handler import (
    TITLE_BLOCK_FIELD_COUNT, WEIGHTS, QualityVerify,
    score_drawing, score_layout, score_table, score_text, score_title_block,
)

def _expected_drawing_count(per_drawing) -> int:
    return int(os.getenv("DRAWAGENT_EXPECTED_DRAWINGS")
               or len(per_drawing))


# ===== 打分纯函数：公式手算断言 =====

def test_score_text_grades():
    """文字维分级：空 0 分，千字级 80 分档，万字级 100 分档。"""
    assert score_text(0) == 0.0
    assert score_text(100) == 30.0
    assert score_text(500) == 60.0
    assert score_text(1500) == 80.0
    assert score_text(5000) == 90.0
    assert score_text(12000) == 100.0


def test_score_layout_composition():
    """版面维：块数分级 70% + 页图存在 30%。"""
    assert score_layout(0, True) == 30.0          # 0*0.7 + 30
    assert score_layout(1, True) == 72.0          # 60*0.7 + 30
    assert score_layout(5, True) == 86.0          # 80*0.7 + 30
    assert score_layout(20, True) == 100.0        # 100*0.7 + 30
    assert score_layout(20, False) == 70.0        # 页图缺失扣 30


def test_score_title_block_ratio():
    """图签维：字段数 / 13 × 100，超出 13 封顶。"""
    assert score_title_block(0) == 0.0
    assert score_title_block(13) == 100.0
    assert abs(score_title_block(11) - 11 / 13 * 100) < 1e-9
    assert score_title_block(20) == 100.0


def test_score_table_na_when_no_tables():
    """表格维：无表格记 N/A（None），有表格按可转换占比。"""
    assert score_table(0, 0) is None
    assert score_table(0, 1) == 0.0
    assert score_table(2, 3) == pytest.approx(2 / 3 * 100)
    assert score_table(3, 3) == 100.0


def test_score_drawing_weighted_total():
    """单图总分 = 四维加权，按 WEIGHTS 手算核对。"""
    detail = {"chars": 10500, "blocks": 119, "has_pages": True,
              "title_block_fields": 11, "tables": 1, "ok_tables": 1}
    s = score_drawing(detail)
    expected = (100 * WEIGHTS["text"] + 100 * WEIGHTS["layout"]
                + 11 / 13 * 100 * WEIGHTS["title_block"]
                + 100 * WEIGHTS["table"])
    assert s["total"] == round(expected, 1)  # = 95.4


def test_score_drawing_no_table_weight_redistribution():
    """无表格图纸：表格维 N/A，其权重按比例摊给其余三维。

    全空图纸（仅页渲染图存在）：文字 0、版面 30、图签 0、表格 N/A。
    摊派后版面权重 = 0.2 / 0.8 = 0.25，总分 = 30 × 0.25 = 7.5。
    """
    detail = {"chars": 0, "blocks": 0, "has_pages": True,
              "title_block_fields": 0, "tables": 0, "ok_tables": 0}
    s = score_drawing(detail)
    assert s["table"] is None
    assert s["total"] == 7.5


# ===== 整链路：真实 MinerU 解析结果 + Mock LLM =====

@pytest.fixture(scope="module")
def p5_result(workspace):
    """跑一遍 P5，供本文件各断言复用（module 级缓存，只跑一次）。"""
    from contracts.interfaces import TaskRequest
    req = TaskRequest(prompt="验证 MinerU 解析质量")
    return QualityVerify().run(workspace, req)


def test_p5_data_completeness(p5_result):
    """data 结构化：overall/weights/success_rate/per_drawing/issues 齐全。"""
    assert p5_result.ok
    assert p5_result.answer_type == AnswerType.MARKDOWN_LIST
    data = p5_result.data
    assert data is not None
    assert set(data) >= {"overall_score", "weights", "success_rate",
                         "per_drawing", "issues"}
    assert data["weights"] == WEIGHTS
    assert 0 <= data["overall_score"] <= 100
    assert 0 <= data["success_rate"] <= 100
    assert set(data["issues"]) == {"empty_content", "weak_title_block",
                                   "bad_tables"}


def test_p5_per_drawing_and_score_ranges(p5_result):
    """逐图评分，四维分与总分都在 [0, 100]（表格维允许 N/A）。"""
    per = p5_result.data["per_drawing"]
    assert len(per) == _expected_drawing_count(per)
    for p in per:
        assert p["name"] and p["discipline"]
        s = p["scores"]
        assert set(s) == {"text", "layout", "title_block", "table"}
        for dim in ("text", "layout", "title_block"):
            assert 0 <= s[dim] <= 100, f"{p['name']} {dim} 分越界：{s[dim]}"
        assert s["table"] is None or 0 <= s["table"] <= 100
        assert 0 <= p["total"] <= 100


def test_p5_overall_is_mean_of_totals(p5_result):
    """全集总分 = 各图单图总分的算术平均。"""
    per = p5_result.data["per_drawing"]
    expected = round(sum(p["total"] for p in per) / len(per), 1)
    assert p5_result.data["overall_score"] == expected


def test_p5_content_format(p5_result):
    """content 保留既有格式约定：总体评分 + /100 + 详情表。"""
    content = p5_result.content
    expected = _expected_drawing_count(p5_result.data["per_drawing"])
    assert "总体评分" in content
    assert "/100" in content
    assert content.count("\n|") >= expected


def test_p5_mock_has_llm_fallback_line(p5_result):
    """无 LLM_API_KEY 时：输出降级说明行，且评分层完整（不因缺密钥缩水）。"""
    assert "需配置 LLM_API_KEY" in p5_result.content
    assert "以上评分结果完整可用" in p5_result.content
