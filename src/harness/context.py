"""DrawAgent 工作空间（上下文 / 缓存）。

Workspace 是 Pipeline 拿到所有能力组件的唯一句柄：
  ctx.parser / ctx.llm            —— 能力组件
  ctx.drawings() / ctx.parsed(id) —— 图纸数据（解析结果带缓存）

build_context() 是工厂：按 config 的 mock 开关装配 mock 或 real 适配器。
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from contracts.interfaces import Drawing, ParsedDrawing  # noqa: E402
from config import CACHE_DIR, USE_MOCK_MINERU, LLM_API_KEY  # noqa: E402
from src.harness.indexer import index_folder  # noqa: E402


class Workspace:
    def __init__(self, root: str, parser, llm) -> None:
        self.root = root
        self.parser = parser
        self.llm = llm
        self._drawings: list[Drawing] = index_folder(root)
        self._by_id = {d.id: d for d in self._drawings}
        self._parsed: dict[str, ParsedDrawing] = {}

    # 图纸 manifest
    def drawings(self) -> list[Drawing]:
        return self._drawings

    def get_drawing(self, drawing_id: str) -> Drawing | None:
        return self._by_id.get(drawing_id)

    def find_drawing(self, query: str) -> Drawing | None:
        """按名称模糊匹配。"""
        if query in self._by_id:
            return self._by_id[query]
        q = query.strip()
        for d in self._drawings:
            if q in d.id or q in d.name or d.name in q:
                return d
        return None

    # 解析结果（内存 + 磁盘缓存）
    def parsed(self, drawing_id: str) -> ParsedDrawing:
        if drawing_id in self._parsed:
            return self._parsed[drawing_id]
        cache_f = CACHE_DIR / f"parsed_{drawing_id.replace('/', '__')}.pkl"
        if cache_f.exists():
            pd = pickle.loads(cache_f.read_bytes())
        else:
            drawing = self._by_id[drawing_id]
            pd = self.parser.parse(drawing)
            cache_f.write_bytes(pickle.dumps(pd))
        self._parsed[drawing_id] = pd
        return pd


def build_context(root: str) -> Workspace:
    """按 config 装配 mock / real 适配器。"""
    if USE_MOCK_MINERU:
        from src.adapters.mineru_mock import MockMinerUParser
        parser = MockMinerUParser()
    else:
        from src.adapters.mineru_real import RealMinerUParser
        parser = RealMinerUParser()

    if LLM_API_KEY:
        from src.adapters.llm_client import OpenAICompatLLM
        llm = OpenAICompatLLM()
    else:
        from src.adapters.llm_mock import MockLLMGateway
        llm = MockLLMGateway()

    return Workspace(root, parser, llm)
