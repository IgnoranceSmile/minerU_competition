"""DrawAgent · HTTP 服务。

端点：
  POST /api/upload        上传图纸压缩包(.zip) → 解压、索引、重建上下文
  GET  /api/drawings      当前图纸库 manifest
  POST /api/chat          对话，SSE 流式：reasoning/content/tool_start/tool_result/done
  GET  /api/drawing_page  取某张图的页渲染图（前端 canvas 用）
  GET  /api/image         取缓存图（bbox 渲染图等）
  GET  /api/trace         最近一次对话的执行轨迹
  POST /api/export        导出结构化结果（JSON/CSV）

SSE 事件契约见 src/harness/agent.py。
"""
from __future__ import annotations

import json
import logging
import shutil
import sys
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import CACHE_DIR, HOST, PORT, ROOT  # noqa: E402
from src.harness.context import build_context  # noqa: E402
from src.harness.registry import PipelineRegistry  # noqa: E402
from src.harness.agent import TaskPlanner  # noqa: E402

from fastapi import FastAPI, File, HTTPException, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from sse_starlette.sse import EventSourceResponse  # noqa: E402

# 结构化执行日志
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
_trace_logger = logging.getLogger("drawagent.trace")
_trace_logger.setLevel(logging.INFO)
_fh = logging.FileHandler(LOG_DIR / "trace.jsonl", encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(message)s"))
_trace_logger.addHandler(_fh)

UPLOAD_DIR = ROOT / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_ROOT = ROOT / "data" / "origin"
_DISC = {"建筑", "结构", "给排水", "电气"}

app = FastAPI(title="DrawAgent")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


class State:
    """当前会话的图纸上下文（单用户 demo，全局可变）。"""
    ctx = None
    planner = None
    root = ""


def rebuild(root: str) -> int:
    """从图纸文件夹重建上下文 + TaskPlanner，返回图纸数。"""
    ctx = build_context(root)
    registry = PipelineRegistry()
    for p in _load_pipelines():
        registry.register(p)
    State.ctx = ctx
    State.planner = TaskPlanner(ctx, registry)
    State.root = root
    return len(ctx.drawings())


def _load_pipelines():
    from src.pipelines import all_pipelines
    return all_pipelines()


def _manifest() -> list[dict]:
    if State.ctx is None:
        return []
    return [{"id": d.id, "name": d.name, "discipline": d.discipline.value,
             "page_count": d.page_count} for d in State.ctx.drawings()]


def _find_drawing_root(extracted: Path) -> Path:
    """在解压目录里定位图纸根（含专业子文件夹的那一层）。"""
    if any((extracted / d).is_dir() for d in _DISC):
        return extracted
    subdirs = [p for p in extracted.iterdir()
               if p.is_dir() and not p.name.startswith("__")]
    for sd in subdirs:
        if any((sd / d).is_dir() for d in _DISC):
            return sd
    return subdirs[0] if subdirs else extracted


# 启动即加载默认图纸集
if DEFAULT_ROOT.exists():
    rebuild(str(DEFAULT_ROOT))


# ===== 端点 =====

@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    """上传图纸压缩包(.zip)，解压后重建图纸库。"""
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(400, "请上传 .zip 压缩包")
    session = uuid.uuid4().hex[:8]
    sess_dir = UPLOAD_DIR / session
    sess_dir.mkdir(parents=True, exist_ok=True)
    zip_path = sess_dir / file.filename
    with open(zip_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    extract_dir = sess_dir / "extracted"
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)
    except zipfile.BadZipFile:
        raise HTTPException(400, "压缩包损坏或非 zip 格式")
    root = _find_drawing_root(extract_dir)
    count = rebuild(str(root))
    return {"ok": True, "session": session, "count": count,
            "drawings": _manifest()}


@app.get("/api/drawings")
def drawings():
    return {"root": State.root, "count": len(_manifest()),
            "drawings": _manifest()}


class ChatReq(BaseModel):
    prompt: str
    target_drawing: str | None = None
    region: dict | None = None        # {kind, drawing_id, page, x, y, bbox}


@app.post("/api/chat")
def chat(req: ChatReq):
    """对话。SSE 流式返回 reasoning/content/tool_start/tool_result/done。"""
    if State.planner is None:
        raise HTTPException(409, "尚未加载图纸，请先上传压缩包")

    def gen():
        for ev in State.planner.run(req.prompt, req.target_drawing, req.region):
            if ev.get("type") == "tool_result":
                r = ev.get("result") or {}
                if r.get("extra_images"):
                    r["extra_images"] = [f"/api/image?path={p}"
                                         for p in r["extra_images"]]
            yield {"event": ev["type"],
                   "data": json.dumps(ev, ensure_ascii=False)}
    return EventSourceResponse(gen())


@app.get("/api/trace")
def trace():
    """返回最近一次对话的执行轨迹（可追溯）。"""
    if State.planner is None:
        return JSONResponse({"steps": [], "total": 0})
    t = State.planner.last_trace()
    return JSONResponse({"steps": t, "total": len(t),
                         "timestamp": datetime.now().isoformat()})


class ExportReq(BaseModel):
    prompt: str
    target_drawing: str | None = None
    format: str = "json"  # json | csv


@app.post("/api/export")
def export(req: ExportReq):
    """同步执行一次任务，返回结构化结果（JSON/CSV）。"""
    if State.planner is None:
        raise HTTPException(409, "尚未加载图纸")
    result_parts = []
    region = None
    turn_req = {"prompt": req.prompt, "target_drawing": req.target_drawing,
                "region": region}
    for ev in State.planner.run(req.prompt, req.target_drawing, None):
        if ev["type"] == "tool_result":
            r = ev.get("result") or {}
            if r.get("ok"):
                result_parts.append({
                    "pipeline": ev.get("name"),
                    "content": r.get("content", ""),
                    "answer_type": r.get("answer_type", "text"),
                    "data": r.get("data"),
                })
        elif ev["type"] == "done":
            result_parts.append({"pipeline": "summary",
                                 "content": ev.get("content", "")})

    if req.format == "csv":
        import csv
        import io
        buf = io.StringIO()
        w = csv.writer(buf)
        wrote_table = False
        for p in result_parts:
            for title, records in _tabular_records(p.get("data")):
                if not records:
                    continue
                cols = list(records[0].keys())
                w.writerow([f"# {p.get('pipeline')} · {title}"])
                w.writerow(cols)
                for rec in records:
                    w.writerow([_csv_cell(rec.get(c, "")) for c in cols])
                w.writerow([])
                wrote_table = True
        if not wrote_table:   # 无结构化数据时退回文本导出
            w.writerow(["pipeline", "answer_type", "content"])
            for p in result_parts:
                w.writerow([p.get("pipeline"), p.get("answer_type"),
                            p.get("content", "")[:2000]])
        return JSONResponse({"format": "csv", "data": buf.getvalue()})
    return JSONResponse({"format": "json", "results": result_parts,
                         "prompt": req.prompt,
                         "timestamp": datetime.now().isoformat()})


def _csv_cell(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def _tabular_records(data) -> list[tuple[str, list[dict]]]:
    """从 TaskResult.data 中找出所有可表格化的 记录列表（list[dict]）。

    兼容三种形态：顶层 list[dict]；P2 形态 list[{caption, records}]；
    dict 中 value 为 list[dict] 的字段（如 P3/P5 的 per_drawing）。
    """
    def is_records(v) -> bool:
        return (isinstance(v, list) and v
                and all(isinstance(x, dict) for x in v))

    out: list[tuple[str, list[dict]]] = []
    if isinstance(data, list):
        for i, item in enumerate(data):
            if isinstance(item, dict) and is_records(item.get("records")):
                out.append((str(item.get("caption") or f"表格{i + 1}"),
                            item["records"]))
        if not out and is_records(data):
            out.append(("records", data))
    elif isinstance(data, dict):
        for k, v in data.items():
            if is_records(v):
                out.append((k, v))
    return out


@app.get("/api/drawing_page")
def drawing_page(id: str, page: int = 0):
    """取某张图的页渲染图。"""
    if State.ctx is None:
        raise HTTPException(409, "尚未加载图纸")
    d = State.ctx.get_drawing(id)
    if d is None:
        raise HTTPException(404, "图纸不存在")
    pd = State.ctx.parsed(d.id)
    if not pd.page_images or page >= len(pd.page_images):
        raise HTTPException(404, "无该页渲染图")
    return FileResponse(pd.page_images[page])


@app.get("/api/image")
def image(path: str):
    """取缓存图（仅允许 CACHE_DIR / uploads 内的文件）。"""
    p = Path(path).resolve()
    allowed = (str(CACHE_DIR.resolve()), str(UPLOAD_DIR.resolve()))
    if not str(p).startswith(allowed):
        raise HTTPException(403, "非法路径")
    if not p.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(p)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
