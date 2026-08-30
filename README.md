# 🧠 Know-You-Memory · 知你记忆 (KYM)

> **让每个 AI 助手记得你是谁。** 五层金字塔持久记忆 Claude Code 插件 —— 零依赖 · 零 LLM · 纯本地。

一个开箱即用的 **Claude Code 插件**：装上后，AI 自动记住你的偏好、原则、工作知识，**跨会话持续回忆**，数据 100% 留在你自己的电脑上。

**没有 API key · 没有云端 · 没有第三方依赖 · 装完即用。**

---

## ✨ 为什么装它

| 没有 KYM | 有了 KYM |
|---|---|
| 每次开新对话，AI 不记得你是谁 | 开窗自动注入「最近记忆 + 待办 flags」 |
| 偏好、踩过的坑反复交代 | 五层金字塔自动分层，一次记住 |
| 记忆散落各处、无法检索 | `/memory-recall` 秒级检索，标置信度 |
| 云端记忆，隐私担忧 | 纯本地 SQLite，零联网 |

## 🧠 五层金字塔

| 层 | 存什么 |
|---|---|
| L0 | 凭据（密钥，默认不入库） |
| L1 | 最高原则 · 底线 |
| L2 | 用户画像 · 身份 |
| L3 | 行为偏好 |
| L4 | 工作知识 · 项目 · 踩坑 · 研究 |
| L5 | 关系记忆 |
| LCM | Agent 能力自画像 |

## 🚀 快速开始（3 步，30 秒）

```bash
# 1. 添加 marketplace
claude plugin marketplace add dashan-qi/know-you-memory-plugin

# 2. 安装插件
claude plugin install know-you-memory@openquant --scope user

# 3. 重启会话，开聊
```

**首次开窗自动分拣**：扫描你的 `CLAUDE.md` / `README` / `.claude/`，把已有记忆分拣进五层金字塔（幂等：变了才更新，绝不堆叠）。之后每次开窗自动注入：

```
[kym] Memory OK | 171 memories | data: ...
## 🧠 KYM 最近记忆 (top-5)
## ⚠️ KYM 待办 flags
```

## 🎯 怎么用

| 方式 | 作用 |
|---|---|
| `/memory-recall <查询>` | 检索相关记忆，标注置信度与层级 |
| `/memory-import [路径]` | 手动分拣目录/文件的已有记忆 |
| `/memory-status` | 查看记忆库总量 / 分层 / 数据位置 |
| **MCP 工具** | 模型原生调用 `memory_add` / `memory_recall` / `memory_status` / `memory_import` |
| `python engine/cli.py inspect` | 记忆健康巡检（分层/重复/低权重/来源完整度） |

## 🔒 数据与隐私

- 数据全本地：`~/.memory_core/memory.db`（SQLite），可选向量目录 `~/.memory_core/vectors/`
- **零联网、零 API key、零第三方 Python 依赖**（纯标准库实现）
- 隐私红线：凭据 / 密码 / Token **永不入库**；分拣 `settings.json` 只摘要名称，跳过 env 里的 key
- 多 agent 共享记忆：设环境变量 `MEMORY_CORE_DATA` 指向同一目录即可

## 🧩 可选增强：语义向量检索

默认纯 Python TF-IDF 检索，秒级返回、零依赖。想要更强的语义召回（BGE 中文嵌入 + LanceDB）：

```bash
pip install -r engine/requirements-vectors.txt
export MEMORY_CORE_VECTORS=1
```

## 📦 卸载

```bash
claude plugin uninstall know-you-memory --scope user
```

如需连数据一起删除：`rm -rf ~/.memory_core`（谨慎，不可恢复）。

## 🧰 为开发者

- **引擎与插件分离**：`engine/` + MCP server 都是纯 stdlib，**非 Claude Code 环境（Codex / dsh / 任意 Python）也能直接消费**，见 `docs/integration-codex-dsh.md`
- 零依赖内核：SQLite + 纯 Python TF-IDF + 规则分类 + 启发式固化
- 测试：`PYTHONPATH="engine;." python -m unittest discover -s tests -v`（33/33 ✅）

## 📄 License

MIT © Open Quant

---

**喜欢？点个 ⭐ 让更多 AI 拥有好记性。欢迎 [Issues](https://github.com/dashan-qi/know-you-memory-plugin/issues) 反馈与 PR。**
