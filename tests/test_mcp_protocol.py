import json, unittest
from pathlib import Path
from servers.mcp_server import MemoryCoreBackend, rpc_reply

REPO = Path(__file__).resolve().parent.parent

class TestMCPProtocol(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.be = MemoryCoreBackend(data_dir=Path(self._tmp.name) / "memory")

    def tearDown(self):
        self.be.mc.close()
        self._tmp.cleanup()

    def test_initialize(self):
        reply = self.be.handle("initialize", {"protocolVersion": "2025-06-18"}, 1)
        self.assertEqual(reply["result"]["capabilities"]["tools"], {})
        self.assertEqual(reply["result"]["serverInfo"]["name"], "know-you-memory")

    def test_tools_list(self):
        reply = self.be.handle("tools/list", {}, 2)
        names = [t["name"] for t in reply["result"]["tools"]]
        self.assertIn("memory_add", names)
        self.assertIn("memory_recall", names)
        self.assertIn("memory_status", names)
        self.assertIn("memory_import", names)

    def test_memory_add_then_recall(self):
        self.be.handle("tools/call", {"name": "memory_add",
            "arguments": {"content": "MCP 测试记忆 18792"}}, 3)
        reply = self.be.handle("tools/call", {"name": "memory_recall",
            "arguments": {"query": "MCP 测试记忆"}}, 4)
        text = json.dumps(reply, ensure_ascii=False)
        self.assertIn("18792", text)

    def test_rpc_reply_shape(self):
        obj = rpc_reply(1, {"ok": True})
        self.assertEqual(obj["jsonrpc"], "2.0")
        self.assertEqual(obj["id"], 1)
        self.assertIn("result", obj)

if __name__ == "__main__":
    unittest.main()
