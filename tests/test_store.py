import tempfile, unittest
from pathlib import Path
from memory_core import MemoryCoreConfig
from memory_core.store import MemoryStore, MemoryEntry

class TestStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = MemoryCoreConfig(data_dir=Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_add_and_get(self):
        s = MemoryStore(self.cfg)
        mid = s.add(content="测试记忆内容", layer="L4", category="knowledge",
                    scope="project", project_id="demo", tags=["test"])
        got = s.get(mid)
        self.assertEqual(got.content, "测试记忆内容")
        self.assertEqual(got.layer, "L4")

    def test_find_by_source_file_replace(self):
        s = MemoryStore(self.cfg)
        s.add(content="# A\n旧内容", layer="L4", category="knowledge",
              scope="project", source_file="CLAUDE.md")
        # 同 source_file 再次灌入 → 替换而非追加
        s.add(content="# A\n新内容", layer="L4", category="knowledge",
              scope="project", source_file="CLAUDE.md")
        rows = s.find_by_source_file("CLAUDE.md")
        self.assertEqual(len(rows), 1)
        self.assertIn("新内容", rows[0].content)

    def test_content_dedup(self):
        s = MemoryStore(self.cfg)
        a = s.add(content="完全相同的句子", layer="L4", category="knowledge",
                  scope="project", dedup=True)
        b = s.add(content="完全相同的句子", layer="L4", category="knowledge",
                  scope="project", dedup=True)
        self.assertEqual(a, b)  # 重复返回同一 id

if __name__ == "__main__":
    unittest.main()
