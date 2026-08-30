# 🧠 Know-You-Memory (KYM)

> **English | [中文](README.zh-CN.md)**

> **Give every AI assistant a memory of who you are.** A five-layer pyramid persistent-memory plugin for Claude Code — zero-dependency · zero-LLM · 100% local.

A drop-in **Claude Code plugin**: once installed, your AI remembers your preferences, principles, and working knowledge — **recalling them across sessions** — while your data stays entirely on your own machine.

**No API keys · No cloud · No third-party dependencies · Works out of the box.**

---

## ✨ Why install it

| Without KYM | With KYM |
|---|---|
| AI forgets who you are every new session | Auto-injects recent memories + todo flags on every window |
| You re-explain preferences & hard-won lessons endlessly | Five-layer pyramid auto-classifies — remembered once |
| Memories scattered & unsearchable | `/memory-recall` instant retrieval with confidence tiers |
| Cloud memory, privacy worries | 100% local SQLite, zero network |

## 🧠 The five-layer pyramid

| Layer | What it stores |
|---|---|
| L0 | Credentials (secrets — excluded by default) |
| L1 | Core principles · boundaries |
| L2 | User profile · identity |
| L3 | Behavioral preferences |
| L4 | Working knowledge · projects · pitfalls · research |
| L5 | Relationship memory |
| LCM | Agent capability self-portrait |

## 🚀 Quick start (3 steps, 30 seconds)

```bash
# 1. Add the marketplace
claude plugin marketplace add dashan-qi/know-you-memory-plugin

# 2. Install the plugin
claude plugin install know-you-memory@openquant --scope user

# 3. Restart your session and go
```

**First window auto-import**: scans your `CLAUDE.md` / `README` / `.claude/` and files existing memory into the pyramid (idempotent — only updates when content changes, never duplicates). Every window after that auto-injects:

```
[kym] Memory OK | 171 memories | data: ...
## 🧠 KYM Recent Memories (top-5)
## ⚠️ KYM Todo Flags
```

## 🎯 Usage

| Method | What it does |
|---|---|
| `/memory-recall <query>` | Retrieve related memories with confidence & layer |
| `/memory-import [path]` | Manually import existing docs |
| `/memory-status` | View memory stats / layers / data location |
| **MCP tools** | Model-native `memory_add` / `memory_recall` / `memory_status` / `memory_import` |
| `python engine/cli.py inspect` | Memory health inspection (layers / duplicates / low-weight / source completeness) |

## 🔒 Data & privacy

- Data stays local: `~/.memory_core/memory.db` (SQLite), optional vectors in `~/.memory_core/vectors/`
- **Zero network, zero API keys, zero third-party Python dependencies** (pure standard library)
- Privacy red line: credentials / passwords / tokens are **never stored**; `settings.json` import only summarizes names, skipping env keys
- Multiple agents sharing memory: point `MEMORY_CORE_DATA` to the same directory

## 🧩 Optional: semantic vector retrieval

Default pure-Python TF-IDF retrieval — instant, zero-dependency. For stronger semantic recall (BGE Chinese embeddings + LanceDB):

```bash
pip install -r engine/requirements-vectors.txt
export MEMORY_CORE_VECTORS=1
```

## 📦 Uninstall

```bash
claude plugin uninstall know-you-memory --scope user
```

To remove data too: `rm -rf ~/.memory_core` (careful — irreversible).

## 🧰 For developers

- **Engine decoupled from the plugin**: `engine/` + MCP server are pure stdlib — consumable by **non-Claude-Code environments (Codex / dsh / any Python)**, see `docs/integration-codex-dsh.md`
- Zero-dependency core: SQLite + pure-Python TF-IDF + rule-based classification + heuristic consolidation
- Tests: `PYTHONPATH="engine;." python -m unittest discover -s tests -v` (33/33 ✅)

## 📄 License

MIT © Open Quant

---

**Like it? Give it a ⭐ to help more AIs keep good memories. Feedback & PRs welcome via [Issues](https://github.com/dashan-qi/know-you-memory-plugin/issues).**
