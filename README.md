# Know-You-Memory · 知你记忆 (KYM)

> 五层金字塔持久记忆：自动分拣已有记忆、跨会话回忆，纯本地零依赖零 LLM。

Know-You-Memory（KYM）是一个 Claude Code 插件，把五层金字塔记忆体系打包成开箱即用的形态：

- **自动分拣**：安装后首次开窗，自动把当前项目的已有记忆（`CLAUDE.md` / `.claude/` / `README`）分拣进记忆库
- **跨会话回忆**：每次开窗自动注入最近活跃记忆与待办 flags；`/memory-recall` 或 MCP 工具随时检索
- **纯本地**：无 API key、无联网、无第三方 Python 依赖，装完即用

## 前置要求

- Claude Code
- **Python 3.11+**，且 `python` 在 PATH 中（MCP server、hooks、CLI 都直接调用 `python`）

## 安装

```bash
claude plugin marketplace add dashan-qi/know-you-memory-plugin
claude plugin install know-you-memory@openquant --scope user
claude plugin list   # 确认 know-you-memory 已出现且 enabled
```

本地开发时也可用 `--plugin-dir` 直接加载：

```bash
claude --plugin-dir /path/to/know_you_memory_plugin
```

## 首次自动分拣

在任意项目目录首次开窗，KYM 的 SessionStart hook 检测到**空记忆库**时，会自动扫描当前 workspace 顶层与 `.claude/`：

| 来源 | 目标层级 |
|------|---------|
| `CLAUDE.md` / `AGENTS.md` | L3 偏好（原则段会由分类器抬到 L1） |
| `README.md` | L4 项目知识 |
| `.claude/skills/**/SKILL.md` | L3 技能知识 |
| `.claude/commands/*.md` | L3 命令知识 |
| `.claude/settings.json` | L4 环境（只摘要 hooks/mcp 的**名称**） |

分拣是**幂等**的：文件内容没变则跳过，变了则按 `source_file` 更新而非堆叠重复。

之后每次开窗，SessionStart 会自动注入：

```
[kym] Memory OK | N memories | data: ...
## 🧠 KYM 最近记忆 (top-5)
## ⚠️ KYM 待办 flags
```

## 命令与 skill

| 命令 / skill | 作用 |
|---|---|
| `/memory-recall <query>` | 检索相关记忆，标注置信度与层级 |
| `/memory-import [path]` | 手动分拣一个目录/文件的已有记忆（无参数扫当前 workspace） |
| `/memory-status` | 查看记忆库总量 / 分层 / 数据位置 |
| `memory-recorder`（skill） | 引导模型：何时写入、何时检索（偏好/决策/踩坑→写；"上次/之前/你记得"→查） |

MCP 工具（模型可原生调用）：

- `mcp__plugin_know-you-memory_kym__memory_add` — 写入一条记忆
- `mcp__plugin_know-you-memory_kym__memory_recall` — 检索相关记忆
- `mcp__plugin_know-you-memory_kym__memory_status` — 记忆库统计
- `mcp__plugin_know-you-memory_kym__memory_import` — 手动分拣

## 可选：语义向量增强

默认使用**纯 Python TF-IDF** 检索（FTS5 + TF-IDF，零依赖，秒回）。若要启用语义向量检索（BGE 中文嵌入 + LanceDB），需要满足两个条件：

1. 安装可选依赖（重型，含 torch）：

   ```bash
   pip install -r engine/requirements-vectors.txt
   ```

2. 开启环境变量（默认关闭，避免拖入 pyarrow/pandas 拖慢启动）：

   ```bash
   # Windows PowerShell
   $env:MEMORY_CORE_VECTORS = "1"
   # bash / zsh / macOS
   export MEMORY_CORE_VECTORS=1
   ```

开启后写入会自动生成向量，检索走「向量 + TF-IDF」融合排序，语义召回更准。不启用也完全可用。

## 数据目录与隐私

- **数据全本地**：默认 `~/.memory_core/memory.db`（SQLite），向量在 `~/.memory_core/vectors/`
- 用环境变量 `MEMORY_CORE_DATA` 可自定义数据目录（多 agent 共享记忆时设为同一目录即可）
- **零联网、零 API key**：引擎不向任何服务器发送数据
- **隐私红线**：分拣 `settings.json` 时**只摘要 hooks/mcp 的名称**，跳过 env/command 里的 key/token/密码；写入约定也遵循"不记敏感凭据"

## 卸载

```bash
claude plugin uninstall know-you-memory --scope user
```

如需连数据一起删除：

```bash
# 谨慎：删除后不可恢复
rm -rf ~/.memory_core
```

## 开发与测试

```bash
cd know_you_memory_plugin
PYTHONPATH="engine;." python -m unittest discover -s tests -v   # 26 项，全绿
```

## 许可证

MIT © Open Quant
