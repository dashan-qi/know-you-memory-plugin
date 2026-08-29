# Know-You-Memory（KYM）插件实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把五层金字塔记忆体系打包成零第三方依赖、纯本地零 LLM 的 Claude Code 官方插件，装后首次开窗自动分拣已有记忆，之后跨会话回忆。

**Architecture:** 插件 = Claude Code 插件外壳（plugin.json + hooks + commands + skill + MCP）包裹一个 vendor 拷贝的"通用版" memory_core 引擎。引擎只用 Python stdlib（sqlite3 存储 + 纯 Python TF-IDF 检索 + 规则分类 + 启发式固化），数据落在用户级 `~/.memory_core/memory.db`。所有入口（hooks/MCP/commands/skill）最终汇入 `MemoryCore` 统一接口。

**Tech Stack:** Python ≥3.11（仅标准库：sqlite3/json/re/hashlib/math/pathlib）、Claude Code 插件格式、JSON-RPC 2.0 over stdio（自实现 MCP）、stdlib `unittest`（测试也零依赖）。

**Spec:** `docs/superpowers/specs/2026-08-29-know-you-memory-plugin-design.md`（本计划据其实现，执行者需同时读 spec 与计划）

## Global Constraints

- Python ≥ 3.11，核心引擎**零第三方依赖**（stdlib only）；语义向量（lancedb+sentence-transformers）仅存于 `requirements-vectors.txt` 声明，不 import 进核心路径
- 插件 name 固定 `know-you-memory`（kebab-case）；MCP server 名固定 `kym`；工具名 `mcp__plugin_know-you-memory_kym__*`
- 数据目录：`os.environ.get("MEMORY_CORE_DATA", str(Path.home()/".memory_core"))` 兜底，插件内所有脚本同一逻辑
- hooks 声明只放 `hooks/hooks.json`（约定位置自动加载），**不得**在 plugin.json 声明 `hooks` 字段（规避冲突 + /reload-plugins 崩溃坑）
- 首次分拣只扫 **cwd 顶层 + `.claude/`**，不递归全盘；手动 import 可指定任意路径
- 不引入 `llm_extractor.py`；`consolidate` 走启发式（`HeuristicExtractor`）
- 敏感内容禁止入库：settings.json 只摘要 hooks/mcp 的**名称**，跳过 env/command 中的 key/token/密码
- 所有测试用 stdlib `unittest`（无需 pytest），**一律以 `PYTHONPATH="engine;."` 运行**（Windows 多路径分隔符是 `;`；`engine` 供 `memory_core` 包，`.` 供 `hooks/`/`engine` 包，缺任一会 `ModuleNotFoundError`）
- 引擎所有路径用 `pathlib.Path`，不写死盘符

---

### Task 1: 引擎骨架拷贝 + config.py 通用化

**Files:**
- Create: `engine/memory_core/`（整体拷贝自 `D:\code\projects\memory_core\memory_core\` 的 `__init__.py`、`config.py`、`store.py`、`classify.py`、`retrieve.py`、`judge.py`、`consolidate.py`）
- Modify: `engine/memory_core/config.py`（通用化）
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: 无（首批）
- Produces:
  - `memory_core.config.MemoryCoreConfig(data_dir: Path)` — 运行时配置
  - `memory_core.config.DEFAULT_DATA_DIR` — `~/.memory_core`（env `MEMORY_CORE_DATA` 覆写）
  - `memory_core.config.LAYERS` = `["L0","L1","L2","L3","L4","L5","LCM"]`

- [ ] **Step 1: 拷贝引擎目录**

```bash
mkdir -p engine
cp -r /d/code/projects/memory_core/memory_core engine/
rm -f engine/memory_core/llm_extractor.py   # 剔除 LLM 依赖
rm -f engine/memory_core/auto_memory.py     # V1 不启用
# 删掉 __pycache__ / .egg-info
```

- [ ] **Step 2: 写失败测试**

```python
# tests/test_config.py
import os, tempfile, unittest
from pathlib import Path

class TestConfig(unittest.TestCase):
    def test_default_data_dir_env_override(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["MEMORY_CORE_DATA"] = d
            import importlib, memory_core.config as c
            importlib.reload(c)
            self.assertEqual(c.DEFAULT_DATA_DIR, Path(d))
            del os.environ["MEMORY_CORE_DATA"]
            importlib.reload(c)

    def test_core_memory_files_removed(self):
        import memory_core.config as c
        self.assertFalse(hasattr(c, "CORE_MEMORY_FILES"))  # 个人清单已剔除

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 运行确认失败**

Run: `cd /d/code/projects/know_you_memory_plugin && PYTHONPATH="engine;." python -m unittest tests.test_config -v`
Expected: 失败（`DEFAULT_DATA_DIR` 仍是旧 `Path.home()/".memory_core"` 外的值，且 `CORE_MEMORY_FILES` 仍存在）

- [ ] **Step 4: 通用化 config.py**

在 `engine/memory_core/config.py` 顶部，改为：
```python
DEFAULT_DATA_DIR = Path(os.environ.get(
    "MEMORY_CORE_DATA", str(Path.home() / ".memory_core")
))
```
并**删除**以下整块：`CLAUDE_PROJECT_SLUG`、`CLAUDE_PROJECTS_DIR`、`TRANSCRIPTS_DIR`、`MEMORY_DIR`、`L5_DIR`、`SESSIONS_DIR`、`PROJECT_ROOT`、`PROJECT_DATA_DIR`（路径全通用化）、`CORE_MEMORY_FILES`（个人文件清单）、`DEEPSEEK_API_KEY`/`DEEPSEEK_MODEL`/`LLM_EXTRACTOR_TIMEOUT`（LLM 配置）。保留 LAYERS/CATEGORIES/SCOPES/权重/衰减/阈值/检索配置。

- [ ] **Step 5: 运行测试确认通过**

Run: `cd /d/code/projects/know_you_memory_plugin && PYTHONPATH="engine;." python -m unittest tests.test_config -v`
Expected: PASS（2 项）

- [ ] **Step 6: Commit**

```bash
git add engine/memory_core tests/test_config.py
git commit -m "feat(kym): 引擎骨架拷贝 + config 通用化（env 数据目录、去个人清单/LLM 配置）"
```

---

### Task 2: store.py 向量层剥离为可选 + SQLite 核心验证

**Files:**
- Modify: `engine/memory_core/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `MemoryCoreConfig`（Task 1）
- Produces:
  - `memory_core.store.SQLiteManager(db_path: Path)` — `insert(entry)->id`、`get(id)->MemoryEntry|None`、`find_by_content(content)->MemoryEntry|None`、`find_by_source_file(source_file)->list[MemoryEntry]`、`list_by_layer(layer)`、`count()`
  - `memory_core.store.MemoryStore(config)` — 统一存储门面，含 `lancedb` 属性（可空）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_store.py
import tempfile, unittest
from pathlib import Path
from memory_core import MemoryCoreConfig
from memory_core.store import MemoryStore, MemoryEntry

class TestStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = MemoryCoreConfig(data_dir=Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_add_and_get(self):
        s = MemoryStore(self.cfg)
        mid = s.add(content="测试记忆内容", layer="L4", category="knowledge",
                    scope="project", project_id="demo", tags=["test"])
        got = s.get(mid)
        self.assertEqual(got.content, "测试记忆内容")
        self.assertEqual(got.layer, "L4")

    def test_find_by_source_file_replace(self):
        s = MemoryStore(self.cfg)
        s.add(content="# A\n旧内容", layer="L4", category="knowledge",
              scope="project", source_file="CLAUDE.md")
        # 同 source_file 再次灌入 → 替换而非追加
        s.add(content="# A\n新内容", layer="L4", category="knowledge",
              scope="project", source_file="CLAUDE.md")
        rows = s.find_by_source_file("CLAUDE.md")
        self.assertEqual(len(rows), 1)
        self.assertIn("新内容", rows[0].content)

    def test_content_dedup(self):
        s = MemoryStore(self.cfg)
        a = s.add(content="完全相同的句子", layer="L4", category="knowledge",
                  scope="project", dedup=True)
        b = s.add(content="完全相同的句子", layer="L4", category="knowledge",
                  scope="project", dedup=True)
        self.assertEqual(a, b)  # 重复返回同一 id

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /d/code/projects/know_you_memory_plugin && PYTHONPATH="engine;." python -m unittest tests.test_store -v`
Expected: 失败（`import lancedb` 崩溃，或 `find_by_source_file` 缺失）

- [ ] **Step 3: 改造 store.py 向量层为可选**

在 `engine/memory_core/store.py` 顶部，把向量导入包进 try/except：
```python
try:
    import lancedb
    import numpy as np
    from lancedb.table import Table as LanceTable
    _VECTOR_AVAILABLE = True
except ImportError:
    _VECTOR_AVAILABLE = False
```
- `MemoryStore.__init__` 中：`self.lancedb = LanceDBManager(self.config) if _VECTOR_AVAILABLE else None`
- `LanceDBManager` 类保留（可选增强路径），但所有被调用的方法在 `self.lancedb is None` 时走降级（返回空结果/跳过），不抛异常
- `store.add(..., source_file=None)` 新增 `source_file` 参数透传到 `MemoryEntry`（importer 用）；`SQLiteManager.insert` 的 INSERT 语句需含 `source_file` 列（生产版 schema 已有则不动）
- 确认 `find_by_source_file` 存在（08-29 生产版已加，若无则补：`SELECT * FROM memories WHERE source_file=? AND status='active'`）

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /d/code/projects/know_you_memory_plugin && PYTHONPATH="engine;." python -m unittest tests.test_store -v`
Expected: PASS（3 项）

- [ ] **Step 5: Commit**

```bash
git add engine/memory_core/store.py tests/test_store.py
git commit -m "feat(kym): store 向量层剥离为可选，SQLite 核心 + source_file 替换验证"
```

---

### Task 3: classify.py 通用化

**Files:**
- Modify: `engine/memory_core/classify.py`
- Test: `tests/test_classify.py`

**Interfaces:**
- Consumes: `MemoryEntry`（Task 2）
- Produces:
  - `classify_content(content: str) -> dict` — `{"layer","category","scope"}`
  - `infer_project_id(content: str, current_project: str | None = None) -> str | None`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_classify.py
import unittest
from memory_core.classify import classify_content, infer_project_id

class TestClassify(unittest.TestCase):
    def test_principle_goes_l1(self):
        r = classify_content("核心原则：绝不删除用户数据，任何删除必须进回收站")
        self.assertEqual(r["layer"], "L1")
        self.assertEqual(r["category"], "principle")

    def test_preference_goes_l3(self):
        r = classify_content("偏好：中文交流，效率优先，测试产物放项目目录")
        self.assertEqual(r["layer"], "L3")

    def test_generic_knowledge_goes_l4(self):
        r = classify_content("DuckDB 直连模式性能优于 ATTACH")
        self.assertEqual(r["layer"], "L4")

    def test_project_id_inferred_from_path_word(self):
        pid = infer_project_id("这个 factor_agent 的端口是 8501")
        self.assertIn("factor", pid or "")

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /d/code/projects/know_you_memory_plugin && PYTHONPATH="engine;." python -m unittest tests.test_classify -v`
Expected: 失败（`classify_content` 引用了已删除的个人项目词表或 import 错误）

- [ ] **Step 3: 通用化 classify.py**

- `infer_project_id`：生产版已带 `current_project` 参数，改为通用化——从内容中提取像项目名的 token（驼峰/蛇形/短横线标识符），不再依赖硬编码 `_PROJECT_SIGNALS` 表；无匹配返回 `None`
- `classify_content` 的 layer 推断规则**保留**（关键词：原则/宪法/绝不/底线→L1，偏好/习惯/喜欢/风格/不要→L3，其余→L4 兜底）
- 若 classify.py 顶部 import 了 `llm_extractor` 或已删配置，一并清理

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /d/code/projects/know_you_memory_plugin && PYTHONPATH="engine;." python -m unittest tests.test_classify -v`
Expected: PASS（4 项）

- [ ] **Step 5: Commit**

```bash
git add engine/memory_core/classify.py tests/test_classify.py
git commit -m "feat(kym): classify 通用化（项目推断去个人词表，层级规则保留）"
```

---

### Task 4: retrieve.py + judge.py 纯 TF-IDF 检索验证

**Files:**
- Modify: `engine/memory_core/retrieve.py`
- Test: `tests/test_retrieve.py`

**Interfaces:**
- Consumes: `MemoryStore`（Task 2）
- Produces:
  - `TFIDFRetriever` — `add(memory_id, content)` / `search(query, top_k=20) -> list[(id, score)]` / `rebuild(documents)`
  - `MemoryRetriever(store)` — `retrieve(query, project_id=None, max_injected=20) -> list[MemoryEntry]`、`add_to_index(memory_id, content)`、`build_index()`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_retrieve.py
import tempfile, unittest
from pathlib import Path
from memory_core import MemoryCoreConfig
from memory_core.store import MemoryStore
from memory_core.retrieve import MemoryRetriever

class TestRetrieve(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(MemoryCoreConfig(data_dir=Path(self._tmp.name)))
        self.retriever = MemoryRetriever(self.store)

    def tearDown(self):
        self._tmp.cleanup()

    def test_tfidf_recall_relevant(self):
        a = self.store.add(content="DuckDB ATTACH 模式日期类型不兼容，改直连", layer="L4",
                           category="pitfall", scope="project", project_id="factor-agent")
        self.store.add(content="红利策略用分红率筛选股票", layer="L4", category="project",
                       scope="project", project_id="dividend")
        self.retriever.add_to_index(a, "DuckDB ATTACH 模式日期类型不兼容，改直连")
        hits = self.retriever.retrieve("DuckDB 日期问题怎么修", max_injected=5)
        self.assertTrue(any("DuckDB" in e.content for e in hits))

    def test_add_to_index_incremental(self):
        mid = self.store.add(content="窗口传参要用空格分隔", layer="L4", category="pitfall",
                             scope="project")
        self.retriever.add_to_index(mid, "窗口传参要用空格分隔")  # 不抛异常
        hits = self.retriever.retrieve("窗口传参", max_injected=5)
        self.assertEqual(len(hits), 1)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /d/code/projects/know_you_memory_plugin && PYTHONPATH="engine;." python -m unittest tests.test_retrieve -v`
Expected: 失败（`MemoryRetriever.retrieve` 引用了 lancedb 分支，或 `add_to_index` 缺失）

- [ ] **Step 3: 改造 retrieve.py 向量分支为可选**

- `MemoryRetriever.retrieve`：向量召回段包在 `if self.store.lancedb is not None:` 内，否则纯 TF-IDF（`TFIDFRetriever.search` 已有）→ RRF 融合（单路时直接返回 TF-IDF 结果）
- `add_to_index` / `build_index` / `remove_from_index`：保持，仅操作 TF-IDF；有向量时同时维护向量
- 确认无 `import llm_extractor` / 已删模块引用

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /d/code/projects/know_you_memory_plugin && PYTHONPATH="engine;." python -m unittest tests.test_retrieve -v`
Expected: PASS（2 项）

- [ ] **Step 5: Commit**

```bash
git add engine/memory_core/retrieve.py tests/test_retrieve.py
git commit -m "feat(kym): retrieve 纯 TF-IDF 路径验证，向量分支可选"
```

---

### Task 5: consolidate 启发式固化 + __init__.py 统一入口

**Files:**
- Modify: `engine/memory_core/consolidate.py`、`engine/memory_core/__init__.py`
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes: `MemoryStore`/`MemoryRetriever`/`MemoryJudge`（Task 2/4）
- Produces:
  - `memory_core.MemoryCore(data_dir: Path)` — 统一入口
    - `add(content, *, layer, category, scope, project, tags, source_file, dedup=True) -> str`
    - `recall(query, *, project=None, max_results=5) -> list[(MemoryEntry, Verdict)]`
    - `stats() -> dict`（`{total, by_layer, vectors}`）
    - `feedback(memory_id, action, *, context_query=None)`
    - `close()`
  - `memory_core.Consolidator(store)` + `quick_consolidate(...)`（启发式）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_core.py
import tempfile, unittest
from pathlib import Path
from memory_core import MemoryCore

class TestCore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.mc = MemoryCore(data_dir=Path(self._tmp.name))

    def tearDown(self):
        self.mc.close()
        self._tmp.cleanup()

    def test_add_auto_classify(self):
        mid = self.mc.add("踩坑：Resend 会拦截 UA 不标准请求")
        self.assertTrue(mid)

    def test_recall_roundtrip(self):
        self.mc.add("灵犀 v3.18 的 MCP Bus 端口是 18792")
        results = self.mc.recall("灵犀 MCP Bus 端口多少", max_results=3)
        self.assertTrue(any("18792" in e.content for e, v in results))

    def test_stats_shape(self):
        self.mc.add("测试条目")
        st = self.mc.stats()
        self.assertIn("total", st)
        self.assertIn("by_layer", st)
        self.assertGreaterEqual(st["total"], 1)

    def test_consolidate_heuristic(self):
        res = self.mc.consolidate(session_id="s1", conversation="今天修好了窗口传参 bug，原因是空格问题")
        self.assertIsNotNone(res)  # 启发式固化不依赖 LLM

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /d/code/projects/know_you_memory_plugin && PYTHONPATH="engine;." python -m unittest tests.test_core -v`
Expected: 失败（`MemoryCore` 或 `Consolidator` import 崩溃——引用了已删的 `LLMExtractor`）

- [ ] **Step 3: 改造 consolidate.py + __init__.py**

- `engine/memory_core/consolidate.py`：`Consolidator.__init__` 移除 `LLMExtractor` 依赖，`consolidate()` 内部直接走 `HeuristicExtractor`（生产版已实现）；删除 `from .llm_extractor import ...` 行；`ConsolidationResult`/`quick_consolidate` 保持
- `engine/memory_core/__init__.py`：删除 `from .llm_extractor import LLMExtractor`；删除 `from .auto_memory import ProjectScanner`；`MemoryCore.__init__` 接受 `data_dir`（构造 `MemoryCoreConfig(data_dir=...)`）；`add()` 增加 `source_file` 参数透传（Task 2 已加则直接接上）；`stats()`/`recall()`/`feedback()`/`close()` 保持生产版签名

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /d/code/projects/know_you_memory_plugin && PYTHONPATH="engine;." python -m unittest tests.test_core -v`
Expected: PASS（4 项）

- [ ] **Step 5: Commit**

```bash
git add engine/memory_core/consolidate.py engine/memory_core/__init__.py tests/test_core.py
git commit -m "feat(kym): MemoryCore 统一入口 + 启发式固化，去 LLM 依赖"
```

---

### Task 6: importer.py 分拣器（首次自动分拣核心）

**Files:**
- Create: `engine/memory_core/importer.py`
- Test: `tests/test_importer.py`

**Interfaces:**
- Consumes: `MemoryCore`（Task 5）
- Produces:
  - `importer.import_workspace(cwd: Path) -> dict` — 扫顶层+`.claude/`，返回 `{"imported": int, "by_layer": {str: int}}`
  - `importer.import_path(path: Path) -> dict` — 手动导入单文件/目录
  - `importer.sources_for_workspace(cwd: Path) -> list[tuple[Path, str, str]]` — 内部：`(文件, layer, category)` 候选

- [ ] **Step 1: 写失败测试**

```python
# tests/test_importer.py
import tempfile, unittest
from pathlib import Path
from memory_core import MemoryCore
from memory_core.importer import import_workspace, import_path

class TestImporter(unittest.TestCase):
    def _make_ws(self, root: Path):
        (root / "CLAUDE.md").write_text(
            "## 原则\n绝不删除用户数据\n## 偏好\n中文交流\n", encoding="utf-8")
        (root / "README.md").write_text("# demo 项目\nDuckDB 直连引擎\n", encoding="utf-8")
        sk = root / ".claude" / "skills" / "greet"
        sk.mkdir(parents=True)
        (sk / "SKILL.md").write_text("---\nname: greet\ndescription: 打招呼\n---\n打招呼用你好\n", encoding="utf-8")

    def test_import_workspace_layers(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._make_ws(root)
            mc = MemoryCore(data_dir=root / "memory")
            mc.close()  # 仅建库
            result = import_workspace(root, data_dir=root / "memory")
            self.assertGreater(result["imported"], 0)
            self.assertIn("L1", result["by_layer"])   # CLAUDE.md 原则
            self.assertIn("L3", result["by_layer"])   # 偏好 + skill
            self.assertIn("L4", result["by_layer"])   # README
            # 幂等：再扫一次不新增
            again = import_workspace(root, data_dir=root / "memory")
            self.assertEqual(again["imported"], 0)

    def test_import_path_single_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "note.md"
            p.write_text("# 笔记\nPython 3.13 已发布\n", encoding="utf-8")
            mc = MemoryCore(data_dir=Path(d) / "memory")
            mc.close()
            result = import_path(p, data_dir=Path(d) / "memory")
            self.assertGreaterEqual(result["imported"], 1)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /d/code/projects/know_you_memory_plugin && PYTHONPATH="engine;." python -m unittest tests.test_importer -v`
Expected: FAIL（`ModuleNotFoundError: memory_core.importer`）

- [ ] **Step 3: 实现 importer.py**

```python
"""已有记忆分拣器 — 把 workspace 的 CLAUDE.md/.claude/README 分拣进五层金字塔"""
from __future__ import annotations
from pathlib import Path
from memory_core import MemoryCore
from memory_core.classify import classify_content

# 来源 → 层级/分类 默认（可被 classify 覆盖）
_TOP_LEVEL = {
    "CLAUDE.md": ("L3", "preference"),      # 含原则段 → classify 会抬到 L1
    "AGENTS.md": ("L3", "preference"),
    "README.md": ("L4", "project"),
}
_CLAUDE_DIR = {
    "skills": ("L3", "knowledge"),   # 每个 SKILL.md：name+description
    "commands": ("L3", "knowledge"), # 每个 *.md：命令名+description
    "agents": ("LCM", "identity"),   # 每个 *.md：agent 定位
}


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _content_for(path: Path, rel: str) -> str:
    """按来源生成记忆内容（保留 frontmatter 标题结构）"""
    body = _read(path)
    if body is None:
        return ""
    if "skills" in rel and body.strip():
        return f"# Skill {path.stem}\n\n{body[:2000]}"
    if "commands" in rel and body.strip():
        return f"# Command {path.stem}\n\n{body[:2000]}"
    if "agents" in rel and body.strip():
        return f"# Agent {path.stem}\n\n{body[:2000]}"
    return body[:4000]


def _settings_summary(path: Path) -> str | None:
    """settings.json 只摘要 hooks/mcp 的【名称】，绝不落 key/token/password"""
    import json
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    names = {"hooks": sorted((data.get("hooks") or {}).keys()),
             "mcp": sorted((data.get("mcpServers") or {}).keys())}
    return f"# Claude 配置摘要\n\nhooks: {names['hooks']}\nmcpServers: {names['mcp']}"


def sources_for_workspace(cwd: Path) -> list[tuple[Path, str, str]]:
    """收集候选源 → [(path, layer_hint, category_hint)]"""
    cands: list[tuple[Path, str, str]] = []
    for fname, (layer, cat) in _TOP_LEVEL.items():
        p = cwd / fname
        if p.is_file():
            cands.append((p, layer, cat))
    claude = cwd / ".claude"
    if claude.is_dir():
        for sub, (layer, cat) in _CLAUDE_DIR.items():
            d = claude / sub
            if d.is_dir():
                for p in sorted(d.rglob("*.md")):
                    if p.name.upper() == "SKILL.MD" or p.parent.name == sub:
                        cands.append((p, layer, cat))
        sj = claude / "settings.json"
        if sj.is_file():
            cands.append((sj, "L4", "environment"))
    return cands


def import_workspace(cwd: Path, *, data_dir: Path | None = None) -> dict:
    mc = MemoryCore(data_dir=data_dir) if data_dir else MemoryCore()
    try:
        return _do_import(mc, sources_for_workspace(cwd))
    finally:
        mc.close()


def import_path(path: Path, *, data_dir: Path | None = None) -> dict:
    mc = MemoryCore(data_dir=data_dir) if data_dir else MemoryCore()
    try:
        if path.is_dir():
            sources = sources_for_workspace(path)
        else:
            sources = [(path, "L4", "knowledge")]
        return _do_import(mc, sources)
    finally:
        mc.close()


def _do_import(mc: MemoryCore, sources: list[tuple[Path, str, str]]) -> dict:
    stats = {"imported": 0, "by_layer": {}, "skipped": 0}
    for path, layer_hint, cat_hint in sources:
        if path.suffix == ".json":
            content = _settings_summary(path)
        else:
            content = _content_for(path, str(path))
        if not content:
            stats["skipped"] += 1
            continue
        classified = classify_content(content)
        layer = classified.get("layer") or layer_hint
        category = classified.get("category") or cat_hint
        try:
            mid = mc.add(content, layer=layer, category=category,
                         scope="project", project=None,
                         source_file=str(path), dedup=True)
        except Exception:
            stats["skipped"] += 1
            continue
        if mid:
            stats["imported"] += 1
            stats["by_layer"][layer] = stats["by_layer"].get(layer, 0) + 1
    return stats
```

> 关键点：`source_file=str(path)` 走 Task 2 的替换语义 → 同文件再扫是更新不是堆叠（幂等）。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /d/code/projects/know_you_memory_plugin && PYTHONPATH="engine;." python -m unittest tests.test_importer -v`
Expected: PASS（2 项）

- [ ] **Step 5: Commit**

```bash
git add engine/memory_core/importer.py tests/test_importer.py
git commit -m "feat(kym): 分拣器 importer（顶层+.claude 映射、source_file 幂等、settings 仅摘要名称）"
```

---

### Task 7: bootstrap.py + cli.py

**Files:**
- Create: `engine/bootstrap.py`、`engine/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `MemoryCore`（Task 5）、`import_workspace`/`import_path`（Task 6）
- Produces:
  - `bootstrap.ensure_data_dir(data_dir: Path) -> Path` — 建目录 + 初始化库，幂等
  - CLI 子命令：`add` / `recall` / `status` / `import`（`python engine/cli.py <cmd> ...`）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_cli.py
import io, json, subprocess, sys, tempfile, unittest
from pathlib import Path
from memory_core import MemoryCore
from engine.bootstrap import ensure_data_dir

REPO = Path(__file__).resolve().parent.parent

class TestBootstrap(unittest.TestCase):
    def test_ensure_data_dir_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            dd = ensure_data_dir(Path(d) / "memory")
            self.assertTrue((dd / "memory.db").exists())
            again = ensure_data_dir(Path(d) / "memory")   # 不抛异常
            self.assertEqual(again, dd)

class TestCLI(unittest.TestCase):
    def _run(self, *args, data_dir):
        env = {"MEMORY_CORE_DATA": str(data_dir), "PYTHONPATH": str(REPO / "engine")}
        return subprocess.run([sys.executable, str(REPO / "engine" / "cli.py"), *args],
                              capture_output=True, text=True, env=env)

    def test_cli_recall_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d) / "memory"
            r1 = self._run("add", "CLI 测试条目：端口 18792", data_dir=dd)
            self.assertEqual(r1.returncode, 0)
            r2 = self._run("recall", "端口", data_dir=dd)
            self.assertIn("18792", r2.stdout)

    def test_cli_status(self):
        with tempfile.TemporaryDirectory() as d:
            self._run("add", "状态测试", data_dir=Path(d) / "memory")
            r = self._run("status", data_dir=Path(d) / "memory")
            self.assertEqual(r.returncode, 0)
            self.assertIn("total", r.stdout)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /d/code/projects/know_you_memory_plugin && PYTHONPATH="engine;." python -m unittest tests.test_cli -v`
Expected: FAIL（`engine.bootstrap` / `engine.cli` 不存在）

- [ ] **Step 3: 实现 bootstrap.py + cli.py**

`engine/bootstrap.py`：
```python
from pathlib import Path
from memory_core import MemoryCore

def default_data_dir() -> Path:
    import os
    return Path(os.environ.get("MEMORY_CORE_DATA", str(Path.home() / ".memory_core")))

def ensure_data_dir(data_dir: Path | None = None) -> Path:
    dd = Path(data_dir) if data_dir else default_data_dir()
    dd.mkdir(parents=True, exist_ok=True)
    mc = MemoryCore(data_dir=dd)   # 触发建库（幂等）
    mc.close()
    return dd
```

`engine/cli.py`（入口片段）：
```python
"""KYM CLI — python engine/cli.py <add|recall|status|import> ..."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /d/code/projects/know_you_memory_plugin && PYTHONPATH="engine;." python -m unittest tests.test_cli -v`
Expected: PASS（3 项）

- [ ] **Step 5: Commit**

```bash
git add engine/bootstrap.py engine/cli.py tests/test_cli.py
git commit -m "feat(kym): bootstrap 数据目录自举 + CLI 入口"
```

---

### Task 8: 插件清单（plugin.json / marketplace.json / hooks.json / LICENSE）

**Files:**
- Create: `.claude-plugin/plugin.json`、`.claude-plugin/marketplace.json`、`hooks/hooks.json`、`LICENSE`
- Test: `claude plugin validate`（手工，见 Step 4）

**Interfaces:**
- Consumes: `session-start.py` / `post-tool-use.py`（Task 9 将实现，hooks.json 先引用占位路径，Task 9 落地后补实）
- Produces: 可被 Claude Code 识别的插件外壳

- [ ] **Step 1: 创建 plugin.json**

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

- [ ] **Step 2: 创建 marketplace.json**

```json
{
  "name": "opensquilla",
  "owner": { "name": "Open Quant" },
  "plugins": [
    { "name": "know-you-memory", "source": "./", "description": "五层金字塔持久记忆", "version": "0.1.0" }
  ]
}
```

- [ ] **Step 3: 创建 hooks/hooks.json + LICENSE**

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
`LICENSE`：MIT 全文（作者 Open Quant）。

- [ ] **Step 4: 校验插件清单**

Run: `cd /d/code/projects/know_you_memory_plugin && claude plugin validate .`
Expected: 通过（若 CLI 不可用，`npx @anthropic-ai/claude-code plugin validate .` 或跳过本步记到端到端 Task 13）

- [ ] **Step 5: Commit**

```bash
git add .claude-plugin hooks/hooks.json LICENSE
git commit -m "feat(kym): 插件清单（plugin/marketplace/hooks）+ MIT"
```

---

### Task 9: hooks 脚本（session-start.py / post-tool-use.py）

**Files:**
- Create: `hooks/session-start.py`、`hooks/post-tool-use.py`
- Test: `tests/test_hooks.py`

**Interfaces:**
- Consumes: `MemoryCore`（Task 5）、`import_workspace`（Task 6）、`bootstrap`（Task 7）
- Produces:
  - SessionStart stdout 注入文本（格式见 spec §8）
  - 首次空库自动触发 `import_workspace(cwd)`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_hooks.py
import io, tempfile, unittest
from contextlib import redirect_stdout
from pathlib import Path
from hooks import session_start

REPO = Path(__file__).resolve().parent.parent

class TestSessionStart(unittest.TestCase):
    def test_first_run_auto_imports_and_reports(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "CLAUDE.md").write_text("## 原则\n绝不删除用户数据\n", encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                session_start.main([str(root)], data_dir_override=root / "memory")
            out = buf.getvalue()
            self.assertIn("首次分拣", out)
            self.assertIn("L1", out)

    def test_nonempty_skips_import(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dd = root / "memory"
            # 预置一条记忆，使库非空
            from memory_core import MemoryCore
            mc = MemoryCore(data_dir=dd); mc.add("已有记忆"); mc.close()
            buf = io.StringIO()
            with redirect_stdout(buf):
                session_start.main([str(root)], data_dir_override=dd)
            self.assertNotIn("首次分拣", buf.getvalue())

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /d/code/projects/know_you_memory_plugin && PYTHONPATH="engine;." python -m unittest tests.test_hooks -v`
Expected: FAIL（`hooks.session_start` 不存在）

- [ ] **Step 3: 实现 session-start.py**

```python
"""KYM SessionStart hook — 首次空库自动分拣 + 上下文注入"""
import sys
from pathlib import Path
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT / "engine"))

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
```

`hooks/post-tool-use.py`：V1 精简实现——只记录"创建/修改了 CLAUDE.md 或 README"这一低频高价值事件，其余忽略：
```python
"""KYM PostToolUse hook — 轻量记录关键写操作（V1：只记 CLAUDE.md/README 变更）"""
import json, os, sys
from pathlib import Path
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT / "engine"))
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
```

> 说明：`sys.path.insert` 指向 `engine/` 让脚本能 import `memory_core`，hooks 命令里 `python` 直接调用（零依赖，无需 venv）。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /d/code/projects/know_you_memory_plugin && PYTHONPATH="engine;." python -m unittest tests.test_hooks -v`
Expected: PASS（2 项）

- [ ] **Step 5: Commit**

```bash
git add hooks/session-start.py hooks/post-tool-use.py tests/test_hooks.py
git commit -m "feat(kym): SessionStart 注入+首次分拣 + PostToolUse 轻量记录"
```

---

### Task 10: commands（/memory-import · /memory-recall · /memory-status）

**Files:**
- Create: `commands/memory-import.md`、`commands/memory-recall.md`、`commands/memory-status.md`

**Interfaces:**
- Consumes: `engine/cli.py`（Task 7）
- Produces: 三个可用的 slash command

- [ ] **Step 1: 创建 commands/memory-import.md**

````markdown
---
description: 手动分拣已有记忆文件/目录进 KYM 记忆库
argument-hint: "[path]"
allowed-tools: "Bash(*)"
---

手动把指定目录或文件的已有记忆分拣进 KYM 五层金字塔。
无参数时扫描当前 workspace（CLAUDE.md/.claude/README）。

1. 运行: !`python ${CLAUDE_PLUGIN_ROOT}/engine/cli.py import ${1:-.}`
2. 将输出（imported/by_layer）汇报给用户，说明分拣了哪些层级各几条。
````

- [ ] **Step 2: 创建 commands/memory-recall.md**

````markdown
---
description: 从 KYM 记忆库检索相关内容
argument-hint: "<query>"
allowed-tools: "Bash(*)"
---

检索 KYM 五层金字塔记忆库并展示 top 结果。

1. 运行: !`python ${CLAUDE_PLUGIN_ROOT}/engine/cli.py recall "$ARGUMENTS"`
2. 把命中的记忆整理成简洁回答，标注置信度与层级。
````

- [ ] **Step 3: 创建 commands/memory-status.md**

````markdown
---
description: 查看 KYM 记忆库状态（总量/分层/数据位置）
allowed-tools: "Bash(*)"
---

1. 运行: !`python ${CLAUDE_PLUGIN_ROOT}/engine/cli.py status`
2. 以可读格式展示 total/by_layer/data 目录。
````

- [ ] **Step 4: 校验命令语法**

Run: `cd /d/code/projects/know_you_memory_plugin && ls commands/ && grep -l "description" commands/*.md`
Expected: 3 个文件，各自含 description frontmatter

- [ ] **Step 5: Commit**

```bash
git add commands/
git commit -m "feat(kym): 三个 slash commands（import/recall/status）"
```

---

### Task 11: skill（memory-recorder）

**Files:**
- Create: `skills/memory-recorder/SKILL.md`

- [ ] **Step 1: 创建 SKILL.md**

````markdown
---
name: memory-recorder
description: 什么时候把信息写进 KYM 记忆库、什么时候检索记忆。Use when 用户透露偏好/决策/身份信息、完成关键操作、踩坑、或问"上次/之前/你记得/你说过"等涉及历史的问题。
---

# KYM 记忆记录

KYM 是五层金字塔持久记忆，纯本地零依赖。数据在 `~/.memory_core/memory.db`。

## 什么时候 ADD（写入记忆）
- 用户透露新偏好、决策、身份信息 → 立即写
- 完成关键操作/修复 → 记录结论与踩坑
- 跨会话需要记住的事实

调用 MCP 工具 `mcp__plugin_know-you-memory_kym__memory_add`
（参数: content 必填, layer/tags/project 可选）。
无 MCP 时用 `!python ${CLAUDE_PLUGIN_ROOT}/engine/cli.py add <内容>`。

## 什么时候 RECALL（检索记忆）
- 用户问"上次/之前/你记得/你说过/之前怎么修的"
- 涉及历史项目状态、要避免重复踩坑

调用 `mcp__plugin_know-you-memory_kym__memory_recall`
或无 MCP 时 `!python ${CLAUDE_PLUGIN_ROOT}/engine/cli.py recall <query>`。

## 原则
- 内容要自包含：让未来的自己无需上下文也能读懂
- 不记临时细节、不记敏感凭据
- L1/L2/L3（原则/画像/偏好）写入要慎重，凭内容判断
````

- [ ] **Step 2: 校验 skill 结构**

Run: `cd /d/code/projects/know_you_memory_plugin && test -f skills/memory-recorder/SKILL.md && grep -q "^name: memory-recorder" skills/memory-recorder/SKILL.md && echo OK`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add skills/memory-recorder
git commit -m "feat(kym): memory-recorder skill（写入/检索时机 + MCP/CLI 调用指引）"
```

---

### Task 12: MCP server（纯 stdlib stdio）

**Files:**
- Create: `servers/mcp_server.py`
- Test: `tests/test_mcp_protocol.py`

**Interfaces:**
- Consumes: `MemoryCore`（Task 5）、`import_path`（Task 6）
- Produces: JSON-RPC 2.0 over stdio 服务，工具 `memory_add`/`memory_recall`/`memory_status`/`memory_import`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_mcp_protocol.py
import json, unittest
from pathlib import Path
from servers.mcp_server import MemoryCoreBackend, rpc_reply

REPO = Path(__file__).resolve().parent.parent

class TestMCPProtocol(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.be = MemoryCoreBackend(data_dir=Path(self._tmp.name) / "memory")

    def tearDown(self):
        self.be.mc.close()
        self._tmp.cleanup()

    def test_initialize(self):
        reply = self.be.handle("initialize", {"protocolVersion": "2025-06-18"}, 1)
        self.assertEqual(reply["result"]["capabilities"]["tools"], {})
        self.assertEqual(reply["result"]["serverInfo"]["name"], "know-you-memory")

    def test_tools_list(self):
        reply = self.be.handle("tools/list", {}, 2)
        names = [t["name"] for t in reply["result"]["tools"]]
        self.assertIn("memory_add", names)
        self.assertIn("memory_recall", names)
        self.assertIn("memory_status", names)
        self.assertIn("memory_import", names)

    def test_memory_add_then_recall(self):
        self.be.handle("tools/call", {"name": "memory_add",
            "arguments": {"content": "MCP 测试记忆 18792"}}, 3)
        reply = self.be.handle("tools/call", {"name": "memory_recall",
            "arguments": {"query": "MCP 测试记忆"}}, 4)
        text = json.dumps(reply, ensure_ascii=False)
        self.assertIn("18792", text)

    def test_rpc_reply_shape(self):
        obj = rpc_reply(1, {"ok": True})
        self.assertEqual(obj["jsonrpc"], "2.0")
        self.assertEqual(obj["id"], 1)
        self.assertIn("result", obj)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /d/code/projects/know_you_memory_plugin && PYTHONPATH="engine;." python -m unittest tests.test_mcp_protocol -v`
Expected: FAIL（`servers.mcp_server` 不存在）

- [ ] **Step 3: 实现 mcp_server.py**

```python
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
            return rpc_reply(msg_id, {"tools": list(self._tools.values())})
        if method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments", {}) or {}
            try:
                return rpc_reply(msg_id, self._call_tool(name, args))
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
```

> 说明：stdio 用 **newline-delimited JSON**（每帧一行）。若 Claude Code 实际走 Content-Length 帧，端到端 Task 13 会暴露，届时补帧解析（改动局限在 `main()` 读入端）。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /d/code/projects/know_you_memory_plugin && PYTHONPATH="engine;." python -m unittest tests.test_mcp_protocol -v`
Expected: PASS（4 项）

- [ ] **Step 5: Commit**

```bash
git add servers/mcp_server.py tests/test_mcp_protocol.py
git commit -m "feat(kym): 纯 stdlib MCP stdio server（JSON-RPC 2.0）"
```

---

### Task 13: 端到端验证 + README

**Files:**
- Create: `README.md`、`engine/requirements-vectors.txt`
- Modify: 各测试可能的连调问题

- [ ] **Step 1: 全量跑测试**

Run: `cd /d/code/projects/know_you_memory_plugin && PYTHONPATH="engine;." python -m unittest discover -s tests -v`
Expected: 全绿（Task 1-12 累计约 24 项）

- [ ] **Step 2: 本地 `--plugin-dir` 冒烟加载**

Run: `cd /d/code/projects/know_you_memory_plugin && claude --plugin-dir "$(pwd)" --help`
Expected: 插件被识别；随后在真实会话验证：
- SessionStart 触发首次分拣（在空库 + 有 CLAUDE.md 的临时目录开窗）
- `/memory-recall` 能检索到分拣的记忆
- MCP 工具 `mcp__plugin_know-you-memory_kym__*` 可调用（若协议帧格式有问题，按 Task 12 注补 Content-Length 解析）

- [ ] **Step 3: 写 README.md**

包含：一句话定位、安装步骤（`claude plugin marketplace add opensquilla/know-you-memory-plugin` + `install`）、前置要求（Python 3.11+）、首次自动分拣说明、三个命令 + 一个 skill、可选向量增强（`pip install -r engine/requirements-vectors.txt`）、数据目录与隐私（数据全本地，settings.json 只摘要名称）、卸载方式。

`engine/requirements-vectors.txt`：
```
lancedb>=0.17
sentence-transformers>=3.0
numpy>=1.26
pyarrow>=14
```

- [ ] **Step 4: Commit**

```bash
git add README.md engine/requirements-vectors.txt
git commit -m "docs(kym): README + 可选向量增强依赖声明"
```

---

### Task 14: 提交 opensquilla + 三兄弟安装验证

**Files:**
- 无代码；仓库运维

- [ ] **Step 1: 创建 opensquilla private 仓库**

Run: `cd /d/code/projects/know_you_memory_plugin && gh repo create opensquilla/know-you-memory-plugin --private --source=. --push`
（2FA/凭证问题见 L0 GitHub 凭据）

- [ ] **Step 2: 大扣本机安装验证**

Run:
```bash
claude plugin marketplace add opensquilla/know-you-memory-plugin
claude plugin install know-you-memory@opensquilla --scope user
claude plugin list
```
Expected: `know-you-memory` 出现且 enabled

- [ ] **Step 3: 真实开窗验证分拣**

- 找一个有 CLAUDE.md 的空记忆项目目录开窗
- 确认 SessionStart 注入 `[kym] 首次分拣: ...`
- `/memory-recall` 能查到 CLAUDE.md 内容
- MCP 工具可调

- [ ] **Step 4: 给大圣/小扣留接入说明**

在仓库 `docs/integration-codex-dsh.md` 写明：Codex/dsh 不跑 Claude Code，直接引用 `engine/`（`sys.path`）或独立拉起 `servers/mcp_server.py`；共享记忆时统一 `MEMORY_CORE_DATA` 环境变量。

- [ ] **Step 5: Commit 收尾**

```bash
git add docs/integration-codex-dsh.md
git commit -m "docs(kym): Codex/dsh 接入说明"
```
然后向大哥汇报安装验证结果与三兄弟接入状态。

---

## 执行顺序依赖

```
Task 1 → 2 → 3 → 4 → 5  （引擎，线性）
              ↘ 6 → 7   （分拣 + CLI）
Task 5/7 完成后可并行：8 → 9 → 10 → 11（插件外壳，各自独立）
Task 12（MCP）依赖 5/6
Task 13 收口 → Task 14 分发
```

引擎任务（1-7）优先，是地基；插件外壳（8-12）多为声明式文件，快。每任务结束有独立测试，可单独审。
