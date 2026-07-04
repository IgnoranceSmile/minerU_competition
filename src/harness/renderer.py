"""可视化渲染器。

- highlight_blocks：在页图上叠加 bbox + 标签
- html_table_to_md：MinerU 表格 HTML → Markdown
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from contracts.interfaces import Block  # noqa: E402
from config import CACHE_DIR  # noqa: E402

_COLORS = ["#e6194b", "#3cb44b", "#4363d8", "#f58231",
           "#911eb4", "#46f0f0", "#f032e6"]


def highlight_blocks(image_path: str, blocks: list[Block],
                     tag: str = "hl") -> str:
    """在页图上高亮内容块，返回输出图路径。"""
    from PIL import Image, ImageDraw

    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    labels = sorted({b.type.value for b in blocks})
    color_of = {lb: _COLORS[i % len(_COLORS)] for i, lb in enumerate(labels)}

    for b in blocks:
        bb = b.bbox
        c = color_of[b.type.value]
        draw.rectangle([bb.x0, bb.y0, bb.x1, bb.y1], outline=c, width=3)
        label_text = b.type.value
        if b.text:
            label_text += f" {b.text[:20]}"
        draw.text((bb.x0, max(0, bb.y0 - 12)), label_text, fill=c)

    out = CACHE_DIR / f"{Path(image_path).stem}_{tag}.png"
    img.save(out)
    return str(out)


def blocks_to_table(blocks: list[Block]) -> str:
    """内容块统计 → Markdown 表格（按类型计数）。"""
    cnt = Counter(b.type.value for b in blocks)
    lines = ["| 块类型 | 数量 |", "|---|---|"]
    for label, n in sorted(cnt.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {label} | {n} |")
    lines.append(f"| **合计** | **{len(blocks)}** |")
    return "\n".join(lines)


def html_table_to_md(html: str) -> str:
    """MinerU 表格 HTML → Markdown 表格。失败时回退原 HTML。"""
    try:
        from io import StringIO

        import pandas as pd
        dfs = pd.read_html(StringIO(html))
        return "\n\n".join(df.to_markdown(index=False) for df in dfs)
    except Exception:
        return html
