import tempfile, unittest
from pathlib import Path
from memory_core import MemoryCore
from memory_core.importer import import_workspace, import_path

class TestImporter(unittest.TestCase):
    def _make_ws(self, root: Path):
        (root / "CLAUDE.md").write_text(
            "## 原则\n绝不删除用户数据\n## 偏好\n中文交流\n", encoding="utf-8")
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
            self.assertGreater(result["imported"], 0)
            self.assertIn("L1", result["by_layer"])   # CLAUDE.md 原则
            self.assertIn("L3", result["by_layer"])   # 偏好 + skill
            self.assertIn("L4", result["by_layer"])   # README
            # 幂等：再扫一次不新增
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

if __name__ == "__main__":
    unittest.main()
