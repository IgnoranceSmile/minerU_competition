"""图纸库索引器。

扫描输入文件夹 → 识别专业分类（按子文件夹名）→ 建 Drawing manifest。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from contracts.interfaces import Discipline, Drawing  # noqa: E402

_DISC = {d.value: d for d in Discipline}


def _page_count(pdf_path: Path) -> int:
    try:
        import fitz
        with fitz.open(pdf_path) as doc:
            return doc.page_count
    except Exception:
        return 1


def index_folder(root: str) -> list[Drawing]:
    """root 下每个子文件夹 = 一个专业；其中每个 PDF = 一张图纸。"""
    root_path = Path(root)
    drawings: list[Drawing] = []
    for pdf in sorted(root_path.rglob("*.pdf")):
        rel = pdf.relative_to(root_path)
        disc_name = rel.parts[0] if len(rel.parts) > 1 else ""
        discipline = _DISC.get(disc_name, Discipline.UNKNOWN)
        drawings.append(Drawing(
            id=f"{disc_name}/{pdf.stem}" if disc_name else pdf.stem,
            name=pdf.stem,
            discipline=discipline,
            path=str(pdf),
            page_count=_page_count(pdf)))
    return drawings
