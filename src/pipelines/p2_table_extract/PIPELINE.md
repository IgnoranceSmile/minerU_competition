---
name: p2_table_extract
description: 表格精准提取。MinerU 表格 HTML → pandas DataFrame（确定性结构化）
             → 可选大模型按问题清洗，输出 markdown 预览 + JSON-safe records
             （TaskResult.data，可直接导出 JSON/CSV）。
input: { prompt: string, target_drawing?: string }
output_type: table
depends_on: [MinerU解析, pandas/lxml/tabulate, LLM网关（可选）]
---
## 触发
- "提取结构设计说明中所有表格"
- "门窗统计表的数据" / "材料表内容"
- "导出构件统计为CSV"

## 流程
1. 定位图纸，取 MinerU 解析结果中的表格块（HTML + 页码 + caption + bbox）
2. **DataFrame 转换（确定性，无 LLM 依赖）**：pandas.read_html（lxml 后端）
   逐表转 DataFrame；清洗：去全空行/全空列、列名与单元格 strip、
   多级表头扁平化、空名/重名列补名去重；rowspan/colspan 合并单元格由
   pandas 自动展开为逐行记录
3. **content**：每表「### 表格 N（第 X 页 · caption）」+ df.to_markdown 预览
4. **data**：`[{caption, page, n_rows, n_cols, records}]`，records 为
   JSON-safe dict 列表（NaN→None，数字保持原生 int/float），
   `json.dumps(..., allow_nan=False)` 可直接序列化
5. **大模型清洗层（可选）**：配置 LLM_API_KEY 后，以「表格数据提取专家」
   角色按用户问题从全部 DataFrame markdown 中提取，拼进
   「大模型提取结果」小节
6. 输出来源定位（evidence）+ 表格区域高亮图（extra_images）

## 降级行为
- 未配置 LLM_API_KEY：跳过大模型清洗层，content 输出一行说明，
  DataFrame 结构化结果完整可用（离线可复现）
- 单表 read_html 转换失败：该表降级为 html_table_to_md 文本并标注
  「结构化转换失败，降级为文本」，不进 data，其余表不受影响
- 图纸无表格：ok=False，error="no tables found"
