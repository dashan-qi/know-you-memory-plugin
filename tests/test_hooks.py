import io, tempfile, unittest
from contextlib import redirect_stdout
from pathlib import Path
from hooks import session_start

REPO = Path(__file__).resolve().parent.parent

class TestSessionStart(unittest.TestCase):
    def test_first_run_auto_imports_and_reports(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "CLAUDE.md").write_text("## 原则\n绝不删除用户数据\n", encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                session_start.main([str(root)], data_dir_override=root / "memory")
            out = buf.getvalue()
            self.assertIn("首次分拣", out)
            self.assertIn("L1", out)

    def test_nonempty_skips_import(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dd = root / "memory"
            # 预置一条记忆，使库非空
            from memory_core import MemoryCore
            mc = MemoryCore(data_dir=dd); mc.add("已有记忆"); mc.close()
            buf = io.StringIO()
            with redirect_stdout(buf):
                session_start.main([str(root)], data_dir_override=dd)
            self.assertNotIn("首次分拣", buf.getvalue())

if __name__ == "__main__":
    unittest.main()
