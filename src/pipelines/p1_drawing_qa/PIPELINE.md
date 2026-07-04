---
name: p1_drawing_qa
description: 单张图纸内容问答。回答关于图纸的事实性问题（层高、专业负责人、
             设计号、图号、出图日期、材料等）。答案来自 MinerU 解析结果。
input: { prompt: string, target_drawing?: string }
output_type: text
depends_on: [MinerU解析, LLM网关]
---
## 触发
- "结构专业负责人是谁" / "这张图层高多少" / "电算程序是什么"
- 用户指定某张图纸后提问

## 不触发
- 跨多张图比对 → p4_cross_drawing
- 批量解析需求 → p3_batch_parse

## 流程
定位目标图纸（resolve_drawing）→ 取 MinerU 解析 markdown → LLM QA → 文字答案 + 来源定位
