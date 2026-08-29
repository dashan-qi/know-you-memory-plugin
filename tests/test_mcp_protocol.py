import io, json, unittest
from pathlib import Path
from servers.mcp_server import MemoryCoreBackend, rpc_reply, read_message, send_message

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

    def test_mcp_content_length_framing_roundtrip(self):
        # 模拟规范客户端：Content-Length 帧发 initialize → 响应必须以帧开头、
        # 正文可解析为 JSON-RPC
        req = {"jsonrpc": "2.0", "method": "initialize",
               "params": {"protocolVersion": "2025-06-18"}, "id": 1}
        payload = json.dumps(req, ensure_ascii=False).encode("utf-8")
        framed = b"Content-Length: %d\r\n\r\n" % len(payload) + payload

        msg = read_message(io.BytesIO(framed))
        self.assertIsNotNone(msg)
        self.assertEqual(msg["method"], "initialize")
        self.assertEqual(msg["id"], 1)

        reply = self.be.handle("initialize", msg.get("params") or {}, msg.get("id"))
        out = io.BytesIO()
        send_message(out, reply)
        data = out.getvalue()
        self.assertTrue(data.startswith(b"Content-Length:"))
        head, sep, body = data.partition(b"\r\n\r\n")
        self.assertEqual(sep, b"\r\n\r\n")
        n = int(head.split(b":")[1].strip())
        parsed = json.loads(body[:n].decode("utf-8"))
        self.assertEqual(parsed["jsonrpc"], "2.0")
        self.assertEqual(parsed["id"], 1)
        self.assertEqual(parsed["result"]["serverInfo"]["name"], "know-you-memory")

    def test_read_message_newline_fallback(self):
        # 兼容 newline-delimited 客户端（无 Content-Length 头）
        msg = read_message(io.BytesIO(b'{"jsonrpc":"2.0","method":"ping","id":9}\n'))
        self.assertIsNotNone(msg)
        self.assertEqual(msg["method"], "ping")
        self.assertEqual(msg["id"], 9)

    def test_read_message_skips_non_dict(self):
        # 合法 JSON 但非对象帧 → 返回 None，主循环跳过不崩
        self.assertIsNone(read_message(io.BytesIO(b"[1,2,3]\n")))
        self.assertIsNone(read_message(io.BytesIO(b"null\n")))
        self.assertIsNone(read_message(io.BytesIO(b"")))

if __name__ == "__main__":
    unittest.main()
