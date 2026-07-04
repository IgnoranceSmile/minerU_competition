---
name: p3_batch_parse
description: 批量解析结果统计与异常检测。汇总既有 MinerU 解析结果的
             版面块/表格/文字量，按专业分组，检出异常图纸；配置 LLM 后追加语义分析。
input: { prompt: string, target_drawing?: string, discipline?: 建筑|结构|给排水|电气 }
output_type: markdown_list
depends_on: [MinerU解析结果（data/mineru）, LLM网关（可选）]
---
## 触发
- "统计所有图纸的解析结果" / "解析结果总览"
- "统计结构专业图纸" / 带 discipline 参数的调用
- "哪些图纸解析结果为空或内容很少"

## 流程
确定筛选范围 → 逐图读取既有解析结果并统计 → 确定性异常检测 →
（配置 LLM 时）语义分析 → 输出报告 + data 结构化结果

## 按专业筛选（三级优先）
1. 显式 `discipline` 参数（LLM function calling 传入，经 registry 落到 req.extra）
2. 从 req.prompt 识别专业关键词（建筑/结构/给排水/电气；命中多个专业时视为对比场景，不筛选）
3. 都未命中则全量统计

筛选生效时报告标题注明筛选条件（如「筛选：结构 专业」）。

## 异常检测规则（确定性）
- 空内容：Markdown 去空白后 < 50 字
- 零版面块：解析版面块数为 0
- 无页图：无页面渲染图
- 文字量偏低：低于本专业中位数 × 30%（该专业 ≥ 3 张可比图纸时才判定；已判空内容的不重复计）
- 解析结果读取失败：读取解析缓存抛异常

## 语义分析层（可选）
`llm_ready()` 为真时，把各专业统计与异常清单交给 `llm_analyze`
（角色：工程图纸解析质量分析师），产出各专业解析特征对比、异常图纸归因假设、
对下游任务（图纸问答/表格提取/跨图审查）的可用性提示，拼入「语义分析」小节。
未配置 LLM_API_KEY 时输出一行说明；统计与异常检测结果不受影响，始终完整。

## data 字段（JSON-safe，供 /api/export 消费）
`{ filter, totals, by_discipline, per_drawing, anomalies }`

## 边界
本流水线不触发 MinerU 解析。解析由 adapters/mineru_real.py 的
parse_folder（按专业分组并行调用 MinerU）预先完成，本流水线读取并统计其结果。
