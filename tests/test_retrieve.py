import tempfile, unittest
from pathlib import Path
from memory_core import MemoryCoreConfig
from memory_core.store import MemoryStore
from memory_core.retrieve import MemoryRetriever

class TestRetrieve(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(MemoryCoreConfig(data_dir=Path(self._tmp.name)))
        self.retriever = MemoryRetriever(self.store)

    def tearDown(self):
        # Windows 下 SQLite 连接持有文件锁，先 close 释放 memory.db 才能删临时目录
        self.store.close()
        self._tmp.cleanup()

    def test_tfidf_recall_relevant(self):
        a = self.store.add(content="DuckDB ATTACH 模式日期类型不兼容，改直连", layer="L4",
                           category="pitfall", scope="project", project_id="factor-agent")
        self.store.add(content="红利策略用分红率筛选股票", layer="L4", category="project",
                       scope="project", project_id="dividend")
        self.retriever.add_to_index(a, "DuckDB ATTACH 模式日期类型不兼容，改直连")
        hits = self.retriever.retrieve("DuckDB 日期问题怎么修", max_injected=5)
        self.assertTrue(any("DuckDB" in e.content for e in hits))

    def test_add_to_index_incremental(self):
        mid = self.store.add(content="窗口传参要用空格分隔", layer="L4", category="pitfall",
                             scope="project")
        self.retriever.add_to_index(mid, "窗口传参要用空格分隔")  # 不抛异常
        hits = self.retriever.retrieve("窗口传参", max_injected=5)
        self.assertEqual(len(hits), 1)

if __name__ == "__main__":
    unittest.main()
