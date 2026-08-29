import os, tempfile, unittest
from pathlib import Path

class TestConfig(unittest.TestCase):
    def test_default_data_dir_env_override(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["MEMORY_CORE_DATA"] = d
            import importlib, memory_core.config as c
            importlib.reload(c)
            self.assertEqual(c.DEFAULT_DATA_DIR, Path(d))
            del os.environ["MEMORY_CORE_DATA"]
            importlib.reload(c)

    def test_core_memory_files_removed(self):
        import memory_core.config as c
        self.assertFalse(hasattr(c, "CORE_MEMORY_FILES"))  # 个人清单已剔除

if __name__ == "__main__":
    unittest.main()
