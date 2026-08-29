import io, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from memory_core import MemoryCore
from engine.bootstrap import ensure_data_dir

REPO = Path(__file__).resolve().parent.parent

class TestBootstrap(unittest.TestCase):
    def test_ensure_data_dir_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            dd = ensure_data_dir(Path(d) / "memory")
            self.assertTrue((dd / "memory.db").exists())
            again = ensure_data_dir(Path(d) / "memory")   # 不抛异常
            self.assertEqual(again, dd)

class TestCLI(unittest.TestCase):
    def _run(self, *args, data_dir):
        # 保留父进程环境（Windows 下必须含 USERPROFILE/PATH，否则 Path.home()/DLL 加载失败）
        env = dict(os.environ)
        env.pop("MEMORY_CORE_VECTORS", None)  # 测试始终走默认（快）路径，不受宿主环境向量开关影响
        env["MEMORY_CORE_DATA"] = str(data_dir)
        env["PYTHONPATH"] = str(REPO / "engine")
        # cli.py 统一 UTF-8 输出（含中文），Windows 下必须显式按 utf-8 解码；
        # stderr 有 jieba/tqdm 的 GBK 日志，errors="replace" 只容忍噪音不崩溃
        return subprocess.run([sys.executable, str(REPO / "engine" / "cli.py"), *args],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env)

    def test_cli_recall_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d) / "memory"
            r1 = self._run("add", "CLI 测试条目：端口 18792", data_dir=dd)
            self.assertEqual(r1.returncode, 0)
            r2 = self._run("recall", "端口", data_dir=dd)
            self.assertIn("18792", r2.stdout)

    def test_cli_status(self):
        with tempfile.TemporaryDirectory() as d:
            self._run("add", "状态测试", data_dir=Path(d) / "memory")
            r = self._run("status", data_dir=Path(d) / "memory")
            self.assertEqual(r.returncode, 0)
            self.assertIn("total", r.stdout)

    def test_cli_inspect(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d) / "memory"
            self._run("add", "巡检测试条目：端口偏好", data_dir=dd)
            r = self._run("inspect", data_dir=dd)
            self.assertEqual(r.returncode, 0)
            rep = json.loads(r.stdout)
            self.assertIn("status_counts", rep)
            self.assertIn("by_layer", rep)
            self.assertIn("active_total", rep)
            self.assertEqual(rep["active_total"], 1)
            # 至少有一条落在某个层（L0-L5/LCM）
            self.assertGreaterEqual(sum(rep["by_layer"].values()), 1)

    def test_cli_inspect_clean(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d) / "memory"
            self._run("add", "巡检 clean 冒烟", data_dir=dd)
            r = self._run("inspect", "--clean", data_dir=dd)
            self.assertEqual(r.returncode, 0)
            rep = json.loads(r.stdout)
            self.assertIn("maintenance", rep)
            self.assertIn("weights_refreshed", rep)

if __name__ == "__main__":
    unittest.main()
