# Codex / dsh 接入说明（不跑 Claude Code）

小扣（Codex）和大圣（dsh）不运行 Claude Code，装不了 Claude 插件。KYM 的引擎与插件外壳
是分离的：`engine/` 和 `servers/mcp_server.py` 都是纯 stdlib（默认非向量路径零第三方依赖），
任意 Python 进程或 MCP 客户端可直接消费。

```
engine/
  memory_core/        # 记忆内核（MemoryCore：add / recall / stats / close）
  cli.py              # 命令行入口
servers/mcp_server.py # MCP stdio server（JSON-RPC 2.0 over stdio，零依赖）
```

数据目录统一由 `MEMORY_CORE_DATA` 决定（默认 `~/.memory_core`）。**三兄弟都指向同一目录，
就是共用同一份记忆。**

## 方式一：Python 直接 import（推荐）

```python
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent  # know_you_memory_plugin 仓库根
sys.path.insert(0, str(PLUGIN_ROOT / "engine"))

from memory_core import MemoryCore

mc = MemoryCore()   # 读 $MEMORY_CORE_DATA；未设则 ~/.memory_core
mid = mc.add("DuckDB ATTACH 日期类型不兼容，改直连", project="factor-agent", tags=["踩坑"])
for entry, verdict in mc.recall("DuckDB 日期问题怎么修？", max_results=5):
    print(f"[{verdict.confidence_tier}][{entry.layer}] {entry.content}")
mc.close()
```

要点：
- `memory_core` 包在 `engine/` 下，先 `sys.path` 指向它再 `from memory_core import MemoryCore`。
- 数据目录在 import 时锁定：**`MEMORY_CORE_DATA` 要在 import 之前设好**，或显式
  `MemoryCore(data_dir=Path("<dir>"))`。
- 主要方法：`add(content, *, layer, category, project, tags, ...)` → 返回 memory_id；
  `recall(query, *, project, max_results)` → `[(MemoryEntry, Verdict)]`；
  `stats()` → `{total, by_layer, vectors}`；用完 `close()`。
- 中文分词：环境里有 jieba 会自动用（仅提示，无则走内置 bigram 回退，无需安装）。

## 方式二：命令行 CLI

在仓库根目录运行（相对路径，不改任何配置）：

```bash
python engine/cli.py add "要记住的内容"
python engine/cli.py recall "查询词"
python engine/cli.py status
python engine/cli.py import [path]   # 手动分拣目录/文件的已有记忆
```

## 方式三：独立拉起 MCP stdio server

```bash
python servers/mcp_server.py
```

标准 MCP stdio 协议：从 stdin 读 JSON-RPC 2.0 帧，结果写 stdout（强制 UTF-8，中文安全）。
工具：`memory_add` / `memory_recall` / `memory_status` / `memory_import`。裸协议一例：

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"memory_recall","arguments":{"query":"DuckDB"}}}
```

你的 harness 若支持 MCP stdio 客户端，把命令配成 `python <plugin-root>/servers/mcp_server.py`、
cwd 指向仓库根即可；`servers/mcp_server.py` 会自动把仓库根 `engine/` 挂进 sys.path。

## 共享记忆（三兄弟同一份）

所有实例把 `MEMORY_CORE_DATA` 指到同一个目录：

```bash
export MEMORY_CORE_DATA=/shared/memory_core        # bash / dsh
$env:MEMORY_CORE_DATA = "<shared-dir>"             # PowerShell / Codex
```

谁写谁读，同一个 SQLite 库（`<data_dir>/memory.db`）。默认路径不用 pip 装任何东西；
可选语义向量增强需 `pip install -r engine/requirements-vectors.txt` 并设
`MEMORY_CORE_VECTORS=1`（默认关闭，不装完全可用）。
