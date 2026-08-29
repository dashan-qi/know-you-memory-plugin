import unittest
from memory_core.classify import classify_content, infer_project_id

class TestClassify(unittest.TestCase):
    def test_principle_goes_l1(self):
        r = classify_content("核心原则：绝不删除用户数据，任何删除必须进回收站")
        self.assertEqual(r["layer"], "L1")
        self.assertEqual(r["category"], "principle")

    def test_preference_goes_l3(self):
        r = classify_content("偏好：中文交流，效率优先，测试产物放项目目录")
        self.assertEqual(r["layer"], "L3")

    def test_generic_knowledge_goes_l4(self):
        r = classify_content("DuckDB 直连模式性能优于 ATTACH")
        self.assertEqual(r["layer"], "L4")

    def test_project_id_inferred_from_path_word(self):
        pid = infer_project_id("这个 factor_agent 的端口是 8501")
        self.assertIn("factor", pid or "")

if __name__ == "__main__":
    unittest.main()
