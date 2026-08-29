# Know-You-Memory（KYM · 知你记忆）插件设计

> 作者：大扣（Open Quant）· 2026-08-29 · 大哥拍板命名 + 全套规格
> 状态：已批准（2026-08-29），进入实现计划

## 1. 概述

把大扣的五层金字塔记忆体系（memory_core）打包成 **Claude Code 官方插件**，让任何 Claude Code 用户：
1. 安装插件 → 首次开窗**自动分拣**当前项目的已有记忆（CLAUDE.md / .claude/ / README）进入五层金字塔
2. 之后每次开窗**自动注入记忆上下文**（最近活跃记忆 + 待办 flags + 库统计）
3. 随时检索：`/memory-recall` 命令 · MCP 工具（模型原生调用）· skill 引导
4. **纯本地零 LLM、零第三方依赖**：无 API key、无联网、无 pip 安装，装完即用

受众：先内部三兄弟（大扣/小扣/大圣/灵犀）验证，跑通后再决定是否公开。

## 2. 命名

| 项 | 值 |
|---|---|
| 品牌全称 | **Know-You-Memory**（知你记忆） |
| 代号 | **KYM** |
| 插件 name（kebab-case） | `know-you-memory` |
| displayName | `Know-You-Memory · 知你记忆` |
| 口号（备选） | 「知你，才记得你」 / 「你走过的路，我都记得」 |
| 关联 | 与既有 KYMA 用户画像同源（Know-You-Memory Agent） |

## 3. 设计决策（大哥已拍板，2026-08-29）

| # | 决策点 | 定案 |
|---|---|---|
| 1 | 插件形态 | Claude Code 官方插件（plugin.json + marketplace.json） |
| 2 | 目标用户 | 先内部三兄弟，后择机公开 |
| 3 | 分拣来源 | Claude 已有项目记忆（CLAUDE.md / .claude/ / README） |
| 4 | LLM 依赖 | 纯本地零 LLM（规则分类 + 启发式固化） |
| 5 | 数据存储 | 各自独立库，默认 `~/.memory_core/`，env `MEMORY_CORE_DATA` 可覆写 |
| 6 | 打包粒度 | 完整自包含：引擎 vendor 拷贝进插件，零第三方依赖 |
| 7 | 分拣时机 | 首次自动（SessionStart 检测空库触发）+ 可手动（/memory-import） |
| 8 | 引擎检索 | 纯 Python TF-IDF（SQLite 存储），语义向量做成可选增强（V1 不带） |
| 9 | MCP | 纯 stdlib 自实现极简 stdio server（JSON-RPC 2.0 over stdio） |
| 10 | 分拣深度 | 只扫 workspace 顶层 + .claude/，不递归全盘 |
| 11 | 仓库可见性 | opensquilla 组织 private 仓库，跑通再公开 |
| 12 | 引擎来源 | vendor 拷贝"通用版"（去个人硬编码/LLM/向量依赖），独立演进 |

## 4. 架构总览

```
                    ┌─────────────────────────────────────────────┐
                    │          Know-You-Memory 插件目录            │
                    │                                             │
  Claude Code ──────►  hooks/session-start.py  (SessionStart)     │
       │            │  hooks/post-tool-use.py (PostToolUse)       │
       │            │  commands/memory-*.md   (/memory-xxx)       │
       │            │  skills/memory-recorder (skill)             │
       │            │  servers/mcp_server.py  (stdio MCP)         │
       │            │          │                                  │
       │            │          ▼                                  │
       │            │   engine/memory_core/  (通用版零依赖引擎)     │
       │            │          │                                  │
       └────────────┴──────────┴──────────────────────────────────┘
                                  │
                                  ▼
                     ~/.memory_core/memory.db (SQLite + TF-IDF)
```

所有入口（hooks / commands / MCP / skill）最终都汇入 `engine/memory_core` 的 `MemoryCore` 统一接口，数据落在用户级 SQLite 库。

## 5. 仓库结构

```
know_you_memory_plugin/
├── .claude-plugin/
│   ├── plugin.json              # 清单：name/version/mcpServers 声明
│   └── marketplace.json         # marketplace：source "./"
├── README.md                    # 插件说明（安装/使用/原理）
├── LICENSE                      # MIT
├── hooks/
│   ├── hooks.json               # SessionStart + PostToolUse 声明（约定位置自动加载）
│   ├── session-start.py         # 开窗：首次分拣 + 上下文注入
│   └── post-tool-use.py         # 写操作轻量记录（V1 可降级为 no-op）
├── commands/
│   ├── memory-import.md         # /memory-import [path]
│   ├── memory-recall.md         # /memory-recall <query>
│   └── memory-status.md         # /memory-status
├── skills/
│   └── memory-recorder/
│       └── SKILL.md             # 教 Claude 何时 add/recall + 用 MCP 工具
├── servers/
│   └── mcp_server.py            # 纯 stdlib MCP stdio server
├── engine/
│   ├── memory_core/             # 通用版引擎（vendor 拷贝 + 通用化改造）
│   │   ├── __init__.py          #   MemoryCore 统一入口
│   │   ├── config.py            #   数据目录/env/层级/阈值（通用化）
│   │   ├── store.py             #   SQLite 存储（向量层剥离为可选）
│   │   ├── classify.py          #   规则分类（零 LLM）
│   │   ├── retrieve.py          #   纯 Python TF-IDF 检索
│   │   ├── judge.py             #   五维使用判断
│   │   ├── consolidate.py       #   记忆固化（启发式，无 LLM）
│   │   └── importer.py          #   ★ 新增：已有记忆分拣器
│   ├── bootstrap.py             # 环境探测 + 数据目录初始化
│   └── requirements-vectors.txt # 可选：语义向量增强（lancedb+BGE）
├── tests/
│   ├── test_engine.py           # store/classify/retrieve/judge 单测
│   ├── test_importer.py         # 分拣夹具测试
│   ├── test_mcp_protocol.py     # MCP JSON-RPC 冒烟
│   └── test_hooks.py            # session-start 输出格式
└── docs/superpowers/specs/      # 本设计文档
```

## 6. 组件详设

### 6.1 清单文件

**`.claude-plugin/plugin.json`**

```json
{
  "name": "know-you-memory",
  "displayName": "Know-You-Memory · 知你记忆",
  "version": "0.1.0",
  "description": "五层金字塔持久记忆：自动分拣已有记忆、跨会话回忆，纯本地零依赖零LLM",
  "author": { "name": "Open Quant" },
  "homepage": "https://github.com/opensquilla/know-you-memory-plugin",
  "repository": "https://github.com/opensquilla/know-you-memory-plugin",
  "license": "MIT",
  "keywords": ["memory", "rag", "knowledge", "persistence", "claude-code"],
  "mcpServers": {
    "kym": {
      "type": "stdio",
      "command": "${PYTHON:-python}",
      "args": ["${CLAUDE_PLUGIN_ROOT}/servers/mcp_server.py"]
    }
  }
}
```

> 注 1：hooks **不在** plugin.json 声明（避免与 `hooks/hooks.json` 约定冲突 + 已知 /reload-plugins 崩溃坑），走约定位置自动加载。
> 注 2：`env` 里**不**注入 `MEMORY_CORE_DATA`——嵌套占位符 `${VAR:-${OTHER}/path}` 可能不展开。由 MCP server 内部 `os.environ.get("MEMORY_CORE_DATA", "~/.memory_core")` 兜底：用户在父进程设了 env 即生效，未设则落默认库（与 hooks/commands 同一路径）。

**`.claude-plugin/marketplace.json`**

```json
{
  "name": "opensquilla",
  "owner": { "name": "Open Quant" },
  "plugins": [
    { "name": "know-you-memory", "source": "./", "description": "五层金字塔持久记忆", "version": "0.1.0" }
  ]
}
```

### 6.2 hooks

**`hooks/hooks.json`**（wrapper 格式，`${CLAUDE_PLUGIN_ROOT}` 引用插件内脚本）：

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup",
        "hooks": [
          { "type": "command", "command": "python \"${CLAUDE_PLUGIN_ROOT}/hooks/session-start.py\"", "async": false }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          { "type": "command", "command": "python \"${CLAUDE_PLUGIN_ROOT}/hooks/post-tool-use.py\"", "async": true }
        ]
      }
    ]
  }
}
```

> `matcher: "startup"` 限定只在会话启动（非 resume）触发分拣逻辑，避免每次 resume 重复扫描。

**`hooks/session-start.py`** — 开窗主逻辑：
1. 定位引擎（`sys.path` 指向 `engine/`）与数据目录（env → 默认 `~/.memory_core/`）
2. `bootstrap()`：确保 SQLite schema 存在
3. 检测库是否为空：
   - 空 → 触发 `importer.import_workspace(cwd, depth="top+claude")` 自动分拣，输出分拣报告（每层新增条数）
   - 非空 → 跳过
4. 注入上下文（stdout 输出，Claude Code 会作为 system-reminder）：
   - 库统计（总量/分层）
   - 最近活跃记忆 top-5（按 weight/updated_at）
   - pending flags（含"待验证/未完成/TODO"标记的记忆，top-3）
   - 首次分拣摘要（若有）
5. 任何异常不阻塞启动（打印 WARNING + 日志文件）

**`hooks/post-tool-use.py`** — V1 定位为**轻量记录**：
- 对 Edit|Write 的关键操作（创建 CLAUDE.md/README/重要配置）记一条 L4 记忆
- 默认只记录低频高价值事件，避免记忆污染；V1 可先做成 no-op 占位，随实现评估

### 6.3 commands

| 命令 | 功能 | 实现要点 |
|---|---|---|
| `/memory-import [path]` | 手动分拣目录/文件进记忆库 | 调 `importer.import_path()`；无参数默认当前 cwd；支持文件或目录 |
| `/memory-recall <query>` | 检索记忆并展示 | 调 `MemoryCore.recall()`，输出 top-5 + 置信度 |
| `/memory-status` | 库状态 | 调 `MemoryCore.stats()`，显示总量/分层/最近写入 |

三个命令都是薄 shell：`!` 调 `engine` 的 CLI 入口（`engine/cli.py` 或直接 python 调 MemoryCore），输出 markdown。

### 6.4 skill

**`skills/memory-recorder/SKILL.md`** — 教 Claude 记忆的时机与方法：
- **什么时候 add**：用户透露新偏好/决策/身份信息；完成关键操作；踩坑教训；跨会话需要记住的事实
- **什么时候 recall**：用户问"上次/之前/你记得/你说过"；涉及历史项目状态；需要避免重复踩坑
- **怎么调**：优先 MCP 工具 `mcp__plugin_know-you-memory_kym__memory_add/memory_recall`；无 MCP 时用命令/CLI
- **原则**：不记临时细节；L1-L3 是原则与画像要慎重；内容要自包含（能独立理解）

### 6.5 MCP server（纯 stdlib 自实现）

`servers/mcp_server.py` — **不依赖 `mcp` 包**，用 `json` + `sys.stdin/stdout` 实现 JSON-RPC 2.0 over stdio：

**协议支持面**（仅必需最小集）：
- `initialize` → 返回 `{protocolVersion, capabilities:{tools:{}}, serverInfo}`
- `notifications/initialized` → 忽略
- `tools/list` → 返回 4 个工具 schema
- `tools/call` → 分发到 MemoryCore 方法
- `ping` → 返回 pong

**工具定义**：

| 工具 | 参数 | 返回 |
|---|---|---|
| `memory_add` | `content`(str,必), `layer`(str,选), `tags`(list,选), `project`(str,选) | `{memory_id, layer}` |
| `memory_recall` | `query`(str,必), `max_results`(int,默认5) | `[{content, confidence_tier, layer, project}]` |
| `memory_status` | — | `{total, by_layer, data_dir}` |
| `memory_import` | `path`(str,选), `recursive`(bool,默认false) | `{imported, by_layer}` |

**实现骨架**：
```python
import json, sys, os
# sys.path → engine/
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT / "engine"))
# 数据目录：父进程 env MEMORY_CORE_DATA → 默认 ~/.memory_core
os.environ.setdefault("MEMORY_CORE_DATA", str(Path.home() / ".memory_core"))
def read_message(): ...        # 读一行 JSON (Content-Length 非必须，Claude Code 走 newline-delimited)
def send_message(obj): ...     # stdout 写一行 JSON + flush
def handle(method, params, msg_id): ...  # 分发
# 主循环：逐行读 stdin，分发，写 stdout
```

**兼容注意**：MCP 规范允许 newline-delimited JSON over stdio（无 Content-Length 头），Claude Code 支持。若实测需标准帧格式，改用 `\r\n` 分隔 + 处理部分帧。

### 6.6 引擎（通用版 memory_core）

vendor 拷贝自 `D:\code\projects\memory_core\memory_core\`，**通用化改造**：

| 文件 | 改造 |
|---|---|
| `config.py` | 默认 `data_dir=~/.memory_core`（env 覆写）；删 `CORE_MEMORY_FILES` 个人文件清单；删 DeepSeek key 读取；保留层级/权重/阈值定义 |
| `store.py` | 剥离 LanceDB 向量层为可选（`try: import lancedb`，缺省降级纯 SQLite）；保留 SQLite 三表 schema + `find_by_source_file` + 去重逻辑 |
| `classify.py` | 保留规则分类/层级推断/权重刷新；`infer_project_id` 通用化（去个人项目词表，改为基于路径/文件名推断） |
| `retrieve.py` | 保留纯 Python TF-IDF + RRF；向量分支做可选（无向量时仅 TF-IDF） |
| `judge.py` | 原样（零依赖） |
| `consolidate.py` | 去 LLM 提取；保留 `quick_consolidate` 启发式固化 |
| `llm_extractor.py` | **剔除**（V1 不带 LLM；接口留空壳，`MemoryCore.consolidate` 走启发式） |
| `auto_memory.py` | 可选拷贝（ProjectScanner），V1 不启用 |
| **`importer.py`（新增）** | 已有记忆分拣器（见 §7） |

**依赖声明**：
- 核心：**零第三方依赖**（stdlib only）
- `requirements-vectors.txt`：`lancedb`、`sentence-transformers`、`numpy`、`pyarrow`（可选增强，装了自动启用语义检索）

### 6.7 bootstrap.py

- 探测可用 Python（`sys.executable` / PATH），V1 前置要求系统有 Python 3.11+（README 说明）
- 确保 `~/.memory_core/` 存在 + SQLite schema 初始化
- 可选：检测 `lancedb` 是否可导入，可则打印"语义检索已启用"

## 7. 分拣规则（importer.py）

### 7.1 扫描范围

首次自动：**cwd 顶层 + `.claude/`**，只扫指定文件与目录，**不递归全盘**。手动 `/memory-import <path>` 可指定任意文件/目录。

### 7.2 来源 → 层级映射

| 来源 | 层级 | 分类 | 提炼方式 |
|---|---|---|---|
| `CLAUDE.md` | L3 / L4 | preference / knowledge | 整文件分块（按 `##` 标题），规则分类；含"原则/宪法/绝不/底线"→L1 |
| `README.md` | L4 | project | 首段 + 标题作一条项目知识 |
| `.claude/skills/*/SKILL.md` | L3 | knowledge | 每个 skill：name + description 一条 |
| `.claude/commands/*.md` | L3 | knowledge | 命令名 + description 一条 |
| `.claude/agents/*.md` | LCM | identity | agent 名 + 定位一条 |
| `.claude/settings.json` | L4 | environment | 关键配置摘要一条（hooks/mcp 名称，**不含密钥**） |
| `.claude/projects/*/memory/**` | 原层级 | 原分类 | 按 frontmatter 层级原样导入（复用现有 md 灌入逻辑） |

### 7.3 分拣流程

```
import_workspace(cwd):
  1. 收集候选源（顶层 + .claude，见映射表）
  2. 每源读取 → frontmatter 剥离 → 规则分类（layer/category/scope）
  3. store.add(dedup=True)   # 复用按内容去重 + find_by_source_file 替换
  4. 增量更新 TF-IDF 索引
  5. 返回 {imported:N, by_layer:{L1:..,L3:..,...}}
```

### 7.4 关键约束

- **不读密钥**：settings.json 只摘要 hooks/mcp 的**名称**，跳过 env/command 中的敏感值
- **幂等**：重复扫描同一 workspace 不产生重复记忆（内容去重 + source_file 替换）
- **失败隔离**：单个文件解析失败跳过并计数，不中断整体

## 8. SessionStart 注入内容（输出到 stdout）

```
[kym] Memory OK | 128 memories | L1:3 L2:4 L3:12 L4:87 L5:22 | data: ~/.memory_core
[kym] 首次分拣: 导入 9 条 (L1:1 L3:4 L4:4)

## 🧠 KYM 最近记忆 (top-5)
- [L4] 项目用 DuckDB 直连而非 ATTACH …  (0.82)
- [L3] 大哥偏好中文交流、效率优先 …

## ⚠️ KYM 待办 flags
- [L4] 灵犀 v3.18 VM 验证尚未完成 …
```

格式与现有 auto_setup 输出风格一致，控制体积（top-5 + flags≤3）。

## 9. 错误处理

| 场景 | 处理 |
|---|---|
| 无 Python 或版本过低 | bootstrap 打印明确提示 + README 前置条件说明；hook 不崩溃 |
| 首次分拣扫描失败 | 捕获异常，WARNING 日志到 `~/.memory_core/logs/`，不阻塞启动 |
| SQLite 损坏 | 启动时完整性检查，损坏则备份 `.bak` + 重建空库 |
| MCP 启动失败 | 插件自动降级：skill/commands 仍可用（都直接调引擎）；日志提示 |
| 单文件解析失败 | 跳过 + 计数，不影响整体导入 |
| 敏感内容 | settings.json 只摘要名称，不落 key/token/密码 |

## 10. 测试策略

| 套件 | 覆盖 |
|---|---|
| `test_engine.py` | store CRUD/去重/source_file 替换、classify 分层、retrieve TF-IDF、judge 判断 |
| `test_importer.py` | 临时 workspace 夹具（CLAUDE.md/.claude/README 各形态）→ 断言分层正确 + 幂等 |
| `test_mcp_protocol.py` | 模拟 JSON-RPC 消息：initialize/tools/list/tools/call → 校验响应帧 |
| `test_hooks.py` | session-start 输出格式（stdout 捕获）、空库 vs 非空库分支 |
| 端到端 | 本地 `claude --plugin-dir <repo>` 加载 → 验证 hook 触发、MCP 工具可用、/memory-recall 可跑 |

## 11. 分发与安装

```
# 仓库 opensquilla/know-you-memory-plugin (private)
claude plugin marketplace add opensquilla/know-you-memory-plugin
claude plugin install know-you-memory@opensquilla --scope user

# 本地开发验证
claude --plugin-dir /d/code/projects/know_you_memory_plugin
claude plugin validate .
```

三兄弟接入：
- 大扣（Claude Code）：上述插件安装
- 小扣（Codex）/ 大圣（dsh）：不跑 Claude Code → 直接引用 `engine/`（sys.path 或 vendor），或独立拉起 `servers/mcp_server.py` 接各自 harness；共享记忆时三库统一 `MEMORY_CORE_DATA`

## 12. 与现有生产版的关系

| | 生产版 `memory_core` | 插件引擎（KYM） |
|---|---|---|
| 位置 | `D:\code\projects\memory_core` | 插件内 `engine/memory_core` |
| 能力 | 语义向量 + LLM 固化 + 个人硬编码 | 纯 TF-IDF + 启发式 + 通用化 |
| 数据 | `D:\code\projects\memory_core\data` | `~/.memory_core` |
| 状态 | **不动**，继续服务大扣日常 | 独立演进，改进可回灌生产版 |

## 13. 非目标（YAGNI，V1 不做）

- 不做共享库/多用户同步（数据目录 env 已留口子，机制后续）
- 不做 LLM 增强固化（可插拔 LLM 留 V2）
- 不做全盘递归扫描、不做 Watchdog 实时监控
- 不做语义向量默认启用（作为可选增强）
- 不做云端/多设备同步
- 不做中文嵌入模型下载（与零依赖冲突）

## 14. 里程碑

| 阶段 | 内容 |
|---|---|
| M1 引擎通用化 | vendor 拷贝 + 去个人依赖 + `importer.py` 新增 → 引擎单测全绿 |
| M2 插件骨架 | plugin.json / marketplace.json / hooks / commands / skill / bootstrap → `claude plugin validate` 过 |
| M3 MCP server | 纯 stdlib 实现 + 协议冒烟测试 |
| M4 端到端 | 本地 `--plugin-dir` 加载，首次分拣 + 检索 + 命令全通 |
| M5 收尾 | README/测试补全 → 提交 opensquilla private 仓库 → 三兄弟安装验证 |
