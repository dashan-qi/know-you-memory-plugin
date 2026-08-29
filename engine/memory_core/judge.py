"""
memory_core 使用判断层（核心创新）

检索返回 top-N 候选记忆 → 五道判断 → 决定注入哪些、怎么注入。

五维判断：
1. 意图匹配 — 表面问题 vs 真实意图
2. 情境兼容 — 当前项目上下文 × 记忆所属项目
3. 确认度分层 — 高/中/低 → 不同处理策略
4. 冲突检测 — 两条记忆矛盾时判断取舍
5. 用后反馈 — 引用后用户反应回传调整权重

MVP: 全部启发式，不调 LLM。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .store import MemoryEntry, FeedbackLog, SQLiteManager
from .config import (
    CONFIDENCE_HIGH, CONFIDENCE_MEDIUM,
    TEMPORAL_PENALTY_EXPIRED, TEMPORAL_PENALTY_RECENT, TEMPORAL_RECENT_DAYS,
)

logger = logging.getLogger("memory_core.judge")


# ── 判断结果 ──────────────────────────────────────────

class Verdict:
    """单条记忆的判断结果"""

    __slots__ = (
        "entry", "score", "intent_match", "context_ok",
        "confidence_tier", "has_conflict", "should_inject",
        "inject_mode", "reason",
    )

    def __init__(
        self,
        entry: MemoryEntry,
        score: float,
        intent_match: bool = True,
        context_ok: bool = True,
        confidence_tier: str = "medium",
        has_conflict: bool = False,
        inject_mode: str = "direct",
        reason: str = "",
    ):
        self.entry = entry
        self.score = score
        self.intent_match = intent_match
        self.context_ok = context_ok
        self.confidence_tier = confidence_tier
        self.has_conflict = has_conflict
        self.inject_mode = inject_mode  # direct / hedged / skip
        self.should_inject = inject_mode != "skip"
        self.reason = reason

    def __repr__(self):
        return (
            f"Verdict({self.entry.id}, tier={self.confidence_tier}, "
            f"inject={self.inject_mode}, score={self.score:.3f})"
        )


def _determine_confidence_tier(
    entry: MemoryEntry,
    retrieval_score: float = 0.5,
    has_vectors: bool = True,
) -> str:
    """根据记忆的 confidence、weight 和检索分数确定确认度层级

    检索分数是 RRF 融合后的分数。RRF 典型范围：0.015-0.060（双路）。
    好的向量匹配会产生更高的 RRF（两路都有贡献）。

    has_vectors 决定用哪套阈值：
    - True（双路 TF-IDF + 向量，生产默认）：保持 strong>=0.030 / moderate>=0.025，
      只对两路都有命中的结果给高置信度。
    - False（单路，零第三方依赖环境纯 TF-IDF）：RRF 只来自一路 rank 项，
      最大值约 1/(k+1)=1/61≈0.0164。若沿用双路阈值，任何结果都会被判 low →
      skip，recall 恒为空。这里放宽到单路量级：strong>=0.014（≈rank1-2）、
      moderate>=0.008（≈rank8 及更优）。
    """
    if has_vectors:
        retrieval_strong = retrieval_score >= 0.030
        retrieval_moderate = retrieval_score >= 0.025
    else:
        # 单路 TF-IDF：RRF rank1≈0.0164 即强匹配；0.008≈rank8 及以上为中等
        retrieval_strong = retrieval_score >= 0.014
        retrieval_moderate = retrieval_score >= 0.008

    if entry.confidence >= CONFIDENCE_HIGH and entry.weight >= 0.5 and retrieval_strong:
        return "high"
    elif entry.confidence >= CONFIDENCE_MEDIUM and entry.weight >= 0.2 and retrieval_moderate:
        return "medium"
    elif retrieval_strong:
        # 检索强匹配但 entry 权重低 → 中等置信度
        return "medium"
    return "low"


# ── 1. 意图匹配 ──────────────────────────────────────

# 表面问题 → 可能真实意图的领域词库
INTENT_MAP: dict[str, list[str]] = {
    # 表面问"怎么加速" → 实际是"因子预览太慢"
    "加速": ["性能", "慢", "卡", "优化", "预览", "加载"],
    "优化": ["性能", "慢", "卡", "加速"],
    "查询": ["数据库", "SQL", "duckdb", "sqlite", "索引"],
    "报错": ["bug", "错误", "修复", "排查", "踩坑"],
    "配置": ["settings", "config", "参数", "端口", "token"],
    "安装": ["pip", "npm", "依赖", "环境", "版本"],
    "部署": ["打包", "打包", "发布", "exe", "dist"],
    "修改": ["改代码", "重构", "优化", "调整"],
}


def check_intent_match(query: str, entry: MemoryEntry) -> bool:
    """检查记忆是否匹配用户的真实意图（而非仅表面关键词）

    简单的交叉验证：用户问题中的领域词 × 记忆内容中的领域词
    """
    query_lower = query.lower()

    # 提取查询中的意图信号
    query_intents = set()
    for surface, intents in INTENT_MAP.items():
        if surface in query_lower:
            query_intents.update(intents)

    if not query_intents:
        return True  # 没有特殊意图信号，默认通过

    # 检查记忆内容是否覆盖任一意图
    content_lower = entry.content.lower()
    for intent in query_intents:
        if intent in content_lower:
            return True

    # 没有命中意图词库，不意味着不匹配，只是置信度降低
    return True  # MVP 阶段不拦截，仅降低置信度（在外层处理）


# ── 2. 情境兼容 ──────────────────────────────────────


def check_context_compatibility(
    entry: MemoryEntry,
    current_project_id: Optional[str] = None,
) -> bool:
    """检查记忆是否兼容当前项目上下文

    规则：
    - scope="global" 的记忆始终兼容
    - scope="project" 的记忆需要 project_id 匹配
    - scope="session" 的记忆只在当前会话有效（在外层处理）
    """
    if entry.scope == "global":
        return True

    if entry.scope == "project":
        if current_project_id is None:
            return True  # 无当前项目上下文时不过滤
        if entry.project_id is None:
            return True  # 未标记项目的记忆不过滤
        return entry.project_id == current_project_id

    # session 作用域的记忆由外层根据 session_id 过滤
    return True


# ── 3. 确认度分层 ────────────────────────────────────


def determine_inject_mode(
    entry: MemoryEntry,
    retrieval_score: float = 0.5,
    has_vectors: bool = True,
) -> str:
    """根据确认度决定注入方式

    - high: 直接引用，不加限定词
    - medium: 加"可能""之前提到过"等限定词
    - low: 不注入（skip）

    has_vectors: 是否双路（TF-IDF + 向量）。False 时用单路 RRF 阈值，
        见 _determine_confidence_tier，否则零依赖环境 recall 恒为空。
    """
    tier = _determine_confidence_tier(entry, retrieval_score, has_vectors=has_vectors)
    if tier == "high":
        return "direct"
    elif tier == "medium":
        return "hedged"
    else:
        return "skip"


# ── 4. 冲突检测 ──────────────────────────────────────


def detect_conflicts(
    entry: MemoryEntry,
    candidates: list[MemoryEntry],
    sqlite: SQLiteManager,
) -> list[MemoryEntry]:
    """检测候选记忆之间是否存在冲突

    1. 先查 edges 表中有没有 contradicts 关系的边
    2. 再查同 key（同 project + 同 category + 同 tags）的多个版本

    Returns:
        与 entry 冲突的其他记忆列表
    """
    conflicts = []

    # 1. 查边表中的 contradicts 关系
    contradictions = sqlite.find_contradictions(entry.id)
    for c in contradictions:
        if c.status == "active" and c.id in {e.id for e in candidates}:
            conflicts.append(c)

    # 2. 同标签 + 同项目 + 同类别 → 可能是同一事实的多个版本
    if entry.tags and entry.project_id:
        for other in candidates:
            if other.id == entry.id:
                continue
            same_tags = bool(set(entry.tags) & set(other.tags or []))
            same_project = entry.project_id == other.project_id
            same_category = entry.category == other.category
            if same_tags and same_project and same_category:
                # 检查内容是否矛盾（简单启发式：一条包含否定词另一条不包含）
                neg_words = ["不", "不是", "不要", "不能", "禁用", "废弃", "已删除", "已移除"]
                e1_neg = any(w in entry.content for w in neg_words)
                e2_neg = any(w in other.content for w in neg_words)
                if e1_neg != e2_neg:
                    conflicts.append(other)

    return conflicts


def resolve_conflict(
    entry_a: MemoryEntry,
    entry_b: MemoryEntry,
) -> MemoryEntry:
    """冲突解决：优先取当前有效的，其次取更新时间更近的

    策略：
    1. 一条当前有效、另一条已过期 → 取当前有效的
    2. 都有效或都过期 → 取 updated_at 更近的
    """
    now = datetime.now(timezone.utc)
    vu_a = _parse_iso_utc(entry_a.valid_until)
    vu_b = _parse_iso_utc(entry_b.valid_until)

    a_current = vu_a is None or vu_a > now
    b_current = vu_b is None or vu_b > now

    if a_current and not b_current:
        return entry_a
    if b_current and not a_current:
        return entry_b

    # 同等时态 → 按更新时间
    return entry_a if entry_a.updated_at >= entry_b.updated_at else entry_b


# ── 5. 用后反馈 ──────────────────────────────────────


def apply_feedback(
    sqlite: SQLiteManager,
    memory_id: str,
    action: str,
    session_id: str,
    context_query: Optional[str] = None,
):
    """记录记忆使用反馈并调整权重

    action 影响：
    - used:     权重 +0.05
    - ignored:  权重 -0.03
    - corrected: 权重不变，降低 confidence
    - deleted:  权重 → 0，标记 archived
    """
    feedback = FeedbackLog(
        memory_id=memory_id,
        session_id=session_id,
        action=action,
        context_query=context_query,
    )
    sqlite.log_feedback(feedback)

    entry = sqlite.get(memory_id)
    if not entry:
        return

    if action == "used":
        new_weight = min(1.0, entry.weight + 0.05)
        sqlite.update(memory_id, weight=new_weight)
    elif action == "ignored":
        new_weight = max(0.0, entry.weight - 0.03)
        sqlite.update(memory_id, weight=new_weight)
    elif action == "corrected":
        new_confidence = max(0.1, entry.confidence - 0.15)
        sqlite.update(memory_id, confidence=new_confidence)
    elif action == "deleted":
        sqlite.update(memory_id, weight=0.0, status="archived")

    logger.info(
        "Feedback applied: %s → %s, new weight=%.3f",
        memory_id, action,
        (sqlite.get(memory_id) or entry).weight,
    )


# ── 判断协调器 ───────────────────────────────────────


def _parse_iso_utc(iso_str: str | None) -> datetime | None:
    """解析 ISO 8601 字符串为 aware UTC datetime，失败返回 None"""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def check_temporal_validity(entry: MemoryEntry) -> tuple[bool, float, str]:
    """检查记忆的时态有效性

    Returns:
        (is_valid, penalty_factor, reason)
        - is_valid: 当前是否可用（valid_from 在未来 → False）
        - penalty_factor: 1.0 正常, 0.7 近期过期, 0.3 长期过期
        - reason: 人类可读的原因说明
    """
    now = datetime.now(timezone.utc)

    # valid_from 在未来 → 不可用
    vf = _parse_iso_utc(entry.valid_from)
    if vf and vf > now:
        return (False, 0.0, f"尚未生效 (valid_from={entry.valid_from})")

    # valid_until 已过 → 降权
    vu = _parse_iso_utc(entry.valid_until)
    if vu and vu <= now:
        days_expired = (now - vu).days
        if days_expired <= TEMPORAL_RECENT_DAYS:
            return (True, TEMPORAL_PENALTY_RECENT,
                    f"近期过期 (valid_until={entry.valid_until}, {days_expired}天前)")
        else:
            return (True, TEMPORAL_PENALTY_EXPIRED,
                    f"已过期 (valid_until={entry.valid_until}, {days_expired}天前)")

    return (True, 1.0, "当前有效")


@dataclass
class JudgmentResult:
    """一次检索的完整判断结果"""
    query: str
    verdicts: list[Verdict] = field(default_factory=list)
    conflicts_resolved: int = 0

    @property
    def to_inject(self) -> list[Verdict]:
        """应注入的判决列表"""
        return [v for v in self.verdicts if v.should_inject]

    @property
    def direct(self) -> list[Verdict]:
        return [v for v in self.to_inject if v.inject_mode == "direct"]

    @property
    def hedged(self) -> list[Verdict]:
        return [v for v in self.to_inject if v.inject_mode == "hedged"]


class MemoryJudge:
    """记忆使用判断统一入口"""

    def __init__(self, store):
        from .store import MemoryStore
        self.store: MemoryStore = store

    def judge(
        self,
        query: str,
        candidates: list[tuple[MemoryEntry, float]],
        *,
        current_project_id: Optional[str] = None,
    ) -> JudgmentResult:
        """对候选记忆逐一判断，返回带判决的结果

        Args:
            query: 用户原始提问
            candidates: [(entry, retrieval_score), ...] 来自 retrieve()
            current_project_id: 当前活跃项目 ID

        Returns:
            JudgmentResult 含所有判决
        """
        result = JudgmentResult(query=query)
        all_entries = [e for e, _ in candidates]
        # 单路（纯 TF-IDF，零第三方依赖）时用放宽的 RRF 阈值，否则 recall 恒为空
        has_vectors = self.store.lancedb is not None

        for entry, score in candidates:
            # 1. 意图匹配
            intent_ok = check_intent_match(query, entry)

            # 2. 情境兼容
            context_ok = check_context_compatibility(entry, current_project_id)

            # 3. 时态有效性检查
            temporal_valid, temporal_factor, temporal_reason = check_temporal_validity(entry)
            if not temporal_valid:
                # valid_from 在未来 → 直接跳过
                verdict = Verdict(
                    entry=entry, score=score,
                    intent_match=intent_ok, context_ok=context_ok,
                    confidence_tier="low", has_conflict=False,
                    inject_mode="skip",
                    reason=f"时态无效: {temporal_reason}",
                )
                result.verdicts.append(verdict)
                continue

            # 4. 确认度分层 → 注入模式（含检索分数）
            inject_mode = determine_inject_mode(entry, score, has_vectors=has_vectors)

            # 时态降权：过期记忆降低注入级别
            if temporal_factor < 1.0:
                if inject_mode == "direct":
                    inject_mode = "hedged"
                elif inject_mode == "hedged" and temporal_factor <= TEMPORAL_PENALTY_EXPIRED:
                    inject_mode = "skip"

            # 5. 冲突检测
            conflicts = detect_conflicts(entry, all_entries, self.store.sqlite)
            if conflicts:
                # 冲突解决：优先取当前有效的
                winner = resolve_conflict(entry, conflicts[0])
                if winner.id != entry.id:
                    inject_mode = "skip"

            # 构建判决
            verdict = Verdict(
                entry=entry,
                score=score,
                intent_match=intent_ok,
                context_ok=context_ok,
                confidence_tier=_determine_confidence_tier(entry, score, has_vectors=has_vectors),
                has_conflict=bool(conflicts),
                inject_mode=inject_mode,
                reason=self._build_reason(intent_ok, context_ok, bool(conflicts)),
            )
            result.verdicts.append(verdict)
            if conflicts:
                result.conflicts_resolved += 1

        return result

    def feedback(
        self,
        memory_id: str,
        action: str,
        session_id: str,
        context_query: Optional[str] = None,
    ):
        """记录使用反馈"""
        apply_feedback(
            self.store.sqlite,
            memory_id=memory_id,
            action=action,
            session_id=session_id,
            context_query=context_query,
        )

    @staticmethod
    def _build_reason(intent_ok: bool, context_ok: bool, has_conflict: bool) -> str:
        parts = []
        if not intent_ok:
            parts.append("意图不匹配")
        if not context_ok:
            parts.append("情境不兼容")
        if has_conflict:
            parts.append("存在冲突")
        return "; ".join(parts) if parts else "通过"
