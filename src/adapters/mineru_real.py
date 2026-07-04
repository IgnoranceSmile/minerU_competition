"""真实 MinerU 解析器。

两阶段设计：
  1. parse_folder(root) — 按专业分组并行解析，结果按原始目录层级保存到 MINERU_ROOT
  2. parse(drawing)     — 读取已解析的结果，转成 contracts.ParsedDrawing

目录结构保持与输入一致：
  输入  data/origin/建筑/平面图.pdf
  产出  data/mineru/建筑/平面图/hybrid_auto/
          平面图.md
          平面图_content_list.json
          平面图_origin.pdf
          images/

并行策略：
  按专业子目录分组，每组调用一次 do_parse（模型只初始化一次），组间线程并行。
  4 个专业 × 1 次模型加载 = 4 次加载（原来 33 次）。
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from contracts.interfaces import (BBox, Block, BlockType, Drawing,  # noqa: E402
                                  ParsedDrawing)
from config import CACHE_DIR, MINERU_ROOT, RENDER_DPI  # noqa: E402

# 标题栏特征字段
_TITLE_KW = ("专业负责人", "设计号", "审定人", "制图人", "校对人", "图别", "图号",
             "摘要", "Abstract", "目录", "项目名称")

# 1.2B 模型只需 ~2.5GB，vLLM 默认预分配 50% 显存（40GB/卡）太浪费
# 在模块加载时 monkey-patch，把预分配降到 0.1（~8GB/卡），4 组可并行
try:
    import mineru.backend.vlm.utils as _vlm_utils  # noqa: E402
    _vlm_utils.set_default_gpu_memory_utilization = lambda: 0.8
except ImportError:
    pass


# ===== 阶段一：按专业分组并行解析 =====

def _method_subdir(backend: str, parse_method: str) -> str:
    if backend.startswith("hybrid"):
        return f"hybrid_{parse_method}"
    return parse_method


def _parse_group(
    group_key: str,
    pdfs: list[Path],
    output_path: Path,
    input_path: Path,
    *,
    backend: str,
    parse_method: str,
    lang: str,
    formula_enable: bool,
    table_enable: bool,
    start_page_id: int,
    end_page_id: int | None,
    skip_existing: bool,
) -> dict[str, str]:
    """解析同一目录下的一组 PDF（一次 do_parse 调用，模型只初始化一次）。"""
    from mineru.cli.common import do_parse, read_fn  # noqa: E402

    msub = _method_subdir(backend, parse_method)

    # 按目录分组：同一 output_dir 下的 PDF 一起提交
    # rel_dir 可能是 "建筑" / "结构" 等
    grouped_by_dir: dict[str, list[tuple[Path, str]]] = defaultdict(list)
    for pdf in pdfs:
        rel = pdf.relative_to(input_path)
        rel_dir = str(rel.parent)
        if rel_dir == ".":
            rel_dir = ""
        grouped_by_dir[rel_dir].append((pdf, rel.stem))

    group_results: dict[str, str] = {}

    for rel_dir, items in grouped_by_dir.items():
        task_output = output_path / rel_dir if rel_dir else output_path

        # 过滤已解析
        todo: list[tuple[Path, str]] = []
        for pdf, stem in items:
            result_dir = task_output / stem / msub
            cl = result_dir / f"{stem}_content_list.json"
            if skip_existing and cl.exists():
                group_results[str(Path(rel_dir) / stem)] = str(result_dir)
            else:
                todo.append((pdf, stem))

        if not todo:
            continue

        names = [stem for _, stem in todo]
        print(f"[MinerU] [{group_key}] 开始解析 {len(todo)} 个 PDF：{', '.join(names)}")

        try:
            do_parse(
                output_dir=str(task_output),
                pdf_file_names=names,
                pdf_bytes_list=[read_fn(p) for p, _ in todo],
                p_lang_list=[lang] * len(todo),
                backend=backend,
                parse_method=parse_method,
                formula_enable=formula_enable,
                table_enable=table_enable,
                start_page_id=start_page_id,
                end_page_id=end_page_id,
                f_draw_layout_bbox=False,
                f_draw_span_bbox=False,
                f_dump_md=True,
                f_dump_middle_json=False,
                f_dump_model_output=False,
                f_dump_orig_pdf=True,
                f_dump_content_list=True,
            )
            for pdf, stem in todo:
                rel_dir_path = str(Path(rel_dir) / stem) if rel_dir else stem
                result_dir = task_output / stem / msub
                group_results[rel_dir_path] = str(result_dir)
            print(f"[MinerU] [{group_key}] 完成 {len(todo)} 个 PDF")
        except Exception as e:
            # 整组失败时逐个重试
            print(f"[MinerU] [{group_key}] 批量失败（{e}），逐个重试…")
            for pdf, stem in todo:
                try:
                    do_parse(
                        output_dir=str(task_output),
                        pdf_file_names=[stem],
                        pdf_bytes_list=[read_fn(pdf)],
                        p_lang_list=[lang],
                        backend=backend,
                        parse_method=parse_method,
                        formula_enable=formula_enable,
                        table_enable=table_enable,
                        start_page_id=start_page_id,
                        end_page_id=end_page_id,
                        f_draw_layout_bbox=False,
                        f_draw_span_bbox=False,
                        f_dump_md=True,
                        f_dump_middle_json=False,
                        f_dump_model_output=False,
                        f_dump_orig_pdf=True,
                        f_dump_content_list=True,
                    )
                    rel_dir_path = str(Path(rel_dir) / stem) if rel_dir else stem
                    result_dir = task_output / stem / msub
                    group_results[rel_dir_path] = str(result_dir)
                except Exception as e2:
                    print(f"[MinerU] [{group_key}] 失败：{stem} — {e2}")

    return group_results


def parse_folder(
    input_root: str,
    output_root: str | None = None,
    *,
    backend: str = "pipeline",
    parse_method: str = "auto",
    lang: str = "ch",
    formula_enable: bool = False,
    table_enable: bool = True,
    start_page_id: int = 0,
    end_page_id: int | None = None,
    skip_existing: bool = True,
    max_workers: int = 4,
) -> dict[str, str]:
    """用 MinerU 按专业分组并行解析图纸文件夹。

    参数：
        input_root:   图纸根目录，含专业子文件夹（建筑/结构/给排水/电气）
        output_root:  解析结果输出根目录，默认 MINERU_ROOT
        backend:      MinerU 后端（pipeline / vlm-auto-engine / hybrid-auto-engine）
        parse_method: 解析方法（auto / txt / ocr）
        lang:         语言（ch / en 等）
        formula_enable: 是否启用公式解析
        table_enable:   是否启用表格解析
        start_page_id:  起始页（0-based）
        end_page_id:    结束页（None = 全部）
        skip_existing:  跳过已存在结果的 PDF
        max_workers:    并行线程数（按专业分组数，建议 2-4）

    返回：
        {相对路径（如 "建筑/平面图"）: 结果目录路径}
    """
    output_root = output_root or MINERU_ROOT
    input_path = Path(input_root)
    output_path = Path(output_root)

    pdfs = sorted(input_path.rglob("*.pdf"))
    if not pdfs:
        print(f"[MinerU] 输入目录无 PDF：{input_root}")
        return {}

    # 按直接父目录名分组（即按专业分组）
    groups: dict[str, list[Path]] = defaultdict(list)
    for pdf in pdfs:
        rel = pdf.relative_to(input_path)
        group_name = str(rel.parent) if str(rel.parent) != "." else "root"
        groups[group_name].append(pdf)

    print(f"[MinerU] 共 {len(pdfs)} 个 PDF，分 {len(groups)} 组并行解析")
    for g, gs in groups.items():
        print(f"  {g}：{len(gs)} 个")

    results: dict[str, str] = {}

    if len(groups) == 1:
        # 单组无需线程池
        gname, gpdfs = next(iter(groups.items()))
        results.update(_parse_group(
            gname, gpdfs, output_path, input_path,
            backend=backend, parse_method=parse_method, lang=lang,
            formula_enable=formula_enable, table_enable=table_enable,
            start_page_id=start_page_id, end_page_id=end_page_id,
            skip_existing=skip_existing,
        ))
    else:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(groups))) as pool:
            futures = {
                pool.submit(
                    _parse_group,
                    gname, gpdfs, output_path, input_path,
                    backend=backend, parse_method=parse_method, lang=lang,
                    formula_enable=formula_enable, table_enable=table_enable,
                    start_page_id=start_page_id, end_page_id=end_page_id,
                    skip_existing=skip_existing,
                ): gname
                for gname, gpdfs in groups.items()
            }
            for future in as_completed(futures):
                gname = futures[future]
                try:
                    results.update(future.result())
                except Exception as e:
                    print(f"[MinerU] 组 {gname} 异常：{e}")

    print(f"[MinerU] 全部完成：{len(results)}/{len(pdfs)} 成功")
    return results


# ===== 阶段二：读取单图解析结果 → ParsedDrawing =====

def _find_result_dir(drawing: Drawing) -> Path | None:
    """在 MINERU_ROOT 下定位图纸的解析结果目录。"""
    root = Path(MINERU_ROOT)
    name = drawing.name

    disc = ""
    if "/" in drawing.id:
        disc = drawing.id.split("/")[0]

    candidates = []
    for method in ("hybrid_auto", "auto", "ocr", "txt"):
        if disc:
            candidates.append(root / disc / name / method)
        candidates.append(root / name / method)

    for c in candidates:
        if (c / f"{name}_content_list.json").exists():
            return c
    return None


class RealMinerUParser:
    """满足 contracts.MinerUParser 接口。

    parse() 读取 MinerU 已有的解析结果（由 parse_folder() 或 mineru CLI 产出）。
    若找不到结果，回退到 MockMinerUParser。
    """

    def parse(self, drawing: Drawing) -> ParsedDrawing:
        result_dir = _find_result_dir(drawing)
        if result_dir is None:
            from src.adapters.mineru_mock import MockMinerUParser
            return MockMinerUParser().parse(drawing)

        cl_file = result_dir / f"{drawing.name}_content_list.json"
        if not cl_file.exists():
            from src.adapters.mineru_mock import MockMinerUParser
            return MockMinerUParser().parse(drawing)

        items = json.loads(cl_file.read_text(encoding="utf-8"))
        md_file = result_dir / f"{drawing.name}.md"
        markdown = (md_file.read_text(encoding="utf-8")
                    if md_file.exists() else "")

        blocks = self._build_blocks(drawing, items)
        page_images = self._render_pages(drawing, result_dir)

        return ParsedDrawing(
            drawing_id=drawing.id,
            blocks=blocks,
            markdown=markdown,
            page_images=page_images,
        )

    @staticmethod
    def _build_blocks(drawing: Drawing, items: list) -> list[Block]:
        blocks: list[Block] = []
        for i, it in enumerate(items):
            t = it.get("type")
            page = it.get("page_idx", 0)
            bb = it.get("bbox") or [0, 0, 0, 0]
            bbox = BBox(bb[0], bb[1], bb[2], bb[3], page)

            if t == "table":
                caption = "；".join(it.get("table_caption") or []) or "表格"
                blocks.append(Block(
                    id=f"{drawing.id}#b{i}", drawing_id=drawing.id,
                    page=page, bbox=bbox, type=BlockType.TABLE,
                    text=caption, html=it.get("table_body", ""),
                    reading_order=i))
            elif t == "text":
                txt = (it.get("text") or "").strip()
                if not txt:
                    continue
                if any(k in txt for k in _TITLE_KW):
                    bt = BlockType.HEADER
                elif it.get("text_level"):
                    bt = BlockType.TITLE
                else:
                    bt = BlockType.TEXT
                blocks.append(Block(
                    id=f"{drawing.id}#b{i}", drawing_id=drawing.id,
                    page=page, bbox=bbox, type=bt, text=txt,
                    reading_order=i))
        return blocks

    @staticmethod
    def _render_pages(drawing: Drawing, result_dir: Path) -> list[str]:
        """渲染 page 图（供前端查看 / bbox 标注叠加）。"""
        try:
            import fitz
        except ImportError:
            return []

        src = result_dir / f"{drawing.name}_origin.pdf"
        if not src.exists():
            src = Path(drawing.path)
        if not src.exists():
            return []

        fitz.TOOLS.set_aa_level(0)
        out: list[str] = []
        with fitz.open(src) as doc:
            for pno in range(doc.page_count):
                pix = doc[pno].get_pixmap(
                    dpi=RENDER_DPI, colorspace=fitz.csRGB, alpha=False)
                p = CACHE_DIR / f"{drawing.id.replace('/', '__')}_p{pno}.png"
                pix.save(p)
                out.append(str(p))
        return out


# ===== CLI 入口 =====

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="MinerU 批量解析图纸文件夹")
    parser.add_argument("input_dir", help="图纸文件夹路径（含专业子文件夹）")
    parser.add_argument("-o", "--output-dir", default=None,
                        help="输出目录（默认 data/mineru）")
    parser.add_argument("-b", "--backend", default="hybrid-auto-engine",
                        choices=["pipeline", "vlm-auto-engine",
                                 "hybrid-auto-engine"],
                        help="MinerU 后端（默认 hybrid-auto-engine）")
    parser.add_argument("-m", "--method", default="auto",
                        choices=["auto", "txt", "ocr"],
                        help="解析方法（默认 auto）")
    parser.add_argument("-l", "--lang", default="ch", help="语言（默认 ch）")
    parser.add_argument("--no-skip", action="store_true",
                        help="不跳过已解析的 PDF")
    parser.add_argument("-w", "--workers", type=int, default=4,
                        help="并行线程数（默认 4，按专业分组数）")
    args = parser.parse_args()

    results = parse_folder(
        args.input_dir,
        output_root=args.output_dir,
        backend=args.backend,
        parse_method=args.method,
        lang=args.lang,
        skip_existing=not args.no_skip,
        max_workers=args.workers,
    )
    for rel, path in sorted(results.items()):
        print(f"  {rel} → {path}")
