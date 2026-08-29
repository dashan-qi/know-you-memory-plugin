"""KYM MCP server — 纯 stdlib JSON-RPC 2.0 over stdio，零第三方依赖"""
from __future__ import annotations
import json, os, sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT / "engine"))
os.environ.setdefault("MEMORY_CORE_DATA", str(Path.home() / ".memory_core"))

from memory_core import MemoryCore
from memory_core.importer import import_path


def rpc_reply(msg_id, result=None, error=None):
    obj = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        obj["error"] = {"code": -32000, "message": str(error)}
    else:
        obj["result"] = result
    return obj


class MemoryCoreBackend:
    def __init__(self, data_dir: Path | None = None):
        self.mc = MemoryCore(data_dir=data_dir) if data_dir else MemoryCore()
        self._tools = {
            "memory_add": {
                "description": "写入一条记忆（layer 可选 L1-L5）",
                "inputSchema": {"type": "object", "properties": {
                    "content": {"type": "string"},
                    "layer": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "project": {"type": "string"}},
                    "required": ["content"]}},
            "memory_recall": {
                "description": "检索相关记忆",
                "inputSchema": {"type": "object", "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"}},
                    "required": ["query"]}},
            "memory_status": {
                "description": "记忆库统计",
                "inputSchema": {"type": "object", "properties": {}}},
            "memory_import": {
                "description": "手动分拣一个目录/文件的已有记忆",
                "inputSchema": {"type": "object", "properties": {
                    "path": {"type": "string"},
                    "recursive": {"type": "boolean"}}}},
        }

    def handle(self, method: str, params: dict, msg_id):
        if method == "initialize":
            return rpc_reply(msg_id, {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "know-you-memory", "version": "0.1.0"}})
        if method in ("notifications/initialized", "notifications/cancelled"):
            return None
        if method == "ping":
            return rpc_reply(msg_id, {})
        if method == "tools/list":
            # MCP 规范要求每个 tool 带 name；brief 的 _tools 值里没写 name，
            # 用 dict 键注入（否则 tools/list 测试拿不到 t["name"]）
            tools = [{"name": name, **schema} for name, schema in self._tools.items()]
            return rpc_reply(msg_id, {"tools": tools})
        if method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments", {}) or {}
            try:
                result = self._call_tool(name, args)
                # MCP 规范：成功响应须包 content block，Claude Code 读 result.content 渲染给模型
                return rpc_reply(msg_id, {
                    "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                    "isError": False})
            except Exception as e:
                return rpc_reply(msg_id, error=e)
        return rpc_reply(msg_id, error=f"unknown method: {method}")

    def _call_tool(self, name, args):
        if name == "memory_add":
            mid = self.mc.add(args["content"], layer=args.get("layer"),
                              tags=args.get("tags"), project=args.get("project"))
            return {"memory_id": mid, "layer": args.get("layer") or "auto"}
        if name == "memory_recall":
            out = []
            for entry, verdict in self.mc.recall(args["query"],
                    max_results=args.get("max_results", 5)):
                out.append({"content": entry.content[:500], "layer": entry.layer,
                            "confidence_tier": verdict.confidence_tier,
                            "project": entry.project_id})
            return {"results": out}
        if name == "memory_status":
            st = self.mc.stats()
            return {"total": st["total"], "by_layer": st["by_layer"],
                    "data_dir": str(self.mc.config.data_dir)}
        if name == "memory_import":
            path = Path(args.get("path", "."))
            return import_path(path, data_dir=self.mc.config.data_dir)
        raise ValueError(f"unknown tool: {name}")


def main():
    backend = MemoryCoreBackend()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue  # 合法 JSON 但非对象帧（[]/42/null），不崩循环
        method = msg.get("method")
        params = msg.get("params") or {}
        msg_id = msg.get("id")
        if msg_id is None:
            continue  # 通知
        reply = backend.handle(method, params, msg_id)
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
