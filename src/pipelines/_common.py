"""Pipeline 共享工具：OCR 纠错、图签提取、目录条目解析、表格结构化。"""
from __future__ import annotations

import re


# OCR 纠错
_OCR_TERMS = {
    "铜筋": "钢筋", "退凝土": "混凝土", "签筋": "箍筋", "锥筋": "箍筋",
    "篝筋": "箍筋", "基础厚": "基础顶", "基础坝": "基础顶", "徽膨胀": "微膨胀",
    "凌工": "竣工", "悬臂聚": "悬臂梁", "翻边商": "翻边高", "翻边阔": "翻边高",
}


def ocr_correct(text: str) -> str:
    """术语纠错 + 国标图集编号 O/0 纠错。"""
    for wrong, right in _OCR_TERMS.items():
        text = text.replace(wrong, right)
    text = re.sub(r"(\d)[O0]([GJSG]\d{3})", r"\g<1>0\2", text)
    text = re.sub(r"([GJSG]\d{3})[O0](\d)", r"\g<1>0\2", text)
    return text


def strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s)


# 图签提取
_CLEAN_STOPS = [
    "设计号", "委托单位", "合作单位", "公司出图", "项目总负责人", "图纸名称",
    "DRAWING", "STATUS", "SCALE", "版本REVISION", "版本Revision",
    "日期DATE", "会签栏", "比例SCALE",
]
_ROLE_FIELDS = [
    ("项目总负责人", "project_director"), ("审定人", "authorized_by"),
    ("专业负责人", "discipline_lead"), ("校对人", "checked_by"),
    ("设计人", "designer"), ("制图人", "drawn_by"),
]


def _clean_field(val: str) -> str:
    for s in _CLEAN_STOPS:
        i = val.find(s)
        if i > 0:
            val = val[:i]
    return val.split("\n")[0].strip()


def extract_title_block(md: str) -> dict:
    """从文档 markdown 提取图签结构化字段。"""
    tbl, tbl_start = None, 0
    for m in re.finditer(r"<table[^>]*>(.*?)</table>", md, re.DOTALL):
        if "DRAWING" in m.group(1) or "DESIGNED" in m.group(1):
            tbl, tbl_start = m.group(1), m.start()
    out: dict = {}
    if tbl is not None:
        flat = re.sub(r"\s+", " ", strip_html(tbl))
        m = re.search(r"图别\s*STATUS\s+(.+?)\s+图号\s*DRAWING\s*NO\.?\s+(\S+)", flat)
        if m:
            out["discipline"] = m.group(1).replace(" ", "")
            out["drawing_number"] = m.group(2)
        m = re.search(r"版本\s*(?:REVISION|Revision|revision)\s+(\S+)", flat)
        if m:
            out["version"] = m.group(1)
        m = re.search(r"日期\s*DATE\s+([\d.]+)", flat)
        if m:
            out["date"] = m.group(1)
        for label, key in _ROLE_FIELDS:
            rm = re.search(label + r"[^一-龥]{0,30}?([一-龥]{2,4})", flat)
            if rm:
                out[key] = rm.group(1)
    pre = md[:tbl_start] if tbl_start else md
    m = re.search(r"项目名称\s*[:：]?\s*PROJECT\s*NAME\s*(.+?)"
                  r"(?=设计号|委托|合作|公司出图|项目总负责人|图纸名称|\Z)", pre)
    if m:
        out["project_name"] = _clean_field(m.group(1))
    m = re.search(r"图纸名称\s*[:：]?\s*DRAWING\s*TITLE\s*(.+?)"
                  r"(?=设计号|委托|合作|公司出图|项目总负责人|DRAWING\s*NO|STATUS"
                  r"|SCALE|版本|日期|会签栏|<table|\Z)", pre)
    if m:
        out["drawing_title"] = _clean_field(m.group(1))
    dno = re.search(r"(20\d{2}-\d{3})", md)
    if dno:
        out["design_no"] = dno.group(1)
    return out


# 目录条目解析
_CAT_NO = re.compile(r"((?:建施|结施|电施|水施)\s*[-–]\s*\d+)")


def parse_table_rows(md: str) -> list[list[str]]:
    """把 markdown 里所有 HTML 表格拆成 行→单元格文本。"""
    rows: list[list[str]] = []
    for tbl in re.findall(r"<table[^>]*>(.*?)</table>", md, re.DOTALL):
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, re.DOTALL):
            cells = [strip_html(td).strip()
                     for td in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.DOTALL)]
            if cells:
                rows.append(cells)
    return rows


def extract_catalog_entries(md: str) -> list[dict]:
    """从目录文档提取 {图号, 图名} 列表。"""
    entries: list[dict] = []
    seen: set[str] = set()
    for cells in parse_table_rows(md):
        joined = " ".join(cells)
        if "DESIGNED BY" in joined:
            continue
        num_m = _CAT_NO.search(joined)
        if not num_m:
            continue
        num = re.sub(r"\s", "", num_m.group(1)).replace("–", "-")
        if num in seen:
            continue
        seen.add(num)
        names = [c for c in cells if not _CAT_NO.search(c)
                 and re.search(r"[一-龥]", c)]
        name = max(names, key=len) if names else ""
        name = re.sub(r"A\d{0,2}\+?\d*/?\d*$", "", name).strip()
        entries.append({"number": num, "name": name})
    return entries


def discipline_of(number: str) -> str:
    """从图号取专业代码。"""
    m = re.match(r"(建施|结施|电施|水施)", number)
    return m.group(1) if m else ""


# 表格结构化提取
def extract_tables_from_parsed(parsed_drawing) -> list[dict]:
    """从 MinerU 解析结果中提取所有表格，返回结构化列表。"""
    tables = []
    for block in parsed_drawing.tables():
        tables.append({
            "block_id": block.id,
            "page": block.page,
            "caption": block.text,
            "html": block.html,
            "bbox": [block.bbox.x0, block.bbox.y0, block.bbox.x1, block.bbox.y1],
        })
    return tables
