"""
memory_core 记忆固化 (Consolidation)

会话结束时触发，从对话中提取关键事实、自动分类、写入记忆库。

MVP: 启发式提取（不调 LLM），但接口预留 LLM 插槽。
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Protocol

from .store import MemoryStore, MemoryEntry
from .classify import classify_content, infer_project_id

logger = logging.getLogger("memory_core.consolidate")


# ── 事实提取器接口（可插拔 LLM） ──────────────────────

class FactExtractor(Protocol):
    """事实提取器协议 — 后续可替换为 LLM 实现"""

    def extract(self, conversation: str) -> list[dict]:
        """从对话文本中提取关键事实

        Returns:
            [{"content": "...", "layer": "L4", "category": "knowledge", ...}, ...]
        """
        ...


# ── MVP: 启发式提取器 ────────────────────────────────

# 关键决策/事实的信号模式
FACT_PATTERNS = [
    # 项目路径声明
    (r"(?:位置|目录|路径)[：:]\s*(.+?)(?:$|\n)", "L4", "knowledge", "project"),
    # 端口号声明
    (r"(?:端口|port)[：:]\s*(\d+)", "L4", "knowledge", "project"),
    # 决策声明
    (r"(?:决定|采用|选用|选择|方案)[：:]\s*(.+?)(?:$|\n)", "L4", "knowledge", "project"),
    # bug 修复
    (r"(?:修复|bug|bugfix)[：:]\s*(.+?)(?:$|\n)", "L4", "knowledge", "project"),
    # 配置变更
    (r"(?:配置|settings|config)[：:]\s*(.+?)(?:$|\n)", "L4", "knowledge", "project"),
    # 偏好声明
    (r"(?:偏好|更喜欢|不喜欢|不要|以后)[：:]\s*(.+?)(?:$|\n)", "L3", "preference", "global"),
    # 踩坑
    (r"(?:踩坑|教训|注意|⚠️|小心)[：:]\s*(.+?)(?:$|\n)", "L4", "knowledge", "project"),
    # 命名/称呼
    (r"(?:叫我|我是|我是谁|用户是)[：:]\s*(.+?)(?:$|\n)", "L2", "identity", "global"),
]


class HeuristicExtractor:
    """启发式事实提取器 — MVP，不调 LLM"""

    def extract(
        self, conversation: str, current_project: str | None = None
    ) -> list[dict]:
        """从对话文本中提取结构化事实

        分类流程：模式匹配 → classify_content() 精细化分类 → 去重
        """
        from .classify import classify_content as _classify

        facts = []

        # 按模式匹配
        for pattern, layer, category, scope in FACT_PATTERNS:
            for m in re.finditer(pattern, conversation, re.IGNORECASE):
                content = m.group(1).strip()
                if len(content) < 3 or len(content) > 500:
                    continue

                # 用 classify_content 精细化分类（不盲信模式的硬编码 category）
                refined = _classify(content)
                project_id = infer_project_id(content, current_project)
                facts.append({
                    "content": content,
                    "layer": refined.get("layer", layer),
                    "category": refined.get("category", category),
                    "scope": refined.get("scope", scope),
                    "project_id": project_id or current_project,
                    "confidence": 0.70,  # 启发式提取的默认置信度
                })

        # 去重（相同 content 只保留一条）
        seen = set()
        unique = []
        for f in facts:
            key = f["content"].strip().lower()
            if key not in seen:
                seen.add(key)
                unique.append(f)

        logger.info("HeuristicExtractor: %d facts extracted", len(unique))
        return unique


# ── Consolidation 管线 ───────────────────────────────

@dataclass
class ConsolidationResult:
    """一次 consolidation 的结果"""
    session_id: str
    facts_extracted: int
    new_memories: int
    updated_memories: int
    conflicts_found: int
    memory_ids: list[str] = field(default_factory=list)


class Consolidator:
    """记忆固化器 — 会话结束时运行"""

    def __init__(
        self,
        store: MemoryStore,
        extractor: FactExtractor | None = None,
    ):
        self.store = store
        if extractor is not None:
            self.extractor = extractor
            return
        # 默认启发式提取器（stdlib-only，不调 LLM）；KYM V1 已剔除 LLMExtractor
        self.extractor = HeuristicExtractor()

    def consolidate(
        self,
        session_id: str,
        conversation: str,
        *,
        current_project: str | None = None,
        extra_context: dict | None = None,
    ) -> ConsolidationResult:
        """对一次会话执行 consolidation

        Args:
            session_id: 会话标识
            conversation: 完整对话文本
            current_project: 当前活跃项目 ID
            extra_context: 额外上下文（如用户指令触发的元信息）

        Returns:
            ConsolidationResult
        """
        result = ConsolidationResult(session_id=session_id, facts_extracted=0,
                                     new_memories=0, updated_memories=0,
                                     conflicts_found=0)

        # Step 1: 提取事实
        facts = self.extractor.extract(conversation, current_project)
        result.facts_extracted = len(facts)

        if not facts:
            logger.info("Consolidation: no facts extracted for session %s", session_id)
            return result

        # Step 2: 检查是否已有相似记忆（去重 + 事实演变检测）
        new_facts = []
        for fact in facts:
            similar = self._find_similar(fact["content"])
            if similar:
                # 已有相似记忆 → 更新或取代
                evolved_id = self._update_existing(similar, fact, session_id)
                result.updated_memories += 1
                result.memory_ids.append(evolved_id)
            else:
                new_facts.append(fact)

        # Step 3: 批量写入新记忆
        if new_facts:
            for fact in new_facts:
                fact["source_session_id"] = session_id
            ids = self.store.add_batch(new_facts)
            result.new_memories = len(ids)
            result.memory_ids.extend(ids)

        # Step 4: 冲突检测
        result.conflicts_found = self._detect_new_conflicts(result.memory_ids)

        logger.info(
            "Consolidation complete: %d extracted, %d new, %d updated, %d conflicts",
            result.facts_extracted, result.new_memories,
            result.updated_memories, result.conflicts_found,
        )
        return result

    def _find_similar(self, content: str) -> Optional[MemoryEntry]:
        """查找是否有相似内容（简单向量相似度）"""
        results = self.store.search_vector(content, top_k=3)
        for entry, sim in results:
            if sim >= 0.85:  # 高相似度 → 可能重复
                return entry
        return None

    def _update_existing(
        self, existing: MemoryEntry, fact: dict, session_id: str
    ) -> str:
        """更新或取代已有记忆（时态建模核心）

        当新事实与旧记忆内容不同时，不是简单覆盖，而是：
        1. 设置旧条目 valid_until = now()（旧事实到此为止）
        2. 创建新条目 valid_from = now()（新事实从现在生效）
        3. 创建 supersedes 边（新 → 旧）

        当内容相同时，仅刷新 updated_at。

        Returns:
            新条目的 ID（内容相同则返回旧条目 ID）
        """
        import difflib

        now_iso = datetime.now(timezone.utc).isoformat()
        new_content = fact.get("content", "").strip()
        existing_content = (existing.content or "").strip()

        # 内容完全相同 → 仅刷新时间
        if new_content == existing_content:
            self.store.sqlite.update(existing.id, updated_at=now_iso)
            return existing.id

        # 内容不同 → 计算相似度，判断是修正还是演变
        similarity = difflib.SequenceMatcher(
            None, existing_content, new_content
        ).ratio()

        if similarity >= 0.5:
            # 小幅修正（相似度 ≥ 0.5）→ 直接更新内容
            self.store.update(
                existing.id,
                content=new_content,
                updated_at=now_iso,
            )
            return existing.id

        # 事实演变（相似度 < 0.5）→ 关闭旧条目 + 创建新条目
        from .store import Edge

        # 1. 关闭旧条目
        self.store.sqlite.update(
            existing.id,
            valid_until=now_iso,
            updated_at=now_iso,
        )

        # 2. 创建新条目（继承旧条目的元数据）
        new_entry = MemoryEntry(
            content=new_content,
            layer=fact.get("layer", existing.layer),
            category=fact.get("category", existing.category),
            scope=fact.get("scope", existing.scope),
            project_id=fact.get("project_id", existing.project_id),
            tags=fact.get("tags", existing.tags),
            source_session_id=session_id,
            confidence=fact.get("confidence", existing.confidence),
            valid_from=now_iso,
            valid_until=None,
        )
        self.store.sqlite.insert(new_entry)

        # 写入向量（可选增强路径，无向量依赖时跳过）
        if self.store.lancedb is not None:
            vectors = self.store.embed([new_content])
            self.store.lancedb.upsert([new_entry], vectors)

        # 3. 创建 supersedes 边（新 → 旧）
        edge = Edge(
            source_id=new_entry.id,
            target_id=existing.id,
            relation_type="supersedes",
        )
        self.store.sqlite.add_edge(edge)

        logger.info(
            "Fact evolution: %s → %s (sim=%.2f, supersedes edge created)",
            existing.id, new_entry.id, similarity,
        )
        return new_entry.id

    def _detect_new_conflicts(self, new_ids: list[str]) -> int:
        """检测新记忆与已有记忆的冲突"""
        conflicts = 0
        for mid in new_ids:
            entry = self.store.sqlite.get(mid)
            if not entry:
                continue

            # 简单检查：同 project + 同 tags 的其他记忆
            if entry.project_id and entry.tags:
                relatives = self.store.sqlite.search_by_tags(entry.tags)
                for other in relatives:
                    if other.id == entry.id:
                        continue
                    if other.project_id == entry.project_id:
                        # 检查是否可能冲突：一条含否定词，另一条不含
                        neg_words = ["不", "不是", "不要", "不能", "禁用", "废弃"]
                        e1_neg = any(w in entry.content for w in neg_words)
                        e2_neg = any(w in other.content for w in neg_words)
                        if e1_neg != e2_neg:
                            from .store import Edge
                            self.store.sqlite.add_edge(Edge(
                                source_id=entry.id,
                                target_id=other.id,
                                relation_type="contradicts",
                            ))
                            conflicts += 1
        return conflicts


# ── 便捷函数 ─────────────────────────────────────────


def quick_consolidate(
    store: MemoryStore,
    session_id: str,
    conversation: str,
    current_project: str | None = None,
) -> ConsolidationResult:
    """一键 consolidation"""
    consolidator = Consolidator(store)
    return consolidator.consolidate(session_id, conversation,
                                    current_project=current_project)
