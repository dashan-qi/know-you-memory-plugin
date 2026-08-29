"""
memory_core 分类逻辑

四层分类规则 + 自动降级/归档/过期。
MVP 用关键词启发式，不调 LLM。
"""

from __future__ import annotations

import re
import logging
from datetime import datetime, timezone

from .config import (
    LAYERS,
    CATEGORIES,
    SCOPES,
    AUTO_ARCHIVE_DAYS,
    AUTO_EXPIRE_DAYS,
    BASE_WEIGHTS,
    TEMPORAL_PENALTY_EXPIRED,
    TEMPORAL_PENALTY_RECENT,
    TEMPORAL_RECENT_DAYS,
)
from .store import MemoryEntry, SQLiteManager

logger = logging.getLogger("memory_core.classify")


# ── 层级分类规则（关键词 → 层级 + 类别） ─────────────

# 每条规则: (keywords, layer, category, scope)
CLASSIFICATION_RULES: list[tuple[list[str], str, str, str]] = [
    # L1 — 最高原则（宪法级别，几乎不自动分类到 L1）
    (["原则", "宪法", "底线", "绝不", "道德底线", "安全红线", "不可违背",
      "最高原则", "宁可拒绝"],
     "L1", "principle", "global"),

    # L2 — 用户画像
    (["我叫", "我是", "用户是", "老板是", "大哥是", "从事", "职业",
      "专业领域", "金融工程", "量化投资", "居住地", "角色", "身份是"],
     "L2", "identity", "global"),
    (["大扣是", "小扣是", "assistant", "助手", "AI 小弟", "副手",
      "大扣身份", "小扣身份"],
     "L2", "identity", "global"),

    # L3 — 行为偏好
    (["偏好", "习惯", "喜欢", "风格", "不要", "喜欢怎么", "怎么用",
      "工作风格", "沟通风格", "决策模式", "开发流程", "修复流程",
      "重启", "收工", "关窗", "暗号", "触发", "项目根目录",
      "不要自动", "先搜索", "不钻本地"],
     "L3", "preference", "global"),
    (["Skill/MCP", "工具评估", "固定流程", "重复执行"],
     "L3", "preference", "global"),
    # L3 — 否定表达（用户说"不要/不喜欢/别"）
    (["我不要", "我不喜欢", "我讨厌", "我不需要", "以后不要", "再也不要",
      "不要给我", "别给我", "别总是", "不要每次", "不想看到",
      "不想要", "别", "不许", "禁止", "严禁", "杜绝"],
     "L3", "preference", "global"),
    # L3 — 强调/频率表达（用户说"每次都要/必须/绝不"）
    (["每次都要", "每次都要", "一定要", "必须", "总是要", "绝不", "绝对不",
      "从来没有", "从不", "一定不要", "务必", "千万"],
     "L3", "preference", "global"),
    # L3 — 反馈/评价
    (["太慢了", "卡死了", "不好用", "很烦", "烦人", "麻烦", "难用",
      "体验差", "不好", "不够好", "很差", "不行", "算了吧",
      "这个好", "好用", "喜欢这个", "做的不错", "干得不错", "很好用", "真棒"],
     "L3", "preference", "global"),

    # L4 — 工作知识（默认落地层）
    (["项目", "模块", "架构", "技术栈", "数据库", "API", "接口",
      "配置", "部署", "端口", "文件结构", "目录", "路径",
      "踩坑", "bug", "bug", "报错", "出错", "排查",
      "代码在", "启动命令", "python", "streamlit", "DuckDB",
      "因子", "回测", "策略", "量化", "Tushare", "LanceDB",
      "SQLite", "向量库", "嵌入模型", "记忆系统", "memory_core"],
     "L4", "knowledge", "project"),
    # L4 — 打包/发布
    (["打包", "编译", "build", "构建", "发布", "release", "安装包",
      "installer", "NSIS", "nsis", "exe", "setup", "gateway",
      "extraResources", "asar", "electron-builder", "installer.nsh"],
     "L4", "knowledge", "project"),
    # L4 — 前端/UI
    (["前端", "前端", "Electron", "electron", "React", "react", "组件",
      "component", "UI", "界面", "CSS", "css", "HTML", "html", "渲染",
      "xterm", "terminal", "终端", "窗口", "按钮", "菜单", "弹窗",
      "Toast", "toast", "dialog", "modal"],
     "L4", "knowledge", "project"),
    # L4 — 后端/服务
    (["后端", "后端", "FastAPI", "fastapi", "Flask", "flask", "Django",
      "服务端", "服务器", "server", "REST", "WebSocket", "websocket",
      "SSE", "API", "endpoint", "路由", "中间件", "middleware"],
     "L4", "knowledge", "project"),
    # L4 — 基础设施/运维
    (["服务器", "SSH", "ssh", "VNC", "vnc", "云服务器", "腾讯云",
      "阿里云", "Nginx", "nginx", "DNS", "dns", "Cloudflare",
      "cloudflare", "域名", "HTTP", "HTTPS", "SSL", "证书",
      "防火墙", "端口转发", "反向代理"],
     "L4", "knowledge", "project"),
    # L4 — 编码/环境
    (["编码", "encoding", "GBK", "gbk", "UTF-8", "utf-8", "BOM", "bom",
      "乱码", "iconv", "locale", "字符集", "换行符", "CRLF", "LF",
      "环境变量", "PATH", "path", "Node.js", "node", "nodejs",
      "npm", "pnpm", "yarn", "pip", "conda", "virtualenv", "venv"],
     "L4", "knowledge", "project"),
    (["论文", "研究", "调研", "学术", "arXiv", "竞品", "对比",
      "方法论", "算法", "公式"],
     "L4", "knowledge", "global"),

    # L5 — 关系记忆
    (["上次聊", "那天说", "之前讨论", "记得你说", "你跟我说过",
      "我们聊过", "对话", "互动", "感情", "情绪", "信任",
      "关系", "兄弟", "搭档", "伙伴"],
     "L5", "relationship", "global"),
]


def classify_content(content: str) -> dict[str, str]:
    """根据内容自动判断层级、类别、作用域

    返回: {"layer": "L4", "category": "knowledge", "scope": "project"}
    """
    content_lower = content.lower()

    for keywords, layer, category, scope in CLASSIFICATION_RULES:
        for kw in keywords:
            if kw.lower() in content_lower:
                logger.debug(
                    "Classified as %s/%s/%s via keyword '%s'",
                    layer, category, scope, kw,
                )
                return {"layer": layer, "category": category, "scope": scope}

    # 默认 → L4 工作知识
    return {"layer": "L4", "category": "knowledge", "scope": "project"}


# ── 项目 ID 推断（通用化） ─────────────────────────────

# 类标识符 token 匹配：蛇形 / 短横线 / 驼峰 / 帕斯卡
_PROJECT_TOKEN_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+"        # snake_case
    r"|[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+"       # kebab-case
    r"|[a-z]+[A-Z][a-zA-Z0-9]*"                       # camelCase
    r"|[A-Z][a-z0-9]+(?:[A-Z][a-zA-Z0-9]*)+",         # PascalCase
)

# 驼峰/帕斯卡边界：小写或数字后紧跟大写 → 插下划线
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _canonical_token(token: str) -> str:
    """统一为小写蛇形，便于跨命名风格比较与分组"""
    return _CAMEL_BOUNDARY_RE.sub("_", token).replace("-", "_").lower()


def infer_project_id(content: str, current_project: str | None = None) -> str | None:
    """从内容推断所属项目（通用化，不依赖个人项目词表）

    策略：
    1. 提取内容中的类标识符 token（驼峰/蛇形/短横线）。
    2. 若当前项目名（规范化后）命中某 token，优先返回当前项目。
    3. 否则返回最长 token 的规范化结果（更可能是具体项目名）。
    4. 无任何匹配返回 None。

    Args:
        content: 记忆内容
        current_project: 当前活跃项目 ID，优先匹配
    """
    if not content:
        return None

    tokens = _PROJECT_TOKEN_RE.findall(content)
    if not tokens:
        return None

    if current_project:
        cur = _canonical_token(current_project)
        if cur in {_canonical_token(t) for t in tokens}:
            return current_project

    # 最长 token 更可能是项目名（factor_agent 比 agent 更具体）
    best = max(tokens, key=len)
    return _canonical_token(best)


# ── 自动归档 & 过期 ──────────────────────────────────


def check_archival(entry: MemoryEntry, now: datetime | None = None) -> str | None:
    """检查是否需要归档/过期，返回新状态或 None

    两个触发条件（满足任一即触发）：
    1. last_accessed_at 超过阈值（访问衰减）
    2. valid_until 已过期超过 AUTO_EXPIRE_DAYS（时态过期）
    """
    now = now or datetime.now(timezone.utc)
    last = datetime.fromisoformat(entry.last_accessed_at)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)

    days_since_access = (now - last).days

    # 访问衰减
    if entry.status == "active" and days_since_access >= AUTO_ARCHIVE_DAYS:
        return "archived"
    if entry.status == "archived" and days_since_access >= AUTO_EXPIRE_DAYS:
        return "expired"

    # 时态过期：valid_until 已过且超过 AUTO_EXPIRE_DAYS
    if entry.valid_until and entry.status == "active":
        try:
            valid_until_dt = datetime.fromisoformat(entry.valid_until)
            if valid_until_dt.tzinfo is None:
                valid_until_dt = valid_until_dt.replace(tzinfo=timezone.utc)
            days_since_expiry = (now - valid_until_dt).days
            if days_since_expiry >= AUTO_EXPIRE_DAYS:
                return "expired"
        except (ValueError, TypeError):
            pass

    return None


def run_maintenance(sqlite: SQLiteManager) -> dict:
    """执行定期维护：归档/过期一批记忆

    Returns:
        {"archived": N, "expired": N}
    """
    now = datetime.now(timezone.utc)
    stats = {"archived": 0, "expired": 0}

    for entry in sqlite.list_all(status="active"):
        new_status = check_archival(entry, now)
        if new_status:
            sqlite.update(entry.id, status=new_status)
            stats[new_status] = stats.get(new_status, 0) + 1
            logger.info("Memory %s → %s (last accessed %s)",
                        entry.id, new_status, entry.last_accessed_at)

    return stats


# ── 权重计算 ─────────────────────────────────────────


def _compute_temporal_factor(entry: MemoryEntry, now: datetime) -> float:
    """根据 valid_until 计算时态有效性因子

    - valid_until 为 None 或未来 → 1.0（当前有效）
    - valid_until 在过去 ≤TEMPORAL_RECENT_DAYS 天 → TEMPORAL_PENALTY_RECENT
    - valid_until 在过去 >TEMPORAL_RECENT_DAYS 天 → TEMPORAL_PENALTY_EXPIRED
    """
    if entry.valid_until is None:
        return 1.0

    try:
        valid_until_dt = datetime.fromisoformat(entry.valid_until)
    except (ValueError, TypeError):
        return 1.0  # 解析失败，不惩罚

    if valid_until_dt.tzinfo is None:
        valid_until_dt = valid_until_dt.replace(tzinfo=timezone.utc)

    if valid_until_dt >= now:
        return 1.0  # 尚未过期

    days_expired = (now - valid_until_dt).days
    if days_expired <= TEMPORAL_RECENT_DAYS:
        return TEMPORAL_PENALTY_RECENT
    return TEMPORAL_PENALTY_EXPIRED


def compute_weight(entry: MemoryEntry, now: datetime | None = None) -> float:
    """计算记忆的当前权重（base × decay × confidence × temporal_factor）

    公式: weight = base_weight(layer, category) × decay(days_since_access) × confidence × temporal_factor(valid_until)
    """
    from .config import DECAY_SCHEDULE

    now = now or datetime.now(timezone.utc)

    # 基础权重
    base = BASE_WEIGHTS.get((entry.layer, entry.category), 0.70)

    # 时间衰减
    last = datetime.fromisoformat(entry.last_accessed_at)
    # 确保两者都是 aware 或都是 naive
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    days = (now - last).days
    decay = 1.0
    for threshold, factor in DECAY_SCHEDULE:
        if days <= threshold:
            decay = factor
            break

    # 确认度
    confidence = entry.confidence

    # 时态有效性
    temporal = _compute_temporal_factor(entry, now)

    weight = base * decay * confidence * temporal
    return round(weight, 4)


def refresh_weights(sqlite: SQLiteManager) -> int:
    """刷新所有活跃记忆的权重

    Returns:
        更新的记忆数
    """
    now = datetime.now(timezone.utc)
    count = 0
    for entry in sqlite.list_all(status="active"):
        new_weight = compute_weight(entry, now)
        if abs(new_weight - entry.weight) > 0.001:
            sqlite.update(entry.id, weight=new_weight)
            count += 1
    return count


# ── L4 子目录分类（巡检用） ─────────────────────────────

# L4 子目录关键词规则（与 memory-classification-rules.md §3 同步）
L4_SUBDIR_KEYWORDS: dict[str, list[str]] = {
    "pitfalls": [
        "踩坑", "pitfall", "bug", "报错", "出错", "排查", "修复", "fix",
        "失败", "error", "500", "502", "timeout", "超时", "崩溃", "crash",
        "不工作", "无法", "不能", "问题", "故障", "异常", "warning",
        "教训", "注意", "小心", "陷阱", "坑",
    ],
    "infrastructure": [
        "SSH", "ssh", "VNC", "vnc", "服务器", "云服务器", "腾讯云",
        "阿里云", "Nginx", "nginx", "DNS", "dns", "Cloudflare",
        "cloudflare", "域名", "HTTP", "HTTPS", "SSL", "证书",
        "防火墙", "端口转发", "反向代理", "部署", "deploy",
        "环境", "配置", "config", "settings", "setup", "安装",
        "infrastructure", "网关", "gateway", "隧道", "tunnel",
        "CC-Switch", "Codex", "codex", "编码", "encoding",
        "GBK", "utf-8", "乱码", "Node.js", "node", "npm",
        "pip", "conda", "Python", "python", "依赖",
        "数据库", "database", "DuckDB", "SQLite", "LanceDB",
        "存储", "store", "向量库", "嵌入", "embedding",
        "数据管线", "数据系统", "自动化", "hook", "脚本",
        "scheduler", "定时", "守护", "daemon", "systemd",
    ],
    "research": [
        "论文", "研究", "调研", "学术", "arXiv", "竞品", "对比",
        "方法论", "算法", "公式", "因子", "factor", "回测",
        "backtest", "量化", "quant", "策略", "strategy",
        "分析", "analysis", "review", "survey", "文献",
        "理论", "模型", "实证", "回归", "regression",
        "遗传算法", "GA", "机器学习", "machine learning",
        "深度学习", "deep learning", "NLP", "OCR",
    ],
    "projects": [
        "项目", "project", "模块", "module", "功能", "feature",
        "需求", "requirement", "版本", "version", "发布", "release",
        "进度", "progress", "计划", "plan", "TODO", "roadmap",
        "开发", "develop", "构建", "build", "打包", "package",
        "应用", "app", "工具", "tool", "平台", "platform",
        "网站", "website", "前端", "frontend", "后端", "backend",
        "UI", "界面", "组件", "component", "React", "Electron",
        "Streamlit", "API", "endpoint", "接口",
    ],
}


def classify_l4_subdir(content: str) -> tuple[str, dict[str, int]]:
    """根据内容关键词推断 L4 文件应归入的子目录。

    Returns:
        (subdir_name, scores_dict)
        subdir_name: "projects" | "infrastructure" | "pitfalls" | "research"
        scores_dict: 每个子目录的关键词命中数
        默认 "projects"
    """
    text = content[:1000].lower()
    scores = {}
    for subdir, keywords in L4_SUBDIR_KEYWORDS.items():
        scores[subdir] = sum(1 for kw in keywords if kw.lower() in text)
    best = max(scores, key=scores.get)
    return (best if scores[best] > 0 else "projects"), scores


# L4 子目录 → 标准子目录名映射
L4_SUBDIR_NAMES = {"projects", "infrastructure", "pitfalls", "research", "archive", "knowledge"}

def validate_l4_classification(memory_dir: str | Path) -> list[dict]:
    """巡检：检查 L4 文件的结构性异常。

    不检查「这个文件该放哪个子目录」——人工分类最准，关键词只会瞎报。
    只检查管线 bug 产生的客观异常：

    1. 破损文件名（UUID_---.md）—— isalnum() 剥中文的残留
    2. 异常目录名（不应存在的目录，如 knowledges/）

    Args:
        memory_dir: memory/ 目录路径

    Returns:
        [{"file": "rel/path", "issue": "broken_filename"|"bad_directory", ...}, ...]
    """
    from pathlib import Path
    memory_dir = Path(memory_dir)
    l4_dir = memory_dir / "L4_knowledge"
    if not l4_dir.is_dir():
        return []

    anomalies = []

    # 检查 1：异常目录名
    _KNOWN_GOOD_DIRS = {"projects", "infrastructure", "pitfalls", "research", "archive", "knowledge", "logs"}
    for d in l4_dir.iterdir():
        if d.is_dir() and d.name not in _KNOWN_GOOD_DIRS and not d.name.startswith("_"):
            anomalies.append({
                "file": f"{d.name}/",
                "issue": "bad_directory",
                "detail": f"Unknown L4 subdirectory (pipeline bug?)",
            })

    # 检查 2：inbox 积压
    inbox_dir = memory_dir / "inbox"
    if inbox_dir.is_dir():
        inbox_files = sorted(inbox_dir.glob("*.md"))
        if inbox_files:
            anomalies.append({
                "file": f"inbox/ ({len(inbox_files)} files)",
                "issue": "inbox_pending",
                "detail": "Files waiting for classification triage",
            })

    # 检查 3：破损文件名
    for md_file in sorted(l4_dir.rglob("*.md")):
        if "_---" in md_file.name:
            rel = md_file.relative_to(l4_dir)
            anomalies.append({
                "file": str(rel),
                "issue": "broken_filename",
                "detail": "UUID_---.md pattern (isalnum stripped Chinese)",
            })

    return anomalies
