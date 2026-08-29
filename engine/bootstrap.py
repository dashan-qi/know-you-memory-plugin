from pathlib import Path
from memory_core import MemoryCore

def default_data_dir() -> Path:
    import os
    return Path(os.environ.get("MEMORY_CORE_DATA", str(Path.home() / ".memory_core")))

def ensure_data_dir(data_dir: Path | None = None) -> Path:
    dd = Path(data_dir) if data_dir else default_data_dir()
    dd.mkdir(parents=True, exist_ok=True)
    mc = MemoryCore(data_dir=dd)   # 触发建库（幂等）
    mc.close()
    return dd
