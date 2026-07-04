"""Mock MinerU 解析器。

满足 contracts.MinerUParser 接口。用 PyMuPDF 直接抽取文本块 + 渲染页图，
产出与真实 MinerU 同形状的 ParsedDrawing。
真实 MinerU 接入后，换成 mineru_real.py（接口不变）即可。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from contracts.interfaces import (BBox, Block, BlockType, Drawing,  # noqa: E402
                                  ParsedDrawing)
from config import CACHE_DIR, RENDER_DPI  # noqa: E402


class MockMinerUParser:
    def parse(self, drawing: Drawing) -> ParsedDrawing:
        try:
            import fitz
        except ImportError:
            return self._fallback(drawing)

        doc = fitz.open(drawing.path)
        fitz.TOOLS.set_aa_level(0)
        scale = RENDER_DPI / 72.0
        blocks: list[Block] = []
        page_images: list[str] = []
        md_parts: list[str] = []
        order = 0

        for pno in range(doc.page_count):
            page = doc[pno]
            pix = page.get_pixmap(dpi=RENDER_DPI, colorspace=fitz.csRGB, alpha=False)
            img_path = CACHE_DIR / f"{drawing.id.replace('/', '__')}_p{pno}.png"
            pix.save(img_path)
            page_images.append(str(img_path))

            for b in page.get_text("blocks"):
                x0, y0, x1, y1, text, bno, *_ = b
                text = (text or "").strip()
                if not text:
                    continue
                bbox = BBox(x0 * scale, y0 * scale, x1 * scale, y1 * scale, pno)
                btype = self._guess_type(text, bbox, pix.width)
                blocks.append(Block(
                    id=f"{drawing.id}#b{order}", drawing_id=drawing.id,
                    page=pno, bbox=bbox, type=btype, text=text,
                    reading_order=order))
                md_parts.append(text)
                order += 1

            try:
                for ti, tbl in enumerate(page.find_tables().tables):
                    r = tbl.bbox
                    bbox = BBox(r[0] * scale, r[1] * scale,
                                r[2] * scale, r[3] * scale, pno)
                    html = tbl.to_pandas().to_html(index=False)
                    blocks.append(Block(
                        id=f"{drawing.id}#t{ti}", drawing_id=drawing.id,
                        page=pno, bbox=bbox, type=BlockType.TABLE,
                        html=html, reading_order=order))
                    md_parts.append(f"[表格]\n{html}")
                    order += 1
            except Exception:
                pass

        doc.close()
        return ParsedDrawing(
            drawing_id=drawing.id, blocks=blocks,
            markdown="\n\n".join(md_parts), page_images=page_images)

    @staticmethod
    def _guess_type(text: str, bbox: BBox, page_w: int) -> BlockType:
        kw = ("专业负责人", "设计号", "审定人", "制图人", "图别", "图号")
        if bbox.x0 > page_w * 0.78 and any(k in text for k in kw):
            return BlockType.HEADER
        if len(text) < 20 and "\n" not in text:
            return BlockType.TITLE
        return BlockType.TEXT

    @staticmethod
    def _fallback(drawing: Drawing) -> ParsedDrawing:
        return ParsedDrawing(
            drawing_id=drawing.id,
            markdown=f"[mock] 无法解析 {drawing.name}，请安装 pymupdf。")
