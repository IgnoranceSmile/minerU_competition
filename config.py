"""DrawAgent 全局配置。密钥放 .env（已 gitignore），其余可用环境变量覆盖。"""
import os
from pathlib import Path

ROOT = Path(__file__).parent

# 加载 .env（KEY=VALUE 行，存放密钥，不入库）
_env = ROOT / ".env"
if _env.exists():
    for _ln in _env.read_text(encoding="utf-8").splitlines():
        _ln = _ln.strip()
        if _ln and not _ln.startswith("#") and "=" in _ln:
            _k, _v = _ln.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# 推理模型后端（OpenAI 兼容协议，可切换任意厂商）
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

# MinerU 解析结果（每图一个 hybrid_auto 目录）
MINERU_ROOT = os.getenv("MINERU_ROOT", str(ROOT / "data" / "mineru"))

# Mock 开关
_mineru_ready = Path(MINERU_ROOT).exists()
USE_MOCK_MINERU = os.getenv("USE_MOCK_MINERU",
                            "0" if _mineru_ready else "1") == "1"

# 数据与缓存
CACHE_DIR = ROOT / "data" / "cache"
RENDER_DPI = int(os.getenv("RENDER_DPI", "300"))

# 服务
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))

CACHE_DIR.mkdir(parents=True, exist_ok=True)
