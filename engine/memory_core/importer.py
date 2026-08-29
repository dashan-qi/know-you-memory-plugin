"""已有记忆分拣器 — 把 workspace 的 CLAUDE.md/.claude/README 分拣进五层金字塔"""
from __future__ import annotations
import re
from pathlib import Path
from memory_core import MemoryCore
from memory_core.classify import classify_content
from memory_core.store import _sha256_file

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


def _chunk_content(content: str) -> list[str]:
    """按 `## ` 二级标题分块 markdown 内容（spec §7.2 约束）。

    每块 = 标题行 + 到下一个 `## ` 标题前的正文；无 `## ` 标题 → 整文件一块。
    YAML frontmatter（`---...---`）只保留给第一块，让原则/偏好/项目各节
    分别落到对应层级，而不是整文件坍缩成一条。
    """
    lines = content.splitlines(keepends=True)
    fm_lines: list[str] = []
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                fm_lines = lines[: i + 1]
                lines = lines[i + 1:]
                break
    chunks: list[str] = []
    cur: list[str] = []
    for ln in lines:
        if re.match(r"^##\s+", ln):
            if cur:
                chunks.append("".join(cur))
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        chunks.append("".join(cur))
    if not chunks:
        return []
    if fm_lines:
        chunks[0] = "".join(fm_lines) + "\n" + chunks[0]
    # 去空白块
    return [c for c in (c.strip() for c in chunks) if c]


def _heading_slug(chunk: str, idx: int) -> str:
    """从 chunk 的首个 `## ` 标题生成稳定 slug（无标题回退到 chunk 索引）。

    用于多块文件的 source_file 后缀，保证每块有自己的 hash 幂等键。
    重复标题（同文件多个 `## 偏好` 节）用块序号 `-{idx}` 消歧——否则两块
    撞同一个 source_file，重扫时各自 hash 键冲突、每块都重灌（imported != 0）。
    idx 由 `_chunk_content` 的块序确定，同一文件内容未变则 idx 不变，
    source_file 跨重扫保持稳定，幂等成立。
    """
    m = re.search(r"^#+\s+(.+)", chunk, flags=re.MULTILINE)
    if m:
        slug = m.group(1).strip().lower()
        slug = "".join(
            c for c in slug if c.isalnum() or "一" <= c <= "鿿" or c in "-_"
        )[:40]
        if slug:
            return f"{slug}-{idx}"
    return f"chunk{idx}"


def _settings_summary(path: Path) -> str | None:
    """settings.json 只摘要 hooks/mcp 的【名称】，绝不落 key/token/password"""
    import json
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    hooks = data.get("hooks")
    mcp = data.get("mcpServers")
    names = {
        "hooks": sorted(hooks.keys()) if isinstance(hooks, dict) else [],
        "mcp": sorted(mcp.keys()) if isinstance(mcp, dict) else [],
    }
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
            chunks = [content] if content else []
        else:
            body = _read(path)
            if body is None:
                stats["skipped"] += 1
                continue
            # skill/command/agent 单文件先加标题前缀（保留来源可读性）
            rel = str(path)
            if "skills" in rel and body.strip():
                body = f"# Skill {path.stem}\n\n{body}"
            elif "commands" in rel and body.strip():
                body = f"# Command {path.stem}\n\n{body}"
            elif "agents" in rel and body.strip():
                body = f"# Agent {path.stem}\n\n{body}"
            chunks = _chunk_content(body)
        if not chunks:
            stats["skipped"] += 1
            continue
        multi = len(chunks) > 1
        for idx, chunk in enumerate(chunks):
            # 幂等：同 chunk 内容 hash 未变 → 跳过。多块文件 source_file 带块 slug，
            # 每块各自跟踪 hash，重扫不新增、增删某节时其余块不重灌。
            source_file = str(path)
            if multi:
                source_file = f"{source_file}::{_heading_slug(chunk, idx)}"
            content_hash = _sha256_file(chunk)
            if mc.store.sqlite.file_hash_unchanged(source_file, content_hash):
                stats["skipped"] += 1
                continue
            classified = classify_content(chunk)
            # 来源 hint 是默认值；classify 只在命中明确规则（非默认 L4/knowledge）时覆盖，
            # 否则 skill→L3 / agents→LCM 等 hint 会被 classify 的兜底 L4 吞掉。
            layer, category = layer_hint, cat_hint
            if classified.get("layer") != "L4" or classified.get("category") != "knowledge":
                layer = classified.get("layer") or layer_hint
                category = classified.get("category") or cat_hint
            # L1-L3 规则的 global 作用域优先于 hardcode 的 project
            scope = classified.get("scope") or "project"
            try:
                mid = mc.add(chunk, layer=layer, category=category,
                             scope=scope, project=None,
                             source_file=source_file, dedup=True)
                if mid:
                    st = path.stat()
                    mc.store.sqlite.upsert_file_hash(source_file, content_hash,
                                                     st.st_mtime, st.st_size)
            except Exception:
                stats["skipped"] += 1
                continue
            if mid:
                stats["imported"] += 1
                stats["by_layer"][layer] = stats["by_layer"].get(layer, 0) + 1
    return stats
