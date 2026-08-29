"""
memory_core 检索层

Query Router → 双路召回(TF-IDF + 语义向量) → RRF 融合 → 排序输出

原则：搜索路径上零 LLM 调用，纯本地计算，<200ms。
"""

from __future__ import annotations

import re
import math
import logging
from collections import defaultdict
from typing import Optional

from datetime import datetime, timezone

from .config import (
    MEMORY_SIGNAL_WORDS,
    PROJECT_SIGNAL_WORDS,
    MAX_CANDIDATES,
    MAX_INJECTED,
    VECTOR_TOP_K,
    TFIDF_TOP_K,
    RRF_K,
    VECTOR_SIMILARITY_THRESHOLD,
)
from .store import MemoryStore, MemoryEntry

logger = logging.getLogger("memory_core.retrieve")


# ── Query Router ──────────────────────────────────────

class RouteDecision:
    """路由决策"""

    __slots__ = ("is_memory", "is_knowledge", "confidence")

    def __init__(self, is_memory: bool, is_knowledge: bool, confidence: float):
        self.is_memory = is_memory
        self.is_knowledge = is_knowledge
        self.confidence = confidence

    @property
    def is_hybrid(self) -> bool:
        return self.is_memory and self.is_knowledge

    def __repr__(self):
        if self.is_hybrid:
            return f"RouteDecision(hybrid, conf={self.confidence:.2f})"
        if self.is_memory:
            return f"RouteDecision(memory, conf={self.confidence:.2f})"
        return f"RouteDecision(knowledge, conf={self.confidence:.2f})"


# 知识问题信号词
KNOWLEDGE_SIGNAL_PATTERNS = [
    r"(怎么|如何|什么是|为什么|最佳实践|用法|API|文档|教程)",
    r"(DuckDB|SQLite|LanceDB|Python|Streamlit|React|Electron|Node\.js)\s+(优化|查询|配置|安装|使用)",
    r"(有没有|有哪些|什么方法|什么方案)\s+(?!.*咱们)(?!.*上次)(?!.*之前)",
]


def route_query(query: str) -> RouteDecision:
    """判断问题类型：知识 vs 记忆 vs 混合

    规则：
    1. 含"咱们/上次/之前/那个"等 → 记忆问题
    2. 含"怎么/什么是/为什么/最佳实践"等 → 知识问题
    3. 两者都有 → 混合
    """
    query_lower = query.lower()

    # 记忆信号检测：关系词 + 项目关键词
    memory_signals = 0
    for word in MEMORY_SIGNAL_WORDS:
        if word in query_lower:
            memory_signals += 1
    # 项目关键词也是记忆信号（提到项目名的查询大概率需要查记忆）
    project_signals = 0
    for word in PROJECT_SIGNAL_WORDS:
        if word in query_lower:
            project_signals += 1
    memory_signals += project_signals

    # 知识信号检测
    knowledge_signals = 0
    for pattern in KNOWLEDGE_SIGNAL_PATTERNS:
        if re.search(pattern, query):
            knowledge_signals += 1

    is_memory = memory_signals > 0 or (
        # 无明确信号时，短问题（<20 字）更可能是对话延续，倾向记忆
        len(query) < 20 and not knowledge_signals
    )
    is_knowledge = knowledge_signals > 0 or (
        # 长问题（>50 字）且无记忆信号，倾向知识
        len(query) > 50 and not memory_signals
    )

    # 无任何信号时：默认查记忆（搜一下不费事，漏掉就亏了）
    if not is_memory and not is_knowledge:
        is_memory = True

    # 置信度
    if memory_signals and knowledge_signals:
        confidence = 0.90
    elif project_signals and not memory_signals:
        # 只有项目关键词，没有关系词 → 中等置信度查记忆
        confidence = 0.70
        is_memory = True
        is_knowledge = knowledge_signals > 0
    elif memory_signals or knowledge_signals:
        confidence = 0.75
    else:
        confidence = 0.50  # 无信号，默认走记忆

    return RouteDecision(is_memory=is_memory, is_knowledge=is_knowledge, confidence=confidence)


# ── TF-IDF 关键词检索 ────────────────────────────────

class TFIDFRetriever:
    """轻量 TF-IDF 检索器（无外部依赖，纯 Python）"""

    def __init__(self):
        self._documents: dict[str, str] = {}        # id → content
        self._inverted_index: dict[str, set[str]] = defaultdict(set)  # term → set of ids
        self._term_df: dict[str, int] = defaultdict(int)  # term → document frequency
        self._total_docs = 0
        self._dirty = False

    def add(self, memory_id: str, content: str):
        """添加文档到索引"""
        # 移除旧条目
        if memory_id in self._documents:
            self._remove_from_index(memory_id, self._documents[memory_id])

        self._documents[memory_id] = content
        terms = self._tokenize(content)
        seen = set()
        for term in terms:
            if term not in seen:
                self._term_df[term] = self._term_df.get(term, 0) + 1
                seen.add(term)
            self._inverted_index[term].add(memory_id)
        self._total_docs += 1
        self._dirty = True

    def remove(self, memory_id: str):
        """从索引移除"""
        if memory_id in self._documents:
            self._remove_from_index(memory_id, self._documents[memory_id])
            del self._documents[memory_id]
            self._total_docs -= 1
            self._dirty = True

    def _remove_from_index(self, memory_id: str, content: str):
        terms = self._tokenize(content)
        for term in set(terms):
            if term in self._inverted_index:
                self._inverted_index[term].discard(memory_id)
                if not self._inverted_index[term]:
                    del self._inverted_index[term]
                    self._term_df.pop(term, None)

    def search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        """搜索，返回 [(memory_id, tfidf_score), ...]"""
        query_terms = self._tokenize(query)
        if not query_terms or self._total_docs == 0:
            return []

        # 计算每个候选文档的 TF-IDF 分数
        scores: dict[str, float] = defaultdict(float)
        for term in query_terms:
            if term not in self._inverted_index:
                continue
            idf = math.log((self._total_docs + 1) / (self._term_df[term] + 1)) + 1
            for doc_id in self._inverted_index[term]:
                # TF = 词在文档中出现次数
                doc_text = self._documents.get(doc_id, "")
                tf = doc_text.lower().count(term)
                scores[doc_id] += tf * idf

        # 按分数排序
        sorted_results = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_results[:top_k]

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """中文+英文分词：unigram + bigram（jieba 不可用时的 fallback）

        中文按字切分并生成相邻二元组，英文按词切。
        """
        text = text.lower()
        tokens = []
        segments = re.findall(r'[㐀-䶿一-鿿豈-﫿]|[a-z0-9]+', text)
        # 收集 CJK 字符用于 bigram 生成
        cjk_chars = []
        for seg in segments:
            if re.match(r'[㐀-䶿一-鿿豈-﫿]', seg):
                # 中文字符：收集后统一生成 unigram + bigram
                cjk_chars.append(seg)
            else:
                # 先 flush CJK bigrams
                if cjk_chars:
                    tokens.extend(cjk_chars)  # unigram
                    if len(cjk_chars) >= 2:
                        tokens.extend(
                            cjk_chars[i] + cjk_chars[i + 1]
                            for i in range(len(cjk_chars) - 1)
                        )  # bigram
                    cjk_chars.clear()
                # 英文/数字：>=2 字符才保留
                if len(seg) >= 2:
                    tokens.append(seg)
        # 末尾剩余 CJK
        if cjk_chars:
            tokens.extend(cjk_chars)
            if len(cjk_chars) >= 2:
                tokens.extend(
                    cjk_chars[i] + cjk_chars[i + 1]
                    for i in range(len(cjk_chars) - 1)
                )
        return tokens

    def rebuild(self, documents: dict[str, str]):
        """全量重建索引"""
        self._documents = {}
        self._inverted_index = defaultdict(set)
        self._term_df = defaultdict(int)
        self._total_docs = 0
        for doc_id, content in documents.items():
            self.add(doc_id, content)
        self._dirty = False


# ── RRF 融合 ─────────────────────────────────────────

def reciprocal_rank_fusion(
    *rankings: list[tuple[str, float]],
    k: int = RRF_K,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion — 合并多个排序列表

    RRF_score(d) = Σ 1 / (k + rank_i(d))
    """
    scores: dict[str, float] = defaultdict(float)

    for ranking in rankings:
        for rank, (doc_id, _) in enumerate(ranking, start=1):
            scores[doc_id] += 1.0 / (k + rank)

    # 按 RRF 分数降序
    sorted_results = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return sorted_results


# ── 检索协调器 ───────────────────────────────────────

class MemoryRetriever:
    """记忆检索统一入口"""

    def __init__(self, store: MemoryStore):
        self.store = store
        self.tfidf = TFIDFRetriever()
        self._index_built = False

    def build_index(self):
        """从 SQLite 全量构建 TF-IDF 索引"""
        entries = self.store.sqlite.list_all(status="active")
        docs = {e.id: e.content for e in entries}
        self.tfidf.rebuild(docs)
        self._index_built = True
        logger.info("TF-IDF index built: %d documents", len(docs))

    def add_to_index(self, memory_id: str, content: str):
        """增量添加"""
        self.tfidf.add(memory_id, content)

    def remove_from_index(self, memory_id: str):
        """增量删除"""
        self.tfidf.remove(memory_id)

    def retrieve(
        self,
        query: str,
        *,
        project_id: Optional[str] = None,
        layer: Optional[str] = None,
        max_candidates: int = MAX_CANDIDATES,
        max_injected: int = MAX_INJECTED,
    ) -> list[tuple[MemoryEntry, float]]:
        """主检索入口：双路召回 → RRF 融合 → 排序

        Returns:
            [(MemoryEntry, final_score), ...] 按分数降序
        """
        if not self._index_built:
            self.build_index()

        # 1. 关键词召回（FTS5 → TF-IDF → LIKE 三级降级）
        keyword_results: list[tuple[str, float]] = self._keyword_recall(query, layer=layer, project_id=project_id)

        # 2. 语义向量召回
        vector_results_raw = self.store.search_vector(
            query,
            top_k=VECTOR_TOP_K,
            layer=layer,
            project_id=project_id,
        )
        # 过滤低于阈值的
        vector_results = [
            (e.id, sim) for e, sim in vector_results_raw
            if sim >= VECTOR_SIMILARITY_THRESHOLD
        ]

        # 3. RRF 融合
        fused = reciprocal_rank_fusion(keyword_results, vector_results)

        # 4. 解析为 MemoryEntry + score（去重：同一 memory_id 只保留一个）
        candidates: list[tuple[MemoryEntry, float]] = []
        seen_ids: set[str] = set()
        for memory_id, rrf_score in fused[:max_candidates]:
            if memory_id in seen_ids:
                continue
            seen_ids.add(memory_id)
            entry = self.store.sqlite.get(memory_id)
            if entry and entry.status == "active":
                # 项目过滤（在检索后做，因为向量搜索可能带了 filter）
                if project_id and entry.project_id and entry.project_id != project_id:
                    continue
                candidates.append((entry, rrf_score))

        # 5. 按 RRF 分数排序，当前有效优先 → 近期过期 → 长期过期
        def _sort_key(item: tuple[MemoryEntry, float]) -> tuple[int, float]:
            entry, rrf_score = item
            # 时态优先级：0=当前有效, 1=近期过期, 2=长期过期
            now = datetime.now(timezone.utc)
            temporal_rank = 0
            if entry.valid_until:
                try:
                    vu = datetime.fromisoformat(entry.valid_until)
                    if vu.tzinfo is None:
                        vu = vu.replace(tzinfo=timezone.utc)
                    if vu <= now:
                        days = (now - vu).days
                        temporal_rank = 1 if days <= 30 else 2
                except (ValueError, TypeError):
                    pass
            # temporal_rank 越小越好（当前有效=0），rrf_score 越大越好 → 取负
            return (temporal_rank, -rrf_score)

        candidates.sort(key=_sort_key)
        return candidates[:max_injected]

    def search_by_keyword(
        self, keyword: str, limit: int = 10
    ) -> list[MemoryEntry]:
        """纯关键词搜索（调试用）"""
        return self.store.sqlite.search_content(keyword, limit=limit)

    def search_by_tags(self, tags: list[str]) -> list[MemoryEntry]:
        """按标签搜索"""
        return self.store.sqlite.search_by_tags(tags)

    # ── 降级链 ───────────────────────────────────────

    def _keyword_recall(
        self,
        query: str,
        *,
        layer: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> list[tuple[str, float]]:
        """关键词召回：FTS5 → TF-IDF → LIKE 三级降级

        每层独立故障隔离：上一层失败自动降级到下一层。
        """
        # Level 1: FTS5 + jieba（最佳中文搜索质量）
        fts_results = self.store.sqlite.search_fts(
            query, limit=TFIDF_TOP_K, layer=layer, project_id=project_id
        )
        if fts_results:
            logger.debug("keyword recall: FTS5 (%d results)", len(fts_results))
            return [(e.id, score) for e, score in fts_results]

        # Level 2: TF-IDF（内存索引，重启后需重建）
        if self._index_built:
            tfidf_results = self.tfidf.search(query, top_k=TFIDF_TOP_K)
            if tfidf_results:
                logger.debug("keyword recall: TF-IDF fallback (%d results)", len(tfidf_results))
                return tfidf_results

        # Level 3: SQLite LIKE（最后手段）
        try:
            entries = self.store.sqlite.search_content(query, limit=TFIDF_TOP_K)
            if entries:
                logger.debug("keyword recall: LIKE fallback (%d results)", len(entries))
                return [(e.id, 0.3) for e in entries]  # 低默认分，让向量结果主导
        except Exception:
            pass

        logger.debug("keyword recall: no results from any level")
        return []
