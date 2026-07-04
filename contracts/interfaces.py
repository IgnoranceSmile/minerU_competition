"""DrawAgent · 契约层（Single Source of Truth）

所有 Pipeline、适配器、Engine 组件都依赖这里定义的数据结构与接口。

设计原则：
- 数据结构用 dataclass，接口用 typing.Protocol（鸭子类型，mock 与 real 实现都满足即可）
- 重型 ML 组件（MinerU）只规定 I/O 契约，实现可以是 mock 或 real，可热插拔
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


# ===== 基础类型 =====

class Discipline(str, Enum):
    """专业分类。值与输入文件夹子目录名对齐。"""
    BUILDING = "建筑"
    STRUCTURE = "结构"
    PLUMBING = "给排水"
    ELECTRICAL = "电气"
    UNKNOWN = "未知"


@dataclass
class BBox:
    """像素坐标包围盒。"""
    x0: float
    y0: float
    x1: float
    y1: float
    page: int = 0

    def contains(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def intersects(self, other: "BBox") -> bool:
        if self.page != other.page:
            return False
        return not (self.x1 < other.x0 or other.x1 < self.x0
                    or self.y1 < other.y0 or other.y1 < self.y0)

    def as_list(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1]


class BlockType(str, Enum):
    """MinerU 版面块类型。"""
    TEXT = "text"
    TITLE = "title"
    TABLE = "table"
    FIGURE = "figure"
    HEADER = "header"            # 图纸标题栏 / 文档头信息


# ===== 图纸与解析 =====

@dataclass
class Drawing:
    """一张工程图纸。"""
    id: str                       # 稳定 id，如 "结构/结构设计说明"
    name: str                     # 图名
    discipline: Discipline
    path: str                     # PDF 绝对路径
    page_count: int = 1


@dataclass
class Block:
    """MinerU 解析出的一个版面块。"""
    id: str
    drawing_id: str
    page: int
    bbox: BBox
    type: BlockType
    text: str = ""                # 文本/标题块的纯文本
    html: str = ""                # 表格块的 HTML（MinerU 表格输出）
    reading_order: int = 0


@dataclass
class ParsedDrawing:
    """一张图纸的完整解析结果。"""
    drawing_id: str
    blocks: list[Block] = field(default_factory=list)
    markdown: str = ""            # 按阅读序拼接的 markdown
    page_images: list[str] = field(default_factory=list)  # 各页渲染图路径

    def blocks_of(self, *types: BlockType) -> list[Block]:
        return [b for b in self.blocks if b.type in types]

    def tables(self) -> list[Block]:
        return self.blocks_of(BlockType.TABLE)


# ===== 多模态交互 =====

class RegionKind(str, Enum):
    POINT = "point"
    BOX = "box"


@dataclass
class Region:
    """用户的点选/框选输入。"""
    kind: RegionKind
    drawing_id: str
    page: int = 0
    x: float | None = None
    y: float | None = None
    bbox: BBox | None = None


# ===== Pipeline I/O =====

@dataclass
class TaskRequest:
    """所有 Pipeline 的统一入参。"""
    prompt: str                              # 用户文字
    target_drawing: str | None = None        # 指定/点选的图纸 id
    region: Region | None = None             # 点选/框选
    extra: dict[str, Any] = field(default_factory=dict)


class AnswerType(str, Enum):
    TEXT = "text"
    TABLE = "table"               # content 为 markdown 表格字符串
    BBOX_IMAGE = "bbox_image"     # content 为渲染图路径
    MARKDOWN_LIST = "markdown_list"
    JSON_DATA = "json_data"       # content 为 JSON 字符串（结构化提取结果）
    FILE_EXPORT = "file_export"   # content 为导出文件路径


@dataclass
class Source:
    """答案来源定位，用于可解释性与前端高亮。"""
    drawing: str
    bbox: BBox | None = None
    note: str = ""


@dataclass
class TaskResult:
    """所有 Pipeline 的统一出参。"""
    answer_type: AnswerType
    content: str                              # 文字/markdown/图片路径/JSON
    evidence: list[Source] = field(default_factory=list)
    extra_images: list[str] = field(default_factory=list)  # 附加渲染图
    ok: bool = True
    error: str = ""
    data: Any = None                          # 结构化结果（JSON-safe），供 /api/export 直接消费


# ===== 接口（Protocol）=====

@runtime_checkable
class MinerUParser(Protocol):
    """MinerU 图纸解析。mock 与 real 实现都必须满足此签名。"""
    def parse(self, drawing: Drawing) -> ParsedDrawing: ...


@runtime_checkable
class LLMGateway(Protocol):
    """LLM 推理网关（OpenAI 兼容协议）。

    chat 返回事件流（生成器），事件 dict 形如：
      {"type": "reasoning", "delta": "..."}   思考过程增量
      {"type": "content",   "delta": "..."}   正文增量
      {"type": "tool_call", "name": str, "arguments": dict, "id": str}
      {"type": "done",      "content": str}
    """
    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             stream: bool = True): ...


@runtime_checkable
class Pipeline(Protocol):
    """Pipeline 层统一接口。每个处理场景实现一个。"""
    name: str                     # 工具名，喂给 LLM 的 function name
    description: str              # 何时调用（LLM 据此路由）
    input_schema: dict            # JSON Schema，LLM function calling 用

    def run(self, ctx: "WorkspaceProto", req: TaskRequest) -> TaskResult: ...


@runtime_checkable
class WorkspaceProto(Protocol):
    """工作空间：Pipeline 通过它拿到所有能力句柄。"""
    parser: MinerUParser
    llm: LLMGateway

    def drawings(self) -> list[Drawing]: ...
    def get_drawing(self, drawing_id: str) -> Drawing | None: ...
    def parsed(self, drawing_id: str) -> ParsedDrawing: ...   # 带缓存
