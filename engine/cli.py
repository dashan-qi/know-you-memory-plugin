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

def cmd_inspect(args):
    """记忆巡检：只读健康报告；--clean 时顺带归档/过期/权重刷新/TF-IDF重建"""
    do_clean = "--clean" in args
    mc = MemoryCore(data_dir=default_data_dir())
    try:
        sqlite = mc.store.sqlite
        rep = {}
        # 1. 状态 / 分层
        rep["status_counts"] = {st: sqlite.count(status=st) for st in ["active", "archived", "expired"]}
        rep["by_layer"] = {l: sqlite.count(layer=l) for l in ["L0", "L1", "L2", "L3", "L4", "L5", "LCM"]}
        entries = sqlite.list_all(status="active")
        rep["active_total"] = len(entries)

        # 2. 健康项
        low = [e for e in entries if (e.weight or 0) < 0.2]
        rep["low_weight_lt_0.2"] = len(low)
        rep["low_weight_samples"] = [
            {"id": e.id, "layer": e.layer, "w": round(e.weight or 0, 2),
             "snip": e.content[:42].replace("\n", " ")} for e in low[:8]]
        rep["no_project"] = sum(1 for e in entries if not e.project_id)
        rep["manual_entries_no_source"] = sum(1 for e in entries if not e.source_file)

        # 3. 重复内容（按前 80 字聚类）
        seen, dups = {}, []
        for e in entries:
            k = e.content.strip()[:80]
            if k in seen:
                dups.append((seen[k], e.id))
            else:
                seen[k] = e.id
        rep["duplicate_pairs"] = len(dups)
        rep["duplicate_samples"] = [{"a": a, "b": b} for a, b in dups[:5]]

        # 4. 来源分布（分拣完整性）
        srcs = {}
        for e in entries:
            if e.source_file:
                top = e.source_file.replace("\\", "/").split("/")[-1].split("::")[0]
                srcs[top] = srcs.get(top, 0) + 1
        rep["source_files"] = dict(sorted(srcs.items(), key=lambda x: -x[1]))

        # 5. --clean：执行维护
        if do_clean:
            from memory_core.classify import run_maintenance, refresh_weights
            rep["maintenance"] = run_maintenance(sqlite)
            rep["weights_refreshed"] = refresh_weights(sqlite)
            mc.retriever.build_index()

        print(json.dumps(rep, ensure_ascii=False, indent=2))
    finally:
        mc.close()

def main():
    cmds = {"add": cmd_add, "recall": cmd_recall, "status": cmd_status,
            "import": cmd_import, "inspect": cmd_inspect}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print("usage: python engine/cli.py <add|recall|status|import|inspect> [args]"); sys.exit(2)
    cmds[sys.argv[1]](sys.argv[2:])

if __name__ == "__main__":
    main()
