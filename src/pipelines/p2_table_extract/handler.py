"""P2 表格精准提取。

两层架构：
1. 确定性层（无 LLM 依赖）：MinerU 表格 HTML → pandas.read_html 转 DataFrame，
   清洗（去全空行/全空列、列名与单元格 strip、多级表头扁平化、重名列去重；
   rowspan/colspan 合并单元格由 pandas 自动展开），输出：
   - content：每表「### 表格 N（第 X 页 · caption）」+ df.to_markdown 预览
   - data：JSON-safe 结构化记录（NaN→None、numpy 标量→原生类型），
     形如 [{"caption","page","n_rows","n_cols","records"}]，供 /api/export 直接消费
2. 大模型清洗层（可选）：配置 LLM_API_KEY 后，llm_analyze 按用户问题从
   全部 DataFrame markdown 中精准提取，拼进「大模型提取结果」小节；
   未配置时输出一行说明，确定性层结果不受影响。

单表转换失败时降级为 html_table_to_md 文本并标注，其余表不受影响。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from contracts.interfaces import AnswerType, Source, TaskResult  # noqa: E402
from src.pipelines.base import (DEFAULT_SCHEMA, resolve_drawing,  # noqa: E402
                                llm_analyze, llm_ready)
from src.pipelines._common import extract_tables_from_parsed  # noqa: E402
from src.harness.renderer import highlight_blocks, html_table_to_md  # noqa: E402

_LLM_ROLE = ("你是表格数据提取专家。从工程图纸表格中按用户问题精准提取，"
             "保持数字精度，标注来源表格，合并单元格需说明")


def _flatten_columns(df) -> None:
    """列名清洗（原地修改）：多级表头扁平化、strip、空名/Unnamed 补名、重名去重。"""
    import pandas as pd

    if isinstance(df.columns, pd.MultiIndex):
        flat = []
        for tup in df.columns:
            parts, seen_parts = [], set()
            for p in tup:
                p = str(p).strip()
                if p and not p.startswith("Unnamed") and p not in seen_parts:
                    parts.append(p)
                    seen_parts.add(p)
            flat.append(" ".join(parts))
        df.columns = flat

    cols, seen = [], {}
    for i, c in enumerate(df.columns):
        c = str(c).strip()
        if not c or c.startswith("Unnamed"):
            c = f"列{i + 1}"
        if c in seen:
            seen[c] += 1
            c = f"{c}_{seen[c]}"
        else:
            seen[c] = 1
        cols.append(c)
    df.columns = cols


def _html_to_df(html: str):
    """单个表格块 HTML → 清洗后的 DataFrame。失败时抛异常，由调用方降级。"""
    from io import StringIO

    import pandas as pd

    dfs = pd.read_html(StringIO(html), header=0, flavor="lxml")
    if not dfs:
        raise ValueError("read_html 未解析出表格")
    df = dfs[0]
    _flatten_columns(df)
    # 单元格 strip（DataFrame.map 为 pandas>=2.1，旧版回退 applymap）
    _strip = (lambda v: v.strip() if isinstance(v, str) else v)
    df = getattr(df, "map", getattr(df, "applymap", None))(_strip)
    # 空串视为缺失，去全空行 / 全空列
    df = df.replace("", pd.NA)
    df = df.dropna(axis=0, how="all").dropna(axis=1, how="all")
    return df.reset_index(drop=True)


def _json_safe(v) -> Any:
    """单元格值 → JSON-safe：NaN/NA→None，numpy 标量→原生 int/float，字符串原样。"""
    import pandas as pd

    try:
        if v is None or pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "item"):          # numpy 标量 → Python 原生类型
        v = v.item()
    return v


def _df_to_records(df) -> list[dict]:
    """DataFrame → JSON-safe records（json.dumps(..., allow_nan=False) 可直接序列化）。"""
    return [{k: _json_safe(v) for k, v in row.items()}
            for row in df.to_dict("records")]


class TableExtract:
    name = "p2_table_extract"
    description = ("表格精准提取：从图纸中提取表格数据（门窗统计表、材料表、构件表等），"
                   "输出 DataFrame 结构化结果（JSON-safe records，可导出 JSON/CSV）。"
                   "支持密集数字表格、合并单元格展开、单表失败降级。"
                   "涉及『表格/统计表/门窗表/材料表/数据提取』时调用。")
    input_schema = DEFAULT_SCHEMA

    def run(self, ctx, req) -> TaskResult:
        d = resolve_drawing(ctx, req)
        if d is None:
            return TaskResult(
                AnswerType.TEXT,
                "未能定位目标图纸，请指定图纸名称。",
                ok=False, error="no target drawing")

        pd_ = ctx.parsed(d.id)
        tables = extract_tables_from_parsed(pd_)

        if not tables:
            return TaskResult(
                AnswerType.TEXT,
                f"图纸『{d.name}』中未检测到表格。MinerU 解析可能未识别到表格区域。",
                ok=False, error="no tables found")

        # ── 确定性层：HTML → DataFrame → markdown 预览 + JSON-safe data ──
        sections: list[str] = []
        data: list[dict] = []
        for i, t in enumerate(tables):
            title = f"### 表格 {i + 1}（第 {t['page'] + 1} 页 · {t['caption']}）"
            try:
                df = _html_to_df(t["html"])
                md = df.fillna("").to_markdown(index=False)
                data.append({
                    "caption": t["caption"],
                    "page": t["page"],           # MinerU 页索引（0 起）
                    "n_rows": len(df),
                    "n_cols": len(df.columns),
                    "records": _df_to_records(df),
                })
            except Exception:
                md = ("（结构化转换失败，降级为文本）\n\n"
                      + (html_table_to_md(t["html"]) if t["html"]
                         else "（表格解析失败）"))
            sections.append(f"{title}\n\n{md}")
        body = "\n\n".join(sections)

        # ── 大模型清洗层（可选）：按用户问题从 DataFrame markdown 中提取 ──
        if llm_ready():
            try:
                answer = llm_analyze(ctx, _LLM_ROLE, req.prompt, body)
                content = (f"## 大模型提取结果\n\n{answer}\n\n"
                           f"## DataFrame 结构化结果\n\n{body}")
            except Exception as e:
                content = (f"（大模型清洗调用失败：{e}；"
                           f"以下 DataFrame 结构化结果完整可用）\n\n"
                           f"## DataFrame 结构化结果\n\n{body}")
        else:
            content = ("（大模型清洗层需配置 LLM_API_KEY 后启用；"
                       "以下 DataFrame 结构化结果完整可用）\n\n"
                       f"## DataFrame 结构化结果\n\n{body}")

        # 生成高亮图
        table_blocks = pd_.tables()
        extra_images = []
        if table_blocks and pd_.page_images:
            for page_img in pd_.page_images[:1]:
                try:
                    img = highlight_blocks(page_img, table_blocks, "tables")
                    extra_images.append(img)
                except Exception:
                    pass

        return TaskResult(
            AnswerType.TABLE, content,
            evidence=[Source(drawing=d.id,
                             note=f"表格提取 · {len(tables)} 个表格"
                                  f"（结构化 {len(data)} 个）")],
            extra_images=extra_images,
            data=data)
