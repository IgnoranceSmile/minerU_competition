# DrawAgent API 文档

DrawAgent 后端基于 FastAPI 构建，默认监听端口 `8000`。所有接口启用 CORS，前缀为 `/api`。
**Base URL**: `http://127.0.0.1:8000`

---

## 1. GET /api/drawings — 获取图纸库清单

**响应**：`{"root": str, "count": int, "drawings": [{"id": str, "name": str, "discipline": str, "page_count": int}]}`

```bash
# 示例
curl http://127.0.0.1:8000/api/drawings
```

---

## 2. POST /api/upload — 上传图纸压缩包

上传 .zip 压缩包，服务端自动解压、索引并重建上下文。
**请求格式**：`multipart/form-data`，字段 `file`（必须以 `.zip` 结尾）。
**响应**：`{"ok": bool, "session": str, "count": int, "drawings": [...]}`
**错误码**：`400` — 非 zip 文件或压缩包损坏。

```bash
curl -F "file=@drawings.zip" http://127.0.0.1:8000/api/upload
```

---

## 3. POST /api/chat — 对话（SSE 流式）

与 DrawAgent 对话，返回 Server-Sent Events 流。**请求格式**：`application/json`。

**请求体**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `prompt` | `str` | 用户输入（必填） |
| `target_drawing` | `str \| null` | 指定图纸 id |
| `region` | `object \| null` | 点选/框选区域，结构见下方 |

**region 对象**：`{kind: "point"|"box", drawing_id: str, page: int, x/y: float, bbox: [x0,y0,x1,y1]}`

**SSE 事件类型**：

| type | 说明 | 关键字段 |
|------|------|----------|
| `reasoning` | 思考过程增量 | `delta: str` |
| `content` | 回答正文增量 | `delta: str` |
| `tool_start` | Pipeline 开始执行 | `name: str` |
| `progress` | 执行进度 | `step: int, max_steps: int, pipeline: str` |
| `tool_result` | Pipeline 返回结果 | `name: str, result: {...}` |
| `done` | 本轮结束 | `content: str` |

**tool_result.result 结构**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `answer_type` | `str` | 答案类型枚举（见下） |
| `content` | `str` | 文本 / markdown / 图片路径 / JSON |
| `evidence` | `list` | 来源定位，每项含 `drawing`、`bbox`、`note` |
| `extra_images` | `list[str]` | 标注图 URL（已转为 `/api/image?path=...`） |
| `ok` | `bool` | 是否成功 |
| `error` | `str` | 错误信息 |

**AnswerType 枚举**：`text` | `table` | `markdown_list` | `bbox_image` | `json_data` | `file_export`

**错误码**：`409` — 尚未加载图纸，需先上传。

```bash
# 简单问答
curl -N -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt": "设计号是什么？"}'

# 带图纸指定和框选区域
curl -N -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt":"这个区域是什么？","target_drawing":"建筑/平面图",\
"region":{"kind":"box","drawing_id":"建筑/平面图","page":0,"bbox":[100,200,500,600]}}'
```

---

## 4. GET /api/drawing_page — 获取图纸页渲染图

**查询参数**：`id`（图纸 id，必填）、`page`（页码从 0 开始，默认 0）。
**响应**：PNG 图片文件。
**错误码**：`404` — 图纸或页码不存在；`409` — 尚未加载图纸。

```bash
curl "http://127.0.0.1:8000/api/drawing_page?id=建筑/平面图&page=0" -o page.png
```

---

## 5. GET /api/image — 获取缓存图片

获取 bbox 标注渲染图等缓存文件。仅允许访问 `CACHE_DIR` 和 uploads 目录。
**查询参数**：`path`（图片绝对路径，必填）。
**错误码**：`403` — 路径非法；`404` — 文件不存在。

```bash
curl "http://127.0.0.1:8000/api/image?path=/tmp/drawagent_cache/bbox_001.png" -o bbox.png
```

---

## 6. GET /api/trace — 获取最近执行轨迹

返回最近一次 `/api/chat` 对话的工具调用轨迹，包含每步的 Pipeline、入参、耗时、结果预览。用于执行过程的可追溯与审计。

**响应**：`{"steps": [{"step": int, "pipeline": str, "arguments": {}, "ok": bool, "elapsed_ms": int, "content_preview": str, "error": str}], "total": int, "timestamp": str}`

```bash
curl http://127.0.0.1:8000/api/trace
```

---

## 7. POST /api/export — 结构化导出

同步执行一次任务，返回结构化结果（非流式）。适用于批量处理和程序化调用。

**请求体**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `prompt` | `str` | 用户输入（必填） |
| `target_drawing` | `str \| null` | 指定图纸 id |
| `format` | `str` | 输出格式：`json`（默认）或 `csv` |

**JSON 响应**：`{"format": "json", "results": [{"pipeline": str, "content": str, "answer_type": str, "data": dict|list|null}], "prompt": str, "timestamp": str}`

`data` 为各 Pipeline 的结构化结果（P2：表格 records；P3：统计与异常清单；P4：四类检查 findings；P5：四维评分明细）。CSV 格式会将 `data` 中的记录列表（如 P2 的表格行、P5 的逐图评分）展开为真实数据行；无结构化数据时退回文本导出。

```bash
# JSON 格式导出
curl -X POST http://127.0.0.1:8000/api/export \
  -H "Content-Type: application/json" \
  -d '{"prompt": "提取门窗统计表的数据", "format": "json"}'

# CSV 格式导出
curl -X POST http://127.0.0.1:8000/api/export \
  -H "Content-Type: application/json" \
  -d '{"prompt": "检查所有图纸的设计号是否一致", "format": "csv"}'
```

---

## Pipeline 路由表

Agent 根据用户意图自动路由到以下 Pipeline：

| 名称 | 说明 |
|------|------|
| `p1_drawing_qa` | 单张图纸内容问答（图签、标注、文字提取） |
| `p2_table_extract` | 表格精准提取（门窗表、材料表、构件统计表） |
| `p3_batch_parse` | 批量解析统计与异常检测（支持按专业筛选） |
| `p4_cross_drawing` | 跨图纸比对（图号/设计号一致性校验） |
| `p5_quality_verify` | 解析质量四维加权评分（文字/版面/图签/表格，0-100） |

---

## 通用说明

- 图纸 id 格式为 `{专业}/{图名}`，如 `结构/结构设计说明`，专业包括：建筑、结构、给排水、电气。
- SSE 流需用 `EventSource` 或 curl `-N` 参数接收。
- 服务启动时会尝试加载 `data/origin` 目录下的本地图纸集。
- Agent 最多执行 12 轮工具调用（`max_steps`，对应 `src/harness/agent.py` 中的 `MAX_ITERS`），超出后返回提示。
