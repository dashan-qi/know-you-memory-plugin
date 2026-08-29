import tempfile, unittest
from pathlib import Path
from memory_core import MemoryCore

class TestCore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.mc = MemoryCore(data_dir=Path(self._tmp.name))

    def tearDown(self):
        self.mc.close()
        self._tmp.cleanup()

    def test_add_auto_classify(self):
        mid = self.mc.add("踩坑：Resend 会拦截 UA 不标准请求")
        self.assertTrue(mid)

    def test_recall_roundtrip(self):
        self.mc.add("灵犀 v3.18 的 MCP Bus 端口是 18792")
        results = self.mc.recall("灵犀 MCP Bus 端口多少", max_results=3)
        self.assertTrue(any("18792" in e.content for e, v in results))

    def test_stats_shape(self):
        self.mc.add("测试条目")
        st = self.mc.stats()
        self.assertIn("total", st)
        self.assertIn("by_layer", st)
        self.assertGreaterEqual(st["total"], 1)

    def test_consolidate_heuristic(self):
        res = self.mc.consolidate(session_id="s1", conversation="今天修好了窗口传参 bug，原因是空格问题")
        self.assertIsNotNone(res)  # 启发式固化不依赖 LLM

if __name__ == "__main__":
    unittest.main()
