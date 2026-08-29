import tempfile, unittest
from pathlib import Path
from memory_core import MemoryCore
from memory_core.importer import import_workspace, import_path

class TestImporter(unittest.TestCase):
    def _make_ws(self, root: Path):
        # CLAUDE.md 含两个 `##` 节：原则（→L1）+ 偏好（→L3），必须分块各自落层
        (root / "CLAUDE.md").write_text(
            "## 原则\n绝不删除用户数据\n## 偏好\n喜欢中文交流\n", encoding="utf-8")
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
            # CLAUDE.md 分两块（原则+偏好）+ SKILL.md + README.md = 4 条
            self.assertEqual(result["imported"], 4)
            self.assertEqual(result["by_layer"].get("L1", 0), 1)   # 原则 分块 → L1
            self.assertGreaterEqual(result["by_layer"].get("L3", 0), 1)  # 偏好 分块 + skill → L3
            self.assertIn("L4", result["by_layer"])   # README
            # 幂等：再扫一次不新增（每块 source_file::slug 各自 hash 命中）
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

    def test_duplicate_heading_slug_collision(self):
        # 同文件两个相同 `## 标题`（真实 CLAUDE.md 常见）：slug 必须带块序号区分，
        # 否则两块 source_file 相同 → 重扫时 hash 键冲突、每块都重灌（imported != 0）
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "CLAUDE.md").write_text(
                "## 偏好\n喜欢中文交流\n## 偏好\n喜欢用番茄钟\n", encoding="utf-8")
            mc = MemoryCore(data_dir=root / "memory")
            mc.close()
            first = import_workspace(root, data_dir=root / "memory")
            self.assertEqual(first["imported"], 2)
            again = import_workspace(root, data_dir=root / "memory")
            self.assertEqual(again["imported"], 0)

if __name__ == "__main__":
    unittest.main()
