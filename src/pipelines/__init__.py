"""Pipeline 装配入口。"""


def all_pipelines() -> list:
    """返回全部 5 个 Pipeline 实例，供 PipelineRegistry 注册。"""
    from src.pipelines.p1_drawing_qa.handler import DrawingQA
    from src.pipelines.p2_table_extract.handler import TableExtract
    from src.pipelines.p3_batch_parse.handler import BatchParse
    from src.pipelines.p4_cross_drawing.handler import CrossDrawing
    from src.pipelines.p5_quality_verify.handler import QualityVerify
    return [DrawingQA(), TableExtract(), BatchParse(),
            CrossDrawing(), QualityVerify()]
