"""KYM PostToolUse hook — 轻量记录关键写操作（V1：只记 CLAUDE.md/README 变更）"""
import json, os, sys
from pathlib import Path
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT))          # 让 `engine.bootstrap` 可解析
sys.path.insert(0, str(PLUGIN_ROOT / "engine"))

# Windows 管道默认 GBK，强制 UTF-8，保证中文输出不被转码/抛错
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from memory_core import MemoryCore
from engine.bootstrap import default_data_dir

def main():
    try:
        inp = json.loads(os.environ.get("TOOL_INPUT", "{}"))
        path = (inp.get("file_path") or "").replace("\\", "/")
        name = Path(path).name
        if name in ("CLAUDE.md", "AGENTS.md", "README.md") and path:
            mc = MemoryCore(data_dir=default_data_dir())
            mc.add(f"项目文档 {name} 已更新: {path}", layer="L4",
                   category="knowledge", source_file=path, dedup=True)
            mc.close()
    except Exception:
        pass  # 记录失败不打扰

if __name__ == "__main__":
    main()
