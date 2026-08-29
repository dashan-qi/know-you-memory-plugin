"""
memory_core — 统一记忆基础设施

三个产品共用同一套记忆内核：SkillMatch / 灵犀 / 萌宠(远期)

五维框架：
  分类 → 管理 → 存储 → 检索 → 使用判断
    ↑                                    ↓
    └────────── 反馈循环 ←───────────────┘

快速开始:
    from memory_core import MemoryCore

    mc = MemoryCore()
    mc.add("DuckDB ATTACH 模式日期类型不兼容，改直连", project="factor-agent", tags=["duckdb", "踩坑"])
    results = mc.recall("DuckDB 日期问题怎么修？")
    for entry, verdict in results:
        print(f"[{verdict.confidence_tier}] {entry.content}")

    mc.consolidate(session_id="abc", conversation="...")
    mc.feedback(memory_id="xyz", action="used")
"""

from pathlib import Path

from .config import MemoryCoreConfig, DEFAULT_DATA_DIR
from .store import MemoryStore, MemoryEntry, SQLiteManager, LanceDBManager
from .classify import classify_content, infer_project_id, run_maintenance, refresh_weights
from .retrieve import MemoryRetriever, route_query
from .judge import MemoryJudge, JudgmentResult, Verdict
from .consolidate import Consolidator, ConsolidationResult, HeuristicExtractor, quick_consolidate


class MemoryCore:
    """记忆内核 — 统一入口

    封装 MemoryStore + MemoryRetriever + MemoryJudge + Consolidator
    对外暴露最简单的 API。
    """

    def __init__(self, config: MemoryCoreConfig | None = None):
        self.config = config or MemoryCoreConfig()
        self.store = MemoryStore(self.config)
        self.retriever = MemoryRetriever(self.store)
        self.judge = MemoryJudge(self.store)
        self.consolidator = Consolidator(self.store)

    # ── 写入 ────────────────────────────────────────

    def add(
        self,
        content: str,
        *,
        layer: str | None = None,
        category: str | None = None,
        scope: str | None = None,
        project: str | None = None,
        tags: list[str] | None = None,
        session_id: str | None = None,
        confidence: float = 1.0,
        auto_classify: bool = True,
        valid_from: str | None = None,
        valid_until: str | None = None,
    ) -> str:
        """添加一条记忆

        Args:
            content: 记忆内容
            layer: L1-L5，None 时自动推断
            category: 类别，None 时自动推断
            scope: 作用域，None 时自动推断
            project: 项目 ID，None 时自动推断
            tags: 标签
            session_id: 来源会话
            confidence: 初始确认度 0-1
            auto_classify: 是否自动分类
            valid_from: 事实生效时间 (ISO 8601)，None=一直有效
            valid_until: 事实失效时间 (ISO 8601)，None=持续有效

        Returns:
            memory_id
        """
        if auto_classify and (layer is None or category is None or scope is None):
            classified = classify_content(content)
            layer = layer or classified["layer"]
            category = category or classified["category"]
            scope = scope or classified["scope"]

        if project is None:
            project = infer_project_id(content)

        memory_id = self.store.add(
            content=content,
            layer=layer or "L4",
            category=category or "knowledge",
            scope=scope or "project",
            project_id=project,
            tags=tags,
            source_session_id=session_id,
            confidence=confidence,
            valid_from=valid_from,
            valid_until=valid_until,
        )

        # 增量更新 TF-IDF 索引
        self.retriever.add_to_index(memory_id, content)

        return memory_id

    # ── 检索 ────────────────────────────────────────

    def recall(
        self,
        query: str,
        *,
        project: str | None = None,
        max_results: int = 5,
    ) -> list[tuple[MemoryEntry, Verdict]]:
        """检索相关记忆并判断

        Args:
            query: 用户提问
            project: 当前项目 ID（用于情境过滤）
            max_results: 最多返回几条

        Returns:
            [(MemoryEntry, Verdict), ...]
        """
        # 1. 路由判断
        route = route_query(query)
        if not route.is_memory and not route.is_hybrid:
            return []  # 纯知识问题，不查记忆

        # 2. 检索
        candidates = self.retriever.retrieve(
            query,
            project_id=project,
            max_injected=max_results * 2,  # 多拿一些给 judge 筛选
        )

        if not candidates:
            return []

        # 3. 判断
        judgment = self.judge.judge(
            query, candidates, current_project_id=project,
        )

        # 4. 记录访问
        for v in judgment.to_inject:
            self.store.touch(v.entry.id)

        return [(v.entry, v) for v in judgment.to_inject[:max_results]]

    # ── 笔记写入（DB-first）─────────────────────────────

    def remember_note(
        self,
        title: str,
        content: str,
        *,
        layer: str = "L5",
        category: str = "relationship",
        note_type: str = "session",
        project_id: str | None = None,
        tags: list[str] | None = None,
        export_md: bool = False,
        md_dir: str | Path | None = None,
    ) -> str:
        """写入一篇笔记——DB 为主，md 为可选导出

        这是 DB-first 架构的核心入口。所有笔记（会话记录、计划、
        踩坑、项目文档）都应通过此方法写入。

        Args:
            title: 笔记标题
            content: 完整正文（markdown）
            layer: 层级，默认 L5（关系记忆）
            category: 类别
            note_type: session / plan / pitfall / project
            project_id: 关联项目
            tags: 标签
            export_md: 是否同时导出 markdown 文件
            md_dir: 导出目录，默认 ~/memory_core_exports/

        Returns:
            memory_id
        """
        # 1. 写入 DB（主存储）
        memory_id = self.add(
            content=f"# {title}\n\n{content}",
            layer=layer,
            category=category,
            scope="global" if note_type != "project" else "project",
            project=project_id,
            tags=tags or [note_type],
        )

        # 2. 可选：导出 markdown 备份
        if export_md:
            target = Path(md_dir) if md_dir else DEFAULT_DATA_DIR

            if note_type == "session":
                self.store.export_session_note(
                    session_id=memory_id,
                    content=content,
                    target_dir=target,
                )
            else:
                self.store.export_markdown(
                    memory_id=memory_id,
                    target_dir=target,
                )

        return memory_id

    def export_all_notes(
        self,
        target_dir: str | Path,
        *,
        since: str | None = None,
        layer: str | None = None,
        dry_run: bool = False,
    ) -> list[Path]:
        """批量导出记忆到 markdown 文件

        Args:
            target_dir: 目标目录
            since: 增量导出（ISO 时间）
            layer: 按层级过滤
            dry_run: 预览模式

        Returns:
            已写入的文件路径列表
        """
        return self.store.export_markdown(
            target_dir=Path(target_dir),
            since=since,
            layer=layer,
            dry_run=dry_run,
        )

    # ── Consolidation ────────────────────────────────

    def consolidate(
        self,
        session_id: str,
        conversation: str,
        *,
        project: str | None = None,
    ) -> ConsolidationResult:
        """会话结束时的记忆固化"""
        return self.consolidator.consolidate(
            session_id, conversation, current_project=project,
        )

    # ── 自动记忆扫描 ──────────────────────────────────

    def auto_scan(
        self, project_path: str | Path, project_id: str
    ) -> dict:
        """扫描项目目录，自动创建/更新记忆

        对标 Windsurf Cascade 的自动记忆创建。
        检测项目结构、技术栈、入口点、端口、配置文件、Git 信息等。
        走 consolidation 管线去重，利用时态建模管理事实演变。

        Args:
            project_path: 项目根目录路径
            project_id: 项目标识（如 "factor-agent"）

        Returns:
            {"scanned": N, "new": N, "updated": N, "skipped": N}
        """
        # auto_memory.py 已在 Task 1 剔除（V1 不启用），本项目无需项目扫描
        raise NotImplementedError(
            "auto_scan 需要 auto_memory.ProjectScanner，KYM V1 已剔除该模块"
        )

    # ── 反馈 ────────────────────────────────────────

    def feedback(
        self,
        memory_id: str,
        action: str,
        *,
        session_id: str = "default",
        context_query: str | None = None,
    ):
        """记录记忆使用反馈"""
        self.judge.feedback(memory_id, action, session_id, context_query)

    # ── 维护 ────────────────────────────────────────

    def maintenance(self) -> dict:
        """执行定期维护：归档、过期、权重刷新"""
        from .classify import run_maintenance, refresh_weights

        stats = run_maintenance(self.store.sqlite)
        refreshed = refresh_weights(self.store.sqlite)
        stats["weights_refreshed"] = refreshed

        # 重建 TF-IDF 索引
        self.retriever.build_index()

        return stats

    def sync_c_to_d(self, md_dir: str | Path) -> dict:
        """C盘 → D盘同步：扫描 markdown 变更，写入 DB"""
        return self.store.sync_c_to_d(Path(md_dir))

    def cleanup_stale_vectors(self) -> dict:
        """清理 LanceDB 中 DB 已不存在的僵尸向量

        Returns:
            {"before": N, "after": N, "removed_estimate": N}
        """
        if self.store.lancedb is None:
            return {"before": 0, "after": 0, "removed_estimate": 0}
        before = self.store.lancedb.count()
        self.store.lancedb.compact()
        after = self.store.lancedb.count()
        return {"before": before, "after": after, "removed_estimate": max(0, before - after)}

    def stats(self) -> dict:
        """获取记忆库统计"""
        return {
            "total": self.store.sqlite.count(),
            "by_layer": {
                layer: self.store.sqlite.count(layer=layer)
                for layer in ["L1", "L2", "L3", "L4", "L5", "LCM"]
            },
            "vectors": self.store.lancedb.count() if self.store.lancedb is not None else 0,
        }

    def project_stats(self) -> dict:
        """获取按项目分组的统计

        Returns:
            {"project_id": {"total": N, "active": N, "expired": N, "auto": N}, ...}
        """
        result = {}
        # 按 project_id 分组统计
        rows = self.store.sqlite._conn.execute("""
            SELECT
                COALESCE(project_id, 'unknown') as pid,
                COUNT(*) as total,
                SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active,
                SUM(CASE WHEN valid_until IS NOT NULL
                    AND valid_until < datetime('now') THEN 1 ELSE 0 END) as expired,
                SUM(CASE WHEN tags LIKE '%auto-detected%' THEN 1 ELSE 0 END) as auto
            FROM memories
            GROUP BY pid
            ORDER BY total DESC
        """).fetchall()
        for pid, total, active, expired, auto in rows:
            result[pid] = {"total": total, "active": active or 0,
                          "expired": expired or 0, "auto": auto or 0}
        return result

    def close(self):
        """关闭连接"""
        self.store.close()


# ── 暴露 __version__ ─────────────────────────────────
__version__ = "0.2.0"
__all__ = [
    "MemoryCore",
    "MemoryCoreConfig",
    "MemoryStore",
    "MemoryEntry",
    "SQLiteManager",
    "LanceDBManager",
    "MemoryRetriever",
    "MemoryJudge",
    "JudgmentResult",
    "Verdict",
    "Consolidator",
    "ConsolidationResult",
    "HeuristicExtractor",
    "classify_content",
    "infer_project_id",
    "route_query",
    "quick_consolidate",
    "run_maintenance",
    "refresh_weights",
]
