"""KYM SessionStart hook — 首次空库自动分拣 + 上下文注入"""
import sys
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
from memory_core.importer import import_workspace
from engine.bootstrap import default_data_dir

def _pending_flags(mc, limit=3):
    marks = ("待验证", "未完成", "TODO", "待办", "待修复")
    rows = mc.store.sqlite._conn.execute(
        "SELECT content, layer FROM memories WHERE status='active' "
        "AND (content LIKE '%待验证%' OR content LIKE '%未完成%' OR content LIKE '%TODO%' "
        "OR content LIKE '%待办%' OR content LIKE '%待修复%') ORDER BY weight DESC LIMIT ?",
        (limit,)).fetchall()
    return [{"content": r[0][:150], "layer": r[1]} for r in rows]

def main(argv=None, data_dir_override=None):
    argv = argv if argv is not None else sys.argv[1:]
    cwd = Path(argv[0]) if argv else Path.cwd()
    dd = Path(data_dir_override) if data_dir_override else default_data_dir()
    try:
        mc = MemoryCore(data_dir=dd)
        total = mc.store.sqlite.count()
        if total == 0:
            result = import_workspace(cwd, data_dir=dd)
            report = "  ".join(f"{k}:{v}" for k, v in sorted(result["by_layer"].items()))
            print(f"[kym] 首次分拣: 导入 {result['imported']} 条 ({report})")
        st = mc.stats()
        print(f"[kym] Memory OK | {st['total']} memories | data: {dd}")
        # 最近活跃 top-5
        rows = mc.store.sqlite._conn.execute(
            "SELECT content, layer FROM memories WHERE status='active' "
            "ORDER BY weight DESC, updated_at DESC LIMIT 5").fetchall()
        if rows:
            print("\n## 🧠 KYM 最近记忆 (top-5)")
            for content, layer in rows:
                print(f"- [{layer}] {content[:120].strip()}")
        flags = _pending_flags(mc)
        if flags:
            print("\n## ⚠️ KYM 待办 flags")
            for f in flags:
                print(f"- [{f['layer']}] {f['content']}")
        mc.close()
    except Exception as e:
        print(f"[kym] WARNING: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
