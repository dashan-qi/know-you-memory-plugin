"""已有记忆分拣器 — 把 workspace 的 CLAUDE.md/.claude/README 分拣进五层金字塔"""
from __future__ import annotations
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
        # 幂等：同文件 hash 未变 → 跳过（source_file 替换 + hash 判断 → 重扫不新增）
        content_hash = _sha256_file(content)
        if mc.store.sqlite.file_hash_unchanged(str(path), content_hash):
            stats["skipped"] += 1
            continue
        classified = classify_content(content)
        # 来源 hint 是默认值；classify 只在命中明确规则（非默认 L4/knowledge）时覆盖，
        # 否则 skill→L3 / agents→LCM 等 hint 会被 classify 的兜底 L4 吞掉。
        layer, category = layer_hint, cat_hint
        if classified.get("layer") != "L4" or classified.get("category") != "knowledge":
            layer = classified.get("layer") or layer_hint
            category = classified.get("category") or cat_hint
        try:
            mid = mc.add(content, layer=layer, category=category,
                         scope="project", project=None,
                         source_file=str(path), dedup=True)
            if mid:
                st = path.stat()
                mc.store.sqlite.upsert_file_hash(str(path), content_hash,
                                                 st.st_mtime, st.st_size)
        except Exception:
            stats["skipped"] += 1
            continue
        if mid:
            stats["imported"] += 1
            stats["by_layer"][layer] = stats["by_layer"].get(layer, 0) + 1
    return stats
