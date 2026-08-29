"""
memory_core 存储层 — SQLite + LanceDB

三层存储：
- SQLite: 结构化记忆条目、关联边、反馈日志
- LanceDB: 语义向量嵌入
- 内存缓存: 热数据（后续 Phase 实现）
"""

from __future__ import annotations

import json
import uuid
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Iterator

# 向量层为可选增强路径：lancedb/numpy 不可用时降级为纯 SQLite 核心，
# 保证 `import memory_core` 在零第三方依赖环境（纯 stdlib）下可用。
try:
    import lancedb
    import numpy as np
    from lancedb.table import Table as LanceTable
    _VECTOR_AVAILABLE = True
except ImportError:
    _VECTOR_AVAILABLE = False

from .config import (
    MemoryCoreConfig,
    DEFAULT_DATA_DIR,
    DEFAULT_SQLITE_PATH,
    DEFAULT_LANCEDB_PATH,
    EMBEDDING_DIM,
    LAYERS,
    CATEGORIES,
    SCOPES,
    BGE_QUERY_PREFIX,
)

logger = logging.getLogger("memory_core.store")


# ── 工具函数 ──────────────────────────────────────────

def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串"""
    return datetime.now(timezone.utc).isoformat()


def _days_ago_iso(days: int) -> str:
    """返回 N 天前的 ISO 时间字符串"""
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ── 数据模型 ──────────────────────────────────────────

class MemoryEntry:
    """记忆条目"""

    __slots__ = (
        "id", "name", "content", "layer", "category", "scope", "project_id",
        "tags", "created_at", "updated_at", "last_accessed_at",
        "access_count", "weight", "source_session_id", "confidence",
        "status", "metadata", "source_file", "valid_from", "valid_until",
    )

    def __init__(
        self,
        content: str,
        layer: str = "L4",
        category: str = "knowledge",
        scope: str = "global",
        project_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
        source_session_id: Optional[str] = None,
        confidence: float = 1.0,
        metadata: Optional[dict] = None,
        source_file: Optional[str] = None,
        *,
        id: Optional[str] = None,
        name: str = "",
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        last_accessed_at: Optional[str] = None,
        access_count: int = 0,
        weight: float = 1.0,
        status: str = "active",
        valid_from: Optional[str] = None,
        valid_until: Optional[str] = None,
    ):
        self.id = id or str(uuid.uuid4())[:12]
        self.name = name
        self.content = content
        self.layer = layer
        self.category = category
        self.scope = scope
        self.project_id = project_id
        self.tags = tags or []
        self.created_at = created_at or _now_iso()
        self.updated_at = updated_at or self.created_at
        self.last_accessed_at = last_accessed_at or self.created_at
        self.access_count = access_count
        self.weight = weight
        self.source_session_id = source_session_id
        self.confidence = confidence
        self.status = status
        self.metadata = metadata or {}
        self.source_file = source_file
        self.valid_from = valid_from
        self.valid_until = valid_until

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "content": self.content,
            "layer": self.layer,
            "category": self.category,
            "scope": self.scope,
            "project_id": self.project_id,
            "tags": json.dumps(self.tags, ensure_ascii=False),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_accessed_at": self.last_accessed_at,
            "access_count": self.access_count,
            "weight": self.weight,
            "source_session_id": self.source_session_id,
            "confidence": self.confidence,
            "source_file": self.source_file,
            "status": self.status,
            "metadata": json.dumps(self.metadata, ensure_ascii=False),
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
        }

    @classmethod
    def from_row(cls, row: dict) -> "MemoryEntry":
        tags = row.get("tags", "[]")
        if isinstance(tags, str):
            tags = json.loads(tags)

        meta = row.get("metadata", "{}")
        if isinstance(meta, str):
            meta = json.loads(meta)

        return cls(
            id=row["id"],
            name=row.get("name", ""),
            content=row["content"],
            layer=row["layer"],
            category=row["category"],
            scope=row.get("scope", "global"),
            project_id=row.get("project_id"),
            tags=tags,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_accessed_at=row["last_accessed_at"],
            access_count=row.get("access_count", 0),
            weight=row.get("weight", 1.0),
            source_session_id=row.get("source_session_id"),
            confidence=row.get("confidence", 1.0),
            status=row.get("status", "active"),
            metadata=meta,
            source_file=row.get("source_file") or None,
            valid_from=row.get("valid_from") or None,
            valid_until=row.get("valid_until") or None,
        )

    def __repr__(self):
        preview = self.content[:50].replace("\n", " ")
        return (
            f"MemoryEntry(id={self.id}, layer={self.layer}, "
            f"cat={self.category}, weight={self.weight:.2f}, "
            f"content='{preview}…')"
        )


# ── 关联边 ────────────────────────────────────────────

class Edge:
    """记忆之间的关联边"""

    __slots__ = ("id", "source_id", "target_id", "relation_type", "weight", "created_at")

    RELATION_TYPES = ("related_to", "contradicts", "extends", "replaces")

    def __init__(
        self,
        source_id: str,
        target_id: str,
        relation_type: str = "related_to",
        weight: float = 1.0,
        *,
        id: Optional[str] = None,
        created_at: Optional[str] = None,
    ):
        self.id = id or str(uuid.uuid4())[:12]
        self.source_id = source_id
        self.target_id = target_id
        self.relation_type = relation_type
        self.weight = weight
        self.created_at = created_at or _now_iso()


# ── 反馈日志 ──────────────────────────────────────────

class FeedbackLog:
    """记忆使用反馈"""

    __slots__ = ("id", "memory_id", "session_id", "action", "context_query", "timestamp")

    ACTIONS = ("used", "ignored", "corrected", "deleted")

    def __init__(
        self,
        memory_id: str,
        session_id: str,
        action: str,
        context_query: Optional[str] = None,
        *,
        id: Optional[str] = None,
        timestamp: Optional[str] = None,
    ):
        self.id = id or str(uuid.uuid4())[:12]
        self.memory_id = memory_id
        self.session_id = session_id
        self.action = action
        self.context_query = context_query
        self.timestamp = timestamp or _now_iso()


def _parse_frontmatter(text: str) -> tuple[str | None, str]:
    """解析并剥离所有 YAML frontmatter 块，返回 (name, body)

    会剥离文本中所有独立的 `---...---` 块（包括非开头位置的），
    只保留第一个 frontmatter 中的 name 字段。
    """
    import re as _re
    body = text
    fm_name = None

    # 反复剥离所有 frontmatter 块（可能在开头、中间、嵌套）
    max_iter = 10  # 安全上限
    for _ in range(max_iter):
        # 匹配独立的 --- 分隔符块：行首 --- 单独一行，到下一个行首 --- 单独一行
        m = _re.search(r'(^|\n)---\s*\n', body)
        if not m:
            break
        start_pos = m.start() + len(m.group(1))  # --- 的起始位置
        content_start = m.end()
        end = _re.search(r'\n---\s*(\n|$)', body[content_start:])
        if not end:
            break
        fm_block = body[content_start:content_start + end.start()]
        # 提取 name
        if fm_name is None:
            nm = _re.search(r'^name:\s*(\S+)', fm_block, _re.MULTILINE)
            if nm:
                fm_name = nm.group(1).strip()
        # 删除整个 frontmatter 块
        body = body[:start_pos] + body[content_start + end.end():]

    return fm_name, body.strip()


def _strip_frontmatter(text: str) -> str:
    """剥离所有前导 frontmatter，返回正文"""
    return _parse_frontmatter(text)[1]


def _extract_frontmatter_field(text: str, field: str) -> str | None:
    """从 YAML frontmatter 中提取指定字段值

    用于 sync_c_to_d() 解析 valid_from/valid_until 等时态字段。
    """
    import re as _re
    m = _re.search(r'^---\s*\n(.*?)\n---', text, _re.DOTALL)
    if not m:
        return None
    fm_block = m.group(1)
    nm = _re.search(rf'^{field}:\s*(.+)', fm_block, _re.MULTILINE)
    if nm:
        val = nm.group(1).strip()
        return val if val else None
    return None


# ── SQLite 管理器 ─────────────────────────────────────

# ── FTS5 搜索辅助 ────────────────────────────────────

def _segment_for_fts(text: str) -> str:
    """CJK 文本分词：jieba > bigram fallback > 原文本

    为 FTS5 索引准备分词后的文本，使中文可被正确搜索。
    """
    if not any('一' <= c <= '鿿' or '㐀' <= c <= '䶿'
               for c in text):
        return text  # 纯英文/数字，不需要分词

    # 尝试 jieba 分词
    try:
        import jieba
        return " ".join(jieba.cut(text))
    except ImportError:
        pass

    # jieba 不可用 → unigram + bigram fallback
    return _bigram_fallback(text)


def _bigram_fallback(text: str) -> str:
    """CJK unigram + bigram 分词（jieba 不可用时的 fallback）

    为每个中文字符生成 unigram 和 bigram 对，
    英文单词和数字保持原样。
    """
    import re
    tokens = []
    cjk_buffer = []

    def flush_buffer():
        if not cjk_buffer:
            return
        # unigram
        tokens.extend(cjk_buffer)
        # bigram
        if len(cjk_buffer) >= 2:
            tokens.extend(
                cjk_buffer[i] + cjk_buffer[i + 1]
                for i in range(len(cjk_buffer) - 1)
            )
        cjk_buffer.clear()

    for seg in re.findall(r'[㐀-䶿一-鿿豈-﫿]|[a-z0-9]+',
                          text.lower()):
        if re.match(r'[㐀-䶿一-鿿豈-﫿]', seg):
            cjk_buffer.append(seg)
        else:
            flush_buffer()
            if len(seg) >= 2:
                tokens.append(seg)
    flush_buffer()
    return " ".join(tokens)


def _build_fts_query(query: str) -> Optional[str]:
    """将用户查询转换为 FTS5 查询语法（分词 + OR 组合）

    中文分词后用 OR 连接各词，英文用双引号精确匹配。
    """
    import re
    segmented = _segment_for_fts(query)
    # 提取多字符 token
    tokens = re.findall(
        r"[a-zA-Z0-9一-鿿぀-ゟ゠-ヿ]{2,}",
        segmented
    )
    if not tokens:
        tokens = re.findall(
            r"[a-zA-Z0-9一-鿿぀-ゟ゠-ヿ]+",
            segmented
        )
    if not tokens:
        return None
    # 短语（含连字符的英文词）加双引号
    phrases = re.findall(r"[a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)+", segmented)
    quoted = [f'"{p}"' for p in phrases] + [f'"{t}"' for t in tokens]
    return " OR ".join(quoted)


def _bm25_to_score(rank: float) -> float:
    """BM25 rank → [0, 1] 分数"""
    if rank < 0:
        relevance = -rank
        return relevance / (1.0 + relevance)
    return 1.0 / (1.0 + rank)


def _sha256_file(content: str | bytes) -> str:
    """计算文件内容的 SHA256 哈希"""
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()

class SQLiteManager:
    """SQLite 数据库管理 — 结构化记忆存储"""

    def __init__(self, db_path: Optional[Path] = None):
        import sqlite3

        self.db_path = str(db_path or DEFAULT_SQLITE_PATH)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    def __del__(self):
        """GC 时兜底关闭连接，避免 Windows 下 db 文件被占用无法清理。

        Python 3.14 的 sqlite3 模块会用模块级 LRU 缓存持有连接对象，
        仅靠引用计数归零不会立即关闭底层文件句柄；这里显式 close，
        让未显式调用 close() 的调用方也能释放文件锁。
        """
        try:
            conn = getattr(self, "_conn", None)
            if conn is not None:
                conn.close()
        except Exception:
            pass

    def _create_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id              TEXT PRIMARY KEY,
                name            TEXT    NOT NULL DEFAULT '',
                content         TEXT    NOT NULL,
                layer           TEXT    NOT NULL DEFAULT 'L4',
                category        TEXT    NOT NULL DEFAULT 'knowledge',
                scope           TEXT    NOT NULL DEFAULT 'global',
                project_id      TEXT,
                tags            TEXT    NOT NULL DEFAULT '[]',
                created_at      TEXT    NOT NULL,
                updated_at      TEXT    NOT NULL,
                last_accessed_at TEXT   NOT NULL,
                access_count    INTEGER NOT NULL DEFAULT 0,
                weight          REAL    NOT NULL DEFAULT 1.0,
                source_session_id TEXT,
                confidence      REAL    NOT NULL DEFAULT 1.0,
                status          TEXT    NOT NULL DEFAULT 'active',
                metadata        TEXT    NOT NULL DEFAULT '{}',
                valid_from      TEXT,
                valid_until     TEXT,
                source_file     TEXT
            );

            CREATE TABLE IF NOT EXISTS edges (
                id              TEXT PRIMARY KEY,
                source_id       TEXT    NOT NULL,
                target_id       TEXT    NOT NULL,
                relation_type   TEXT    NOT NULL DEFAULT 'related_to',
                weight          REAL    NOT NULL DEFAULT 1.0,
                created_at      TEXT    NOT NULL,
                FOREIGN KEY (source_id) REFERENCES memories(id) ON DELETE CASCADE,
                FOREIGN KEY (target_id) REFERENCES memories(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS feedback_log (
                id              TEXT PRIMARY KEY,
                memory_id       TEXT    NOT NULL,
                session_id      TEXT    NOT NULL,
                action          TEXT    NOT NULL,
                context_query   TEXT,
                timestamp       TEXT    NOT NULL,
                FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_memories_layer     ON memories(layer);
            CREATE INDEX IF NOT EXISTS idx_memories_category  ON memories(category);
            CREATE INDEX IF NOT EXISTS idx_memories_scope     ON memories(scope);
            CREATE INDEX IF NOT EXISTS idx_memories_project   ON memories(project_id);
            CREATE INDEX IF NOT EXISTS idx_memories_status    ON memories(status);
            CREATE INDEX IF NOT EXISTS idx_memories_weight    ON memories(weight DESC);
            CREATE INDEX IF NOT EXISTS idx_edges_source       ON edges(source_id);
            CREATE INDEX IF NOT EXISTS idx_edges_target       ON edges(target_id);
            CREATE INDEX IF NOT EXISTS idx_feedback_memory    ON feedback_log(memory_id);
            CREATE INDEX IF NOT EXISTS idx_feedback_session   ON feedback_log(session_id);
        """)
        # 兼容旧库：加 name 列（必须在建索引之前）
        try:
            self._conn.execute("ALTER TABLE memories ADD COLUMN name TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass  # 列已存在
        # name 列索引（在 ALTER TABLE 之后）
        try:
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_name ON memories(name)")
        except Exception:
            pass
        # 兼容旧库：加 valid_from / valid_until 列（2026-07-15 时态建模）
        try:
            self._conn.execute("ALTER TABLE memories ADD COLUMN valid_from TEXT")
        except Exception:
            pass  # 列已存在
        try:
            self._conn.execute("ALTER TABLE memories ADD COLUMN valid_until TEXT")
        except Exception:
            pass
        # valid_until 索引（用于时态过滤查询）
        try:
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_valid_until ON memories(valid_until)"
            )
        except Exception:
            pass
        # 兼容旧库：加 source_file 列（ingest 替换 / find_by_source_file 用）
        try:
            self._conn.execute("ALTER TABLE memories ADD COLUMN source_file TEXT")
        except Exception:
            pass  # 列已存在
        try:
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_source_file ON memories(source_file)"
            )
        except Exception:
            pass
        # FTS5 全文索引表（jieba 分词 + bigram fallback）
        self._conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5("
                           "content, id UNINDEXED, layer UNINDEXED, project_id UNINDEXED,"
                           "tokenize='unicode61')")
        # 嵌入缓存表（provider+model+fingerprint+hash 联合键）
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS embedding_cache (
                provider    TEXT NOT NULL DEFAULT 'bge-small-zh',
                model       TEXT NOT NULL DEFAULT 'bge-small-zh-v1.5',
                fp          TEXT NOT NULL DEFAULT '',
                hash        TEXT NOT NULL,
                embedding   TEXT NOT NULL,
                dims        INTEGER NOT NULL,
                updated_at  REAL NOT NULL,
                UNIQUE(provider, model, fp, hash)
            )
        """)
        # 文件哈希追踪表（SHA256 去重：未变文件跳过重索引）
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS files (
                path        TEXT PRIMARY KEY,
                hash        TEXT NOT NULL,
                mtime       REAL NOT NULL,
                size        INTEGER NOT NULL
            )
        """)
        self._conn.commit()

    # ── CRUD: 记忆条目 ────────────────────────────────

    def insert(self, entry: MemoryEntry) -> str:
        """插入一条记忆，返回 id"""
        row = entry.to_dict()
        placeholders = ", ".join(f":{k}" for k in row)
        sql = f"INSERT OR REPLACE INTO memories ({', '.join(row)}) VALUES ({placeholders})"
        self._conn.execute(sql, row)
        self._conn.commit()
        return entry.id

    def get(self, memory_id: str) -> Optional[MemoryEntry]:
        """按 id 获取记忆"""
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        return MemoryEntry.from_row(dict(row)) if row else None

    def update(self, memory_id: str, **fields) -> bool:
        """更新记忆字段"""
        if not fields:
            return False
        fields["updated_at"] = _now_iso()
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        fields["id"] = memory_id
        cur = self._conn.execute(
            f"UPDATE memories SET {sets} WHERE id = :id", fields
        )
        self._conn.commit()
        return cur.rowcount > 0

    def delete(self, memory_id: str) -> bool:
        """删除记忆（级联删除关联边和反馈日志）"""
        cur = self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def touch(self, memory_id: str) -> bool:
        """更新访问时间和计数"""
        return self.update(
            memory_id,
            last_accessed_at=_now_iso(),
            access_count=1,  # SQLite 不支持 +=，用脚本层处理
        )

    def list_all(self, status: str = "active") -> list[MemoryEntry]:
        """列出所有活跃记忆"""
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE status = ? ORDER BY weight DESC",
            (status,),
        ).fetchall()
        return [MemoryEntry.from_row(dict(r)) for r in rows]

    def list_by_layer(self, layer: str, status: str = "active") -> list[MemoryEntry]:
        """按层级列出"""
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE layer = ? AND status = ? ORDER BY weight DESC",
            (layer, status),
        ).fetchall()
        return [MemoryEntry.from_row(dict(r)) for r in rows]

    def list_by_project(
        self, project_id: str, status: str = "active"
    ) -> list[MemoryEntry]:
        """按项目列出"""
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE project_id = ? AND status = ? "
            "ORDER BY weight DESC",
            (project_id, status),
        ).fetchall()
        return [MemoryEntry.from_row(dict(r)) for r in rows]

    def list_updated_since(self, since_iso: str) -> list[MemoryEntry]:
        """查某时间后更新的记忆（增量同步用）"""
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE updated_at > ? ORDER BY updated_at",
            (since_iso,),
        ).fetchall()
        return [MemoryEntry.from_row(dict(r)) for r in rows]

    def search_by_tags(self, tags: list[str]) -> list[MemoryEntry]:
        """按标签搜索（OR 匹配）"""
        if not tags:
            return []
        like_clauses = " OR ".join("tags LIKE ?" for _ in tags)
        params = [f"%{t}%" for t in tags]
        rows = self._conn.execute(
            f"SELECT * FROM memories WHERE ({like_clauses}) AND status = 'active' "
            f"ORDER BY weight DESC",
            params,
        ).fetchall()
        return [MemoryEntry.from_row(dict(r)) for r in rows]

    def find_by_content(self, content: str) -> Optional[MemoryEntry]:
        """按内容精确查重——内容完全相同视为重复"""
        row = self._conn.execute(
            "SELECT * FROM memories WHERE content = ? AND status = 'active' LIMIT 1",
            (content,),
        ).fetchone()
        return MemoryEntry.from_row(dict(row)) if row else None

    def find_by_source_file(self, source_file: str) -> list[MemoryEntry]:
        """按 source_file 列查所有活跃条目（ingest 替换用）"""
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE source_file = ? AND status = 'active' "
            "ORDER BY weight DESC",
            (source_file,),
        ).fetchall()
        return [MemoryEntry.from_row(dict(r)) for r in rows]

    def search_content(self, keyword: str, limit: int = 20) -> list[MemoryEntry]:
        """关键词全文搜索（SQLite LIKE）"""
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE content LIKE ? AND status = 'active' "
            "ORDER BY weight DESC LIMIT ?",
            (f"%{keyword}%", limit),
        ).fetchall()
        return [MemoryEntry.from_row(dict(r)) for r in rows]

    # ── FTS5 全文搜索 ─────────────────────────────────

    def search_fts(self, query: str, limit: int = 20,
                   *, layer: Optional[str] = None,
                   project_id: Optional[str] = None) -> list[tuple[MemoryEntry, float]]:
        """FTS5 全文搜索（BM25 排序），返回 [(entry, score), ...]

        FTS5 表不可用时返回空列表（调用方应 fallback 到 TF-IDF）。
        """
        try:
            fts_query = _build_fts_query(query)
            if not fts_query:
                return []

            where_clauses = ["memories_fts MATCH ?"]
            params: list = [fts_query]

            if layer:
                where_clauses.append("m.layer = ?")
                params.append(layer)
            if project_id:
                where_clauses.append("m.project_id = ?")
                params.append(project_id)

            sql = (
                "SELECT m.id, m.content, m.layer, m.category, m.scope, m.project_id, "
                "m.tags, m.created_at, m.updated_at, m.last_accessed_at, "
                "m.access_count, m.weight, m.source_session_id, m.confidence, "
                "m.status, m.metadata, m.name, m.valid_from, m.valid_until, "
                "m.source_file, bm25(memories_fts) as rank "
                "FROM memories_fts "
                "JOIN memories m ON m.id = memories_fts.id "
                "WHERE " + " AND ".join(where_clauses) + " "
                "AND m.status = 'active' "
                "ORDER BY rank LIMIT ?"
            )
            params.append(limit)

            rows = self._conn.execute(sql, params).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                rank = d.pop("rank", 0)
                score = _bm25_to_score(rank)
                entry = MemoryEntry.from_row(d)
                results.append((entry, score))
            return results
        except Exception as e:
            logger.warning("FTS5 search failed, falling back: %s", e)
            return []

    # ── 文件哈希追踪 ──────────────────────────────────

    def get_file_hash(self, path: str) -> Optional[str]:
        """获取已记录的文件 SHA256 哈希"""
        row = self._conn.execute(
            "SELECT hash FROM files WHERE path = ?", (path,)
        ).fetchone()
        return row[0] if row else None

    def upsert_file_hash(self, path: str, file_hash: str,
                         mtime: float, size: int) -> None:
        """记录/更新文件哈希"""
        self._conn.execute(
            "INSERT OR REPLACE INTO files (path, hash, mtime, size) "
            "VALUES (?, ?, ?, ?)",
            (path, file_hash, mtime, size),
        )
        self._conn.commit()

    def file_hash_unchanged(self, path: str, file_hash: str) -> bool:
        """检查文件哈希是否未变（用于跳过重索引）"""
        existing = self.get_file_hash(path)
        return existing is not None and existing == file_hash

    def count(self, layer: Optional[str] = None, status: str = "active") -> int:
        """计数"""
        if layer:
            return self._conn.execute(
                "SELECT COUNT(*) FROM memories WHERE layer = ? AND status = ?",
                (layer, status),
            ).fetchone()[0]
        return self._conn.execute(
            "SELECT COUNT(*) FROM memories WHERE status = ?", (status,)
        ).fetchone()[0]

    # ── 关联边 ────────────────────────────────────────

    def add_edge(self, edge: Edge) -> str:
        """添加关联边"""
        self._conn.execute(
            "INSERT OR REPLACE INTO edges (id, source_id, target_id, "
            "relation_type, weight, created_at) VALUES (?,?,?,?,?,?)",
            (edge.id, edge.source_id, edge.target_id,
             edge.relation_type, edge.weight, edge.created_at),
        )
        self._conn.commit()
        return edge.id

    def get_neighbors(
        self, memory_id: str, relation_type: Optional[str] = None
    ) -> list[tuple[MemoryEntry, Edge]]:
        """获取某记忆的所有邻居"""
        if relation_type:
            rows = self._conn.execute(
                """SELECT m.*, e.relation_type, e.weight as edge_weight, e.id as edge_id
                   FROM edges e JOIN memories m ON e.target_id = m.id
                   WHERE e.source_id = ? AND e.relation_type = ?""",
                (memory_id, relation_type),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT m.*, e.relation_type, e.weight as edge_weight, e.id as edge_id
                   FROM edges e JOIN memories m ON e.target_id = m.id
                   WHERE e.source_id = ?""",
                (memory_id,),
            ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            entry = MemoryEntry.from_row(d)
            edge = Edge(
                id=d["edge_id"],
                source_id=memory_id,
                target_id=entry.id,
                relation_type=d["relation_type"],
                weight=d["edge_weight"],
            )
            results.append((entry, edge))
        return results

    def find_contradictions(self, memory_id: str) -> list[MemoryEntry]:
        """查与某记忆冲突的其他记忆"""
        pairs = self.get_neighbors(memory_id, "contradicts")
        return [entry for entry, _ in pairs]

    # ── 反馈日志 ──────────────────────────────────────

    def log_feedback(self, feedback: FeedbackLog) -> str:
        """记录一条反馈"""
        self._conn.execute(
            "INSERT INTO feedback_log (id, memory_id, session_id, action, "
            "context_query, timestamp) VALUES (?,?,?,?,?,?)",
            (feedback.id, feedback.memory_id, feedback.session_id,
             feedback.action, feedback.context_query, feedback.timestamp),
        )
        self._conn.commit()
        return feedback.id

    def get_feedback_stats(
        self, memory_id: str, days: int = 30
    ) -> dict[str, int]:
        """统计某记忆近 N 天的反馈"""
        since = _days_ago_iso(days)
        rows = self._conn.execute(
            """SELECT action, COUNT(*) as cnt
               FROM feedback_log
               WHERE memory_id = ? AND timestamp > ?
               GROUP BY action""",
            (memory_id, since),
        ).fetchall()
        return {r["action"]: r["cnt"] for r in rows}

    def close(self):
        self._conn.close()


# ── 距离转换工具 ──────────────────────────────────────

def _l2_to_cosine(l2_distance: float) -> float:
    """L2 距离 → 余弦相似度（假设向量已归一化）

    For normalized vectors: L2² = 2 * (1 - cos_sim)
    → cos_sim = 1 - L2² / 2
    """
    return 1.0 - (l2_distance ** 2) / 2.0


# ── LanceDB 管理器 ────────────────────────────────────

class LanceDBManager:
    """LanceDB 向量存储 — 语义搜索"""

    TABLE_NAME = "memory_vectors"

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = str(db_path or DEFAULT_LANCEDB_PATH)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(self.db_path)
        self._table: Optional[LanceTable] = None
        self._ensure_table()

    def _ensure_table(self):
        """确保向量表存在"""
        if self.TABLE_NAME in self._db.table_names():
            self._table = self._db.open_table(self.TABLE_NAME)
        else:
            # 创建空表，等第一次插入时建 schema
            self._table = None

    def _create_table(self, sample_vectors: list[dict]):
        """用首批数据创建表"""
        import pyarrow as pa
        self._table = self._db.create_table(self.TABLE_NAME, data=sample_vectors)

    def upsert(self, entries: list[MemoryEntry], vectors: list[list[float]]):
        """批量插入/更新向量（先删旧再插，避免冗余）"""
        if not entries:
            return

        # 先删除同 ID 的旧向量（防止冗余累积）
        ids = [e.id for e in entries]
        self._delete_by_ids(ids)

        rows = [
            {
                "id": e.id,
                "vector": v,
                "layer": e.layer,
                "category": e.category,
                "scope": e.scope,
                "project_id": e.project_id or "",
            }
            for e, v in zip(entries, vectors)
        ]

        if self._table is None:
            self._create_table(rows)
        else:
            self._table.add(rows)

    def _delete_by_ids(self, memory_ids: list[str]):
        """按 id 批量删除向量（内部用，不抛异常）"""
        if not self._table or not memory_ids:
            return
        try:
            ids_str = ",".join(repr(i) for i in memory_ids)
            self._table.delete(f"id IN ({ids_str})")
        except Exception:
            pass  # 表为空或 ID 不存在时忽略

    def compact(self):
        """清理冗余向量：删掉 DB 中已不存在的 id 对应的向量，减少陈旧数据"""
        if not self._table:
            return 0
        try:
            # LanceDB 的 optimize 会合并小文件
            self._table.optimize()
        except Exception:
            pass
        return self.count()

    def compact_orphans(self, valid_ids: set[str]) -> int:
        """删除 LanceDB 中在 SQLite 里已不存在的僵尸向量

        Args:
            valid_ids: SQLite 中所有活跃记忆的 ID 集合

        Returns:
            删除的向量数
        """
        if not self._table:
            return 0
        try:
            all_rows = self._table.to_pandas()
            orphan_ids = [rid for rid in all_rows["id"].tolist() if rid not in valid_ids]
            if orphan_ids:
                self.delete(orphan_ids)
            return len(orphan_ids)
        except Exception as e:
            logger.warning("compact_orphans failed: %s", e)
            return 0

    def delete(self, memory_ids: list[str]):
        """按 id 删除向量"""
        if not self._table:
            return
        self._table.delete(f"id IN ({','.join(repr(i) for i in memory_ids)})")

    def search(
        self,
        query_vector: list[float],
        top_k: int = 20,
        *,
        layer: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> list[dict]:
        """语义向量搜索，返回 top-k 相似结果"""
        if not self._table:
            return []

        query = self._table.search(query_vector).limit(top_k)

        # 构建过滤条件
        filters = []
        if layer:
            filters.append(f"layer = '{layer}'")
        if project_id:
            filters.append(f"project_id = '{project_id}'")
        if filters:
            query = query.where(" AND ".join(filters))

        results = query.to_list()
        for r in results:
            l2 = r.pop("_distance", 2.0)  # L2 distance from LanceDB
            r["_distance"] = _l2_to_cosine(l2)  # convert to cosine similarity
        return results

    def search_with_filter(
        self,
        query_vector: list[float],
        top_k: int = 20,
        *,
        filter_expr: Optional[str] = None,
    ) -> list[dict]:
        """带自定义过滤表达式的向量搜索"""
        if not self._table:
            return []

        query = self._table.search(query_vector).limit(top_k)
        if filter_expr:
            query = query.where(filter_expr)

        results = query.to_list()
        for r in results:
            l2 = r.pop("_distance", 2.0)
            r["_distance"] = _l2_to_cosine(l2)  # convert to cosine similarity
        return results

    def count(self) -> int:
        """向量表行数"""
        if not self._table:
            return 0
        return self._table.count_rows()


# ── 统一存储接口 ──────────────────────────────────────

class MemoryStore:
    """记忆存储统一入口 — SQLite + LanceDB 联合操作"""

    def __init__(self, config: Optional[MemoryCoreConfig] = None):
        self.config = config or MemoryCoreConfig()
        self.sqlite = SQLiteManager(self.config.sqlite_path)
        # 向量层为可选增强路径：lancedb/numpy 不可用时置 None，走纯 SQLite 核心
        self.lancedb = (
            LanceDBManager(self.config.lancedb_path) if _VECTOR_AVAILABLE else None
        )
        self._embedder = None  # lazy init
        logger.info(
            "MemoryStore ready: sqlite=%s, lancedb=%s (vectors=%s)",
            self.config.sqlite_path,
            self.config.lancedb_path if self.lancedb is not None else None,
            _VECTOR_AVAILABLE,
        )

    @property
    def embedder(self):
        """延迟加载嵌入模型（向量层不可用时返回 None，不抛异常）"""
        if not _VECTOR_AVAILABLE:
            logger.warning("向量层不可用（lancedb/numpy/sentence-transformers 未安装），embedding 功能关闭")
            return None
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading embedding model: %s", self.config.embedding_model)
            self._embedder = SentenceTransformer(self.config.embedding_model)
        return self._embedder

    def embed(self, texts: list[str]) -> list[list[float]]:
        """文本 → 向量（文档侧，不加前缀）。向量层不可用时返回空列表。"""
        if not _VECTOR_AVAILABLE or self.embedder is None:
            return []
        embeddings = self.embedder.encode(
            texts,
            batch_size=32,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        return embeddings.tolist()

    def embed_query(self, query: str) -> list[float]:
        """查询文本 → 向量（自动加 BGE 前缀以提升检索精度，缓存命中则跳过嵌入）

        BGE 模型在 query 侧加前缀能显著提升语义匹配效果。
        参考: https://huggingface.co/BAAI/bge-small-zh-v1.5
        """
        if not _VECTOR_AVAILABLE or self.embedder is None:
            return []
        query_hash = hashlib.sha256(
            f"query\0{query}".encode("utf-8", errors="replace")
        ).hexdigest()
        # 查缓存
        cached = self._lookup_embedding_cache([query_hash])
        if query_hash in cached:
            return cached[query_hash]

        prefixed = BGE_QUERY_PREFIX + query
        embeddings = self.embedder.encode(
            [prefixed],
            batch_size=1,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        vec = embeddings[0].tolist()
        # 写缓存
        self._store_embedding_cache([(query_hash, vec)])
        return vec

    def add(
        self,
        content: str,
        layer: str = "L4",
        category: str = "knowledge",
        scope: str = "global",
        project_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
        source_session_id: Optional[str] = None,
        confidence: float = 1.0,
        metadata: Optional[dict] = None,
        source_file: Optional[str] = None,
        *,
        dedup: bool = True,
        valid_from: Optional[str] = None,
        valid_until: Optional[str] = None,
    ) -> str:
        """添加一条记忆（同时写 SQLite 和 LanceDB）

        Args:
            dedup: True 时检查内容是否重复，重复则返回已有 ID
            source_file: 来源文件路径（importer 用）。同 source_file 再次灌入时
                替换而非追加（归档旧活跃条目）。也会同步进 metadata 以兼容导出逻辑。
        """
        # 去重：内容完全相同视为重复
        if dedup:
            existing = self.sqlite.find_by_content(content)
            if existing:
                self.sqlite.update(existing.id, updated_at=_now_iso())
                return existing.id

        # source_file 透传：写入 MemoryEntry.source_file，并同步进 metadata（向后兼容）
        if source_file is not None:
            if metadata is None:
                metadata = {}
            metadata.setdefault("source_file", source_file)

        # 从内容提取 name（优先用 metadata.source_file，其次用 frontmatter name）
        entry_name = ""
        fm_name, _ = _parse_frontmatter(content)
        if fm_name:
            entry_name = fm_name
        elif metadata and metadata.get("source_file"):
            entry_name = Path(metadata["source_file"]).stem

        # 推断 project_id（未显式指定时从内容关键词推断）
        if not project_id:
            from .classify import infer_project_id
            project_id = infer_project_id(content)

        # 同 source_file 再次灌入 → 替换而非追加（归档旧活跃条目，避免重复累积）
        if source_file is not None:
            for old in self.sqlite.find_by_source_file(source_file):
                self.sqlite.update(old.id, status="archived", updated_at=_now_iso())
                self._remove_fts(old.id)

        entry = MemoryEntry(
            name=entry_name,
            content=content,
            layer=layer,
            category=category,
            scope=scope,
            project_id=project_id,
            tags=tags,
            source_session_id=source_session_id,
            confidence=confidence,
            metadata=metadata,
            source_file=source_file,
            valid_from=valid_from,
            valid_until=valid_until,
        )
        # SQLite
        self.sqlite.insert(entry)
        # FTS5 索引
        self._index_fts(entry)
        # 实体索引（OpenHuman 跨源聚合轻量版）
        try:
            from .entity_index import index_memory
            index_memory(self.sqlite._conn, entry.id, content)
        except Exception:
            pass  # 实体索引失败不阻塞写入
        # LanceDB（可选增强路径，无向量依赖时跳过）
        if self.lancedb is not None:
            vectors = self.embed([content])
            self.lancedb.upsert([entry], vectors)
        return entry.id

    def add_batch(self, items: list[dict]) -> list[str]:
        """批量添加记忆"""
        entries = []
        contents = []
        for item in items:
            entry = MemoryEntry(
                content=item["content"],
                layer=item.get("layer", "L4"),
                category=item.get("category", "knowledge"),
                scope=item.get("scope", "global"),
                project_id=item.get("project_id"),
                tags=item.get("tags"),
                source_session_id=item.get("source_session_id"),
                confidence=item.get("confidence", 1.0),
                metadata=item.get("metadata"),
                source_file=item.get("source_file"),
                created_at=item.get("created_at"),
                updated_at=item.get("updated_at"),
                valid_from=item.get("valid_from"),
                valid_until=item.get("valid_until"),
            )
            entries.append(entry)
            contents.append(entry.content)

        # 批量写入 SQLite
        for entry in entries:
            self.sqlite.insert(entry)

        # 实体索引（OpenHuman 跨源聚合轻量版）
        try:
            from .entity_index import index_memory
            for entry in entries:
                index_memory(self.sqlite._conn, entry.id, entry.content)
        except Exception:
            pass  # 实体索引失败不阻塞写入

        # 批量嵌入 + 写入 LanceDB（可选增强路径，无向量依赖时跳过）
        if self.lancedb is not None:
            vectors = self.embed(contents)
            self.lancedb.upsert(entries, vectors)

        return [e.id for e in entries]

    def get(self, memory_id: str) -> Optional[MemoryEntry]:
        """获取单条记忆"""
        return self.sqlite.get(memory_id)

    def find_by_source_file(self, source_file: str) -> list[MemoryEntry]:
        """按来源文件查所有活跃条目（ingest 替换用）"""
        return self.sqlite.find_by_source_file(source_file)

    def delete(self, memory_id: str) -> bool:
        """删除记忆"""
        ok = self.sqlite.delete(memory_id)
        if ok:
            if self.lancedb is not None:
                self.lancedb.delete([memory_id])
            self._remove_fts(memory_id)
        return ok

    def update(self, memory_id: str, **fields) -> bool:
        """更新记忆字段。如果改了 content，需要重新嵌入。"""
        if "content" in fields:
            new_content = fields.pop("content")
            old = self.sqlite.get(memory_id)
            if old and old.content != new_content:
                # 内容变化 → 重新嵌入向量（可选增强路径，无向量依赖时跳过）
                if self.lancedb is not None:
                    self.lancedb.delete([memory_id])
                    vectors = self.embed([new_content])
                    entry = MemoryEntry(
                        id=memory_id,
                        content=new_content,
                        layer=old.layer,
                        category=old.category,
                        scope=old.scope,
                        project_id=old.project_id,
                        tags=old.tags,
                        confidence=old.confidence,
                    )
                    self.lancedb.upsert([entry], vectors)
        return self.sqlite.update(memory_id, **fields)

    def touch(self, memory_id: str):
        """记录访问"""
        self.sqlite.touch(memory_id)
        # 更新 access_count（SQLite 不能 +=）
        entry = self.sqlite.get(memory_id)
        if entry:
            self.sqlite.update(
                memory_id,
                access_count=entry.access_count + 1,
            )

    def search_vector(
        self,
        query: str,
        top_k: int = 20,
        *,
        layer: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> list[tuple[MemoryEntry, float]]:
        """语义搜索，返回 (MemoryEntry, similarity) 列表。

        向量层不可用（self.lancedb is None）时返回空列表（调用方走关键词检索）。
        """
        if self.lancedb is None:
            return []
        vec = self.embed_query(query)
        results = self.lancedb.search(
            vec, top_k=top_k, layer=layer, project_id=project_id
        )
        entries = []
        for r in results:
            entry = self.sqlite.get(r["id"])
            if entry and entry.status == "active":
                # _distance is now cosine similarity (converted in LanceDBManager)
                entries.append((entry, r.get("_distance", 0)))
        return entries

    def export_markdown(
        self,
        memory_id: Optional[str] = None,
        target_dir: Optional[Path] = None,
        *,
        since: Optional[str] = None,
        layer: Optional[str] = None,
        dry_run: bool = False,
    ) -> list[Path]:
        """导出记忆为 markdown 文件

        Args:
            memory_id: 导出单条，None 则批量
            target_dir: 目标目录，None 则不写文件（返回内容）
            since: 增量导出——只导出 updated_at > since 的条目
            layer: 按层级过滤
            dry_run: 只返回会写入的路径列表，不实际写入

        Returns:
            已写入的文件 Path 列表
        """
        # 确定导出条目
        if memory_id:
            entry = self.sqlite.get(memory_id)
            entries = [entry] if entry else []
        elif since:
            entries = self.sqlite.list_updated_since(since)
        else:
            entries = self.sqlite.list_all()

        # 层级过滤
        if layer:
            entries = [e for e in entries if e.layer == layer]

        if not entries:
            return []

        # 按层分组导出
        written = []
        for entry in entries:
            md_content = self._entry_to_markdown(entry)
            if target_dir and not dry_run:
                # 确定子目录
                subdir = self._layer_to_subdir(entry.layer, entry.category)
                out_dir = Path(target_dir) / subdir
                out_dir.mkdir(parents=True, exist_ok=True)

                # 文件名：用 metadata.source_file 或生成
                source = entry.metadata.get("source_file", "")
                if source:
                    filename = Path(source).name
                else:
                    # 从标题/内容生成文件名（保留中文字符，不再用 isalnum 剥中文）
                    title = entry.content.split("\n")[0].strip("# ")[:60]
                    safe_title = "".join(
                        c if c.isalnum() or '一' <= c <= '鿿' or c in " _-"
                        else ""
                        for c in title
                    ).strip().replace(" ", "-")[:60]
                    if not safe_title:
                        safe_title = entry.id
                    filename = f"{entry.id}_{safe_title}.md"

                filepath = out_dir / filename
                if not dry_run:
                    filepath.write_text(md_content, encoding="utf-8")
                written.append(filepath)
            elif not target_dir:
                # 没有目标目录，跳过文件写入
                pass
            else:
                written.append(None)  # dry_run

        return written

    def _entry_to_markdown(self, entry: MemoryEntry) -> str:
        """将单条记忆转为 markdown 格式

        对齐五层金字塔 frontmatter 规范：
        - name: kebab-case slug（从内容生成）
        - description: 一行摘要
        - metadata.type: user|feedback|project|reference
        """
        # LCM 层内容自带 frontmatter，直接返回原文
        if entry.layer == "LCM":
            return entry.content

        # 剥离所有前导 frontmatter（防止嵌套）
        _, body = _parse_frontmatter(entry.content)
        if not body:
            body = entry.content

        # 从内容生成 name slug（用 body 而不是原始 content）
        first_line = body.split("\n")[0].strip().lstrip("#").strip()
        # 保留：ASCII字母数字、中文字符、空格、下划线、连字符
        slug = "".join(
            c if c.isalnum() or '一' <= c <= '鿿' or c in " _-" else ""
            for c in first_line[:60]
        ).strip().replace(" ", "-").lower()[:80]
        if not slug:
            slug = entry.id

        # 生成描述（取内容前 120 字符，去换行）
        desc = body.split("\n")[0].strip().lstrip("#").strip()[:120]

        # layer → type 映射
        type_map = {
            "L0": "reference",
            "L1": "user",
            "L2": "user",
            "L3": "user",
            "L4": "project",
            "L5": "feedback",
            "LCM": "reference",
        }
        mem_type = type_map.get(entry.layer, "reference")
        if entry.layer == "L4" and entry.category in ("principle", "identity", "preference"):
            mem_type = "reference"

        lines = ["---"]
        lines.append(f"name: {slug}")
        lines.append(f"description: {desc}")
        lines.append("metadata:")
        lines.append(f"  type: {mem_type}")
        lines.append(f"  layer: {entry.layer}")
        lines.append(f"  category: {entry.category}")
        lines.append(f"  scope: {entry.scope}")
        if entry.project_id:
            lines.append(f"  project_id: {entry.project_id}")
        if entry.tags:
            tags_str = ", ".join(entry.tags)
            lines.append(f"  tags: [{tags_str}]")
        if entry.source_session_id:
            lines.append(f"  source_session_id: {entry.source_session_id}")
        if entry.confidence < 1.0:
            lines.append(f"  confidence: {entry.confidence}")
        lines.append(f"  created_at: {entry.created_at}")
        lines.append(f"  updated_at: {entry.updated_at}")
        if entry.valid_from:
            lines.append(f"  valid_from: {entry.valid_from}")
        if entry.valid_until:
            lines.append(f"  valid_until: {entry.valid_until}")
        # 附加自定义元数据（排除内部字段）
        for k, v in entry.metadata.items():
            if k not in ("source_file",):
                lines.append(f"  {k}: {v}")
        lines.append("---")
        lines.append("")
        lines.append(body)

        # L3/L4 条目追加 Why/How to apply 占位
        if entry.layer in ("L3", "L4") and "**Why:**" not in body:
            lines.append("")
            lines.append("**Why:** _待补充_")
            lines.append("**How to apply:** _待补充_")

        return "\n".join(lines)

    # L4 category → subdirectory mapping (no blind pluralization)
    _L4_SUBDIR_MAP = {
        "knowledge":       "knowledge",
        "principle":       "knowledge",   # L4 principles fall to knowledge
        "project":         "projects",
        "projects":        "projects",
        "infrastructure":  "infrastructure",
        "pitfall":         "pitfalls",
        "pitfalls":        "pitfalls",
        "research":        "research",
        "archive":         "archive",
    }

    def _layer_to_subdir(self, layer: str, category: str) -> str:
        """层级+分类 → 子目录路径（兼容旧结构）

        L4 子目录通过 category 精确映射，不再盲加 's'。
        未知 category 回退到 L4_knowledge/ 根目录。
        """
        if layer == "L4" and category:
            sub = self._L4_SUBDIR_MAP.get(category, "knowledge")
            return f"L4_knowledge/{sub}"
        mapping = {
            "L0": "boss",
            "L1": "L1_principles",
            "L2": "L2_identity",
            "L3": "L3_preferences",
            "L5": "L5_workspace",
            "LCM": "LCM",
        }
        return mapping.get(layer, "L4_knowledge")

    def export_session_note(
        self,
        session_id: str,
        content: str,
        target_dir: Path,
    ) -> Path:
        """导出一篇会话笔记为 markdown（便捷方法）

        直接写文件，不走 MemoryEntry（适合长篇内容）。
        """
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        filename = f"session-note-{today}.md"
        filepath = Path(target_dir) / "L5_workspace" / "sessions" / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)

        # 追加模式
        existing = ""
        if filepath.exists():
            existing = filepath.read_text(encoding="utf-8") + "\n\n---\n\n"

        filepath.write_text(existing + content, encoding="utf-8")
        return filepath

    def sync_c_to_d(self, md_dir: Path) -> dict:
        """C盘 → D盘同步：扫描 markdown 文件，更新 DB 中对应条目

        匹配规则（按优先级）：
        1. metadata.source_file 匹配文件相对路径
        2. frontmatter name 字段匹配 DB entry 的 name slug

        只有 C盘文件比 DB 新时才更新。
        无匹配 DB 条目的文件会新建。
        入库前剥离 frontmatter，DB 只存正文。

        Returns:
            {"scanned": N, "new": N, "updated": N, "skipped": N}
        """
        import re
        from datetime import datetime as dt, timezone as tz, timedelta as td

        stats = {"scanned": 0, "new": 0, "updated": 0, "skipped": 0}
        md_dir = Path(md_dir)

        # 建立 DB 条目索引：source_file → entry, name_slug → entry
        all_entries = self.sqlite.list_all()
        by_source = {}   # source_file → entry
        by_name = {}     # name slug → entry
        for e in all_entries:
            sf = e.metadata.get("source_file", "")
            if sf:
                by_source[sf] = e
            # 从 body 内容提取 name slug
            body_content = _strip_frontmatter(e.content)
            first_line = body_content.split("\n")[0].strip().lstrip("#").strip() if body_content else ""
            slug = "".join(
                c if c.isalnum() or '一' <= c <= '鿿' or c in " _-" else ""
                for c in first_line[:60]
            ).strip().replace(" ", "-").lower()[:80]
            if slug:
                by_name[slug] = e

        for md_file in sorted(md_dir.rglob("*.md")):
            # 跳过非 memory 文件
            path_str = str(md_file)
            if "transcripts" in path_str or "CLOSE.md" in path_str:
                continue
            if md_file.name == "MEMORY.md":
                continue

            stats["scanned"] += 1

            try:
                raw_content = md_file.read_text(encoding="utf-8")
            except Exception:
                continue

            # 剥离 frontmatter，DB 只存正文（LCM 层保留原文）
            fm_name, body = _parse_frontmatter(raw_content)
            # 解析时态字段
            fm_valid_from = _extract_frontmatter_field(raw_content, "valid_from")
            fm_valid_until = _extract_frontmatter_field(raw_content, "valid_until")
            rel_path_pre = str(md_file.relative_to(md_dir)).replace("\\", "/")
            is_lcm = "LCM" in rel_path_pre.split("/")[:1]
            content_for_db = raw_content if is_lcm else (body if body else raw_content)

            file_mtime = dt.fromtimestamp(
                md_file.stat().st_mtime, tz=tz.utc
            ).isoformat()

            # 匹配 DB 条目
            rel_path = str(md_file.relative_to(md_dir)).replace("\\", "/")
            entry = by_source.get(rel_path) or by_source.get(md_file.name)
            if not entry and fm_name:
                entry = by_name.get(fm_name)

            if entry:
                # SHA256 文件哈希去重：内容未变 → 跳过（只更新 mtime）
                file_hash = _sha256_file(raw_content)
                if self.sqlite.file_hash_unchanged(rel_path, file_hash):
                    self.sqlite.upsert_file_hash(
                        rel_path, file_hash,
                        md_file.stat().st_mtime, md_file.stat().st_size
                    )
                    stats["skipped"] += 1
                    continue

                # 比较时间：C盘更新 → 更新 DB
                if file_mtime > entry.updated_at:
                    self.sqlite.update(
                        entry.id,
                        name=fm_name or entry.name,
                        content=content_for_db,
                        updated_at=file_mtime,
                        valid_from=fm_valid_from,
                        valid_until=fm_valid_until,
                    )
                    # 更新向量（如果内容变了；可选增强路径，无向量依赖时跳过）
                    if entry.content != content_for_db and self.lancedb is not None:
                        self.lancedb.delete([entry.id])
                        vectors = self.embed([content_for_db])
                        from .store import MemoryEntry as _ME
                        new_entry = _ME(
                            id=entry.id, content=content_for_db,
                            layer=entry.layer, category=entry.category,
                            scope=entry.scope,
                        )
                        self.lancedb.upsert([new_entry], vectors)
                    stats["updated"] += 1
                    self.sqlite.upsert_file_hash(
                        rel_path, file_hash,
                        md_file.stat().st_mtime, md_file.stat().st_size
                    )
                else:
                    stats["skipped"] += 1
            else:
                # 新建条目——从路径推断 layer + category
                layer = self._infer_layer_from_path(rel_path)
                category = self._infer_category_from_path(rel_path, layer)
                # 用 add 的去重逻辑，避免重复内容
                mid = self.add(
                    content=content_for_db,
                    layer=layer,
                    category=category,
                    scope="global",
                    metadata={"source_file": md_file.name},
                    dedup=True,
                    valid_from=fm_valid_from,
                    valid_until=fm_valid_until,
                )
                stats["new"] += 1
                file_hash = _sha256_file(raw_content)
                self.sqlite.upsert_file_hash(
                    rel_path, file_hash,
                    md_file.stat().st_mtime, md_file.stat().st_size
                )

        return stats

    def _infer_layer_from_path(self, rel_path: str) -> str:
        """从相对路径推断记忆层级"""
        if rel_path.startswith("L1"):
            return "L1"
        elif rel_path.startswith("L2"):
            return "L2"
        elif rel_path.startswith("L3"):
            return "L3"
        elif rel_path.startswith("L4"):
            return "L4"
        elif rel_path.startswith("L5"):
            return "L5"
        elif "LCM" in rel_path:
            return "LCM"
        elif "boss" in rel_path.lower():
            return "L2"  # boss 档案归入身份层
        return "L4"

    def _infer_category_from_path(self, rel_path: str, layer: str) -> str:
        """从相对路径推断记忆类别（细化 L4 子目录）"""
        if layer != "L4":
            mapping = {
                "L1": "principle",
                "L2": "identity",
                "L3": "preference",
                "L5": "relationship",
                "LCM": "knowledge",
            }
            return mapping.get(layer, "knowledge")
        # L4: 从子目录名推断类别
        rel_path = rel_path.replace("\\", "/")
        parts = rel_path.split("/")
        for part in parts:
            if part in self._L4_SUBDIR_MAP:
                return self._L4_SUBDIR_MAP[part]
        # 反查：子目录 → 类别
        subdir_to_cat = {v: k for k, v in self._L4_SUBDIR_MAP.items()}
        for part in parts:
            if part in subdir_to_cat:
                return subdir_to_cat[part]
        return "knowledge"

    def _lookup_embedding_cache(self, hashes: list[str]) -> dict[str, list[float]]:
        """查询嵌入缓存，返回 {hash: embedding}（未命中则不存在于结果中）

        完全 best-effort：任何错误都返回 {}，不阻塞搜索。
        """
        if not hashes:
            return {}
        try:
            placeholders = ",".join("?" * len(hashes))
            rows = self.sqlite._conn.execute(
                f"SELECT hash, embedding FROM embedding_cache "
                f"WHERE hash IN ({placeholders})",
                hashes,
            ).fetchall()
        except Exception:
            return {}

        result = {}
        for row in rows:
            try:
                vec = json.loads(row[1])
                if isinstance(vec, list):
                    result[row[0]] = vec
            except (TypeError, ValueError):
                pass
        return result

    def _store_embedding_cache(self, items: list[tuple[str, list[float]]]) -> None:
        """写入嵌入缓存（INSERT OR IGNORE 幂等）

        Best-effort：写入失败不阻塞，只记日志。
        """
        if not items:
            return
        import time
        now = time.time()
        try:
            for h, vec in items:
                self.sqlite._conn.execute(
                    "INSERT OR IGNORE INTO embedding_cache "
                    "(provider, model, fp, hash, embedding, dims, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("bge-small-zh", "bge-small-zh-v1.5", "",
                     h, json.dumps(vec), len(vec), now),
                )
            self.sqlite._conn.commit()
        except Exception as e:
            try:
                self.sqlite._conn.rollback()
            except Exception:
                pass
            logger.debug("embedding_cache_write_skipped: %s", e)

    def _index_fts(self, entry: MemoryEntry) -> None:
        """将一条记忆的正文分词后写入 FTS5 索引（best-effort）"""
        try:
            segmented = _segment_for_fts(entry.content)
            self.sqlite._conn.execute(
                "INSERT OR REPLACE INTO memories_fts (content, id, layer, project_id) "
                "VALUES (?, ?, ?, ?)",
                (segmented, entry.id, entry.layer, entry.project_id or ""),
            )
            self.sqlite._conn.commit()
        except Exception as e:
            logger.debug("fts_index_skipped: %s", e)

    def _remove_fts(self, memory_id: str) -> None:
        """从 FTS5 索引中删除一条记忆"""
        try:
            self.sqlite._conn.execute(
                "DELETE FROM memories_fts WHERE id = ?", (memory_id,)
            )
            self.sqlite._conn.commit()
        except Exception as e:
            logger.debug("fts_remove_skipped: %s", e)

    def rebuild_fts(self) -> int:
        """全量重建 FTS5 索引（从 memories 表全部重写）"""
        try:
            self.sqlite._conn.execute("DELETE FROM memories_fts")
            entries = self.sqlite.list_all(status="active")
            for e in entries:
                self._index_fts(e)
            logger.info("FTS5 index rebuilt: %d documents", len(entries))
            return len(entries)
        except Exception as e:
            logger.warning("FTS5 rebuild failed: %s", e)
            return 0

    def close(self):
        self.sqlite.close()

    def compact_vectors(self) -> dict:
        """清理 LanceDB 僵尸向量 + optimize

        Returns:
            {"before": N, "after": N, "removed": N}
        """
        if self.lancedb is None:
            return {"before": 0, "after": 0, "removed": 0}
        before = self.lancedb.count()
        active_ids = {e.id for e in self.sqlite.list_all()}
        removed = self.lancedb.compact_orphans(active_ids)
        self.lancedb.compact()  # optimize 合并小文件
        after = self.lancedb.count()
        logger.info("Vector compact: %d → %d (removed %d)", before, after, removed)
        return {"before": before, "after": after, "removed": removed}
