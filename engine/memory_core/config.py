"""
memory_core 配置中心

所有可调参数、路径、阈值统一管理。
"""

from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass, field

# ── 路径 ──────────────────────────────────────────────

DEFAULT_DATA_DIR = Path(os.environ.get(
    "MEMORY_CORE_DATA",
    str(Path.home() / ".memory_core")
))

DEFAULT_SQLITE_PATH = DEFAULT_DATA_DIR / "memory.db"
DEFAULT_LANCEDB_PATH = DEFAULT_DATA_DIR / "vectors"

# ── 嵌入模型 ──────────────────────────────────────────

EMBEDDING_MODEL_NAME = os.environ.get(
    "MEMORY_CORE_EMBEDDING_MODEL",
    "BAAI/bge-small-zh-v1.5"      # 96MB, Chinese-optimized, 512 dim
)
EMBEDDING_DIM = 512               # BGE-small-zh output dim
EMBEDDING_BATCH_SIZE = 32

# BGE 模型推荐：query 加 "为这个句子生成表示以用于检索相关文章：" 前缀
# https://huggingface.co/BAAI/bge-small-zh-v1.5
BGE_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

# ── 记忆层级定义 ──────────────────────────────────────

LAYERS = ["L0", "L1", "L2", "L3", "L4", "L5", "LCM"]
LAYER_LABELS = {
    "L0": "凭据",
    "L1": "最高原则",
    "L2": "用户画像",
    "L3": "行为偏好",
    "L4": "工作知识",
    "L5": "关系记忆",
    "LCM": "Agent能力自画像",
}
CATEGORIES = [
    "principle",       # L1/L2 原则
    "identity",        # L2 身份
    "preference",      # L3 偏好
    "knowledge",       # L4 通用知识（兜底）
    "project",         # L4 项目
    "infrastructure",  # L4 基础设施
    "pitfall",         # L4 踩坑
    "research",        # L4 研究
    "archive",         # L4 归档
    "relationship",    # L5 关系
]
SCOPES = ["global", "project", "session"]

# ── Pending 标记（跨模块共用） ──────────────────────────

_PENDING_MARKERS = [
    "待验证", "未完成", "TODO", "⚠️", "⚠",
    "待办", "未测试", "pending", "还没做", "尚未",
    "需要验证", "需要测试", "还没测", "没做",
    "未验证", "未修复", "待修复",
]

# ── 加载优先级（按层级） ──────────────────────────────

LOAD_PRIORITY = {
    "L1": 100,     # 常驻内存，每次启动全文注入
    "L2": 90,      # 常驻内存，摘要注入
    "L3": 80,      # 常驻内存，标题列表
    "L4": 60,      # 项目摘要热缓存，详细按需
    "L5": 40,      # 按需检索
}
ARCHIVED_BASE_PRIORITY = 10

# ── 时间衰减曲线 ──────────────────────────────────────

# 按时间距离衰减权重
DECAY_SCHEDULE = [
    (0,    1.0),    # 当前会话
    (1,    0.95),   # 1 天内
    (3,    0.85),   # 1-3 天
    (7,    0.70),   # 4-7 天
    (30,   0.50),   # 8-30 天
    (90,   0.30),   # 1-3 月
    (365,  0.10),   # 3 月-1 年
    (float("inf"), 0.05),  # 1 年以上
]

# 自动归档阈值：超过此天数未访问 → 归档
AUTO_ARCHIVE_DAYS = 365
# 自动过期阈值：超过此天数未访问 → 过期
AUTO_EXPIRE_DAYS = 730

# ── 时态有效性配置 ──────────────────────────────────────

# valid_until 已过期 → 权重乘以此系数
TEMPORAL_PENALTY_EXPIRED = 0.3
# valid_until 近期过期（≤TEMPORAL_RECENT_DAYS 天）→ 权重乘以此系数
TEMPORAL_PENALTY_RECENT = 0.7
# "近期过期"的天数阈值
TEMPORAL_RECENT_DAYS = 30

# ── 检索配置 ──────────────────────────────────────────

# 检索返回的最大候选记忆数
MAX_CANDIDATES = 20
# 注入上下文的最大记忆数
MAX_INJECTED = 5
# 向量搜索 top-k
VECTOR_TOP_K = 20
# TF-IDF 搜索 top-k
TFIDF_TOP_K = 20
# RRF (Reciprocal Rank Fusion) 参数 k
RRF_K = 60
# 向量相似度阈值（低于此值不返回）
VECTOR_SIMILARITY_THRESHOLD = 0.30  # BGE-small-zh: 中文语义匹配阈值

# ── Query Router 配置 ─────────────────────────────────

# 记忆问题信号词（含这些词的优先查记忆）
MEMORY_SIGNAL_WORDS = [
    "咱们", "上次", "之前", "上回", "上个月", "上周", "那个",
    "你记得", "你还记得", "记不记得", "之前说的", "之前聊的",
    "之前修的", "之前改的", "踩过", "坑过", "搞过",
]

# 项目关键词 — 含这些词的查询也视为有记忆需求
PROJECT_SIGNAL_WORDS = [
    "灵犀", "打包", "安装包", "绿色安装", "electron",
    "因子", "回测", "策略", "组合", "持仓",
    "memory_core", "记忆系统", "记忆内核",
    "codex", "小扣", "cc-switch",
    "skillmatch", "时光机", "看板", "行业",
    "微信桥", "dakou", "bridge",
    "tushare", "duckdb", "streamlit",
    "端口", "配置", "部署", "发布",
    "bug", "报错", "踩坑", "修复", "排查",
]

# ── 使用判断配置 ──────────────────────────────────────

# 确认度分层阈值
CONFIDENCE_HIGH = 0.80      # 高于此值直接引用
CONFIDENCE_MEDIUM = 0.50    # 高于此值加"可能"引用
# 低于 MEDIUM → 不引用

# 基础权重来源（按层级 + 分类）
BASE_WEIGHTS = {
    ("L1", "principle"):       1.0,
    ("L2", "identity"):        0.95,
    ("L3", "preference"):      0.90,
    ("L4", "knowledge"):       0.80,
    ("L4", "project"):         0.80,
    ("L4", "infrastructure"):  0.80,
    ("L4", "pitfall"):         0.80,
    ("L4", "research"):        0.80,
    ("L4", "archive"):         0.60,
    ("L5", "relationship"):    0.70,
}

# ── Consolidation 配置 ────────────────────────────────

# 触发 consolidation 的最大对话轮数（超过此轮数自动触发摘要）
AUTO_CONSOLIDATE_TURNS = 20

# ── 性能配置 ──────────────────────────────────────────

# TF-IDF 向量化器缓存
TFIDF_MAX_FEATURES = 5000
# 检索超时（毫秒）
RETRIEVAL_TIMEOUT_MS = 200


@dataclass
class MemoryCoreConfig:
    """运行时可覆写的配置对象"""

    data_dir: Path = field(default_factory=lambda: DEFAULT_DATA_DIR)
    sqlite_path: Path | None = None
    lancedb_path: Path | None = None
    embedding_model: str = EMBEDDING_MODEL_NAME
    embedding_dim: int = EMBEDDING_DIM

    max_candidates: int = MAX_CANDIDATES
    max_injected: int = MAX_INJECTED
    vector_top_k: int = VECTOR_TOP_K
    tfidf_top_k: int = TFIDF_TOP_K
    vector_similarity_threshold: float = VECTOR_SIMILARITY_THRESHOLD

    confidence_high: float = CONFIDENCE_HIGH
    confidence_medium: float = CONFIDENCE_MEDIUM

    auto_archive_days: int = AUTO_ARCHIVE_DAYS
    auto_expire_days: int = AUTO_EXPIRE_DAYS

    def __post_init__(self):
        # Accept string paths — convert to Path
        if isinstance(self.data_dir, str):
            self.data_dir = Path(self.data_dir)
        if self.sqlite_path is not None and isinstance(self.sqlite_path, str):
            self.sqlite_path = Path(self.sqlite_path)
        if self.lancedb_path is not None and isinstance(self.lancedb_path, str):
            self.lancedb_path = Path(self.lancedb_path)

        if self.sqlite_path is None:
            self.sqlite_path = self.data_dir / "memory.db"
        if self.lancedb_path is None:
            self.lancedb_path = self.data_dir / "vectors"
