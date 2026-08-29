"""KYM CLI — python engine/cli.py <add|recall|status|import> ..."""
import sys
import json
from pathlib import Path

_ENGINE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _ENGINE_DIR.parent
sys.path.insert(0, str(_ENGINE_DIR))     # 找到 memory_core（engine/ 内）
sys.path.insert(0, str(_PROJECT_ROOT))   # 找到 engine 命名空间包（engine.bootstrap）

# 统一 UTF-8 输出：Windows 管道下 stdout 默认 GBK，中文内容被 hook/MCP 消费会乱码
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from memory_core import MemoryCore
from engine.bootstrap import default_data_dir, ensure_data_dir
from memory_core.importer import import_path

def cmd_add(args):
    mc = MemoryCore(data_dir=default_data_dir())
    mid = mc.add(" ".join(args), auto_classify=True)
    mc.close()
    print(mid)

def cmd_recall(args):
    query = " ".join(args)
    mc = MemoryCore(data_dir=default_data_dir())
    for entry, verdict in mc.recall(query, max_results=5):
        print(f"[{verdict.confidence_tier}][{entry.layer}] {entry.content[:200]}")
    mc.close()

def cmd_status(_args):
    mc = MemoryCore(data_dir=default_data_dir())
    st = mc.stats()
    print(json.dumps(st, ensure_ascii=False))
    mc.close()

def cmd_import(args):
    path = Path(args[0]) if args else Path.cwd()
    ensure_data_dir()
    print(json.dumps(import_path(path, data_dir=default_data_dir()), ensure_ascii=False))

def main():
    cmds = {"add": cmd_add, "recall": cmd_recall, "status": cmd_status, "import": cmd_import}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print("usage: python engine/cli.py <add|recall|status|import> [args]"); sys.exit(2)
    cmds[sys.argv[1]](sys.argv[2:])

if __name__ == "__main__":
    main()
