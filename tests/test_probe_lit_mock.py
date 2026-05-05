# test_probe_lit_mock.py —— 乾坤镜 LIT 适配器 Mock 测试

import sys
import types
import unittest
from pathlib import Path

_src = Path(__file__).parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

_captured_events = []


def _setup_mcp_modules():
    class FakeClientSession:
        async def call_tool(self, name, *args, **kwargs):
            return {"result": "ok"}

        async def read_resource(self, uri, *args, **kwargs):
            return {"content": "resource"}

    class FakeServer:
        async def handle_request(self, *args, **kwargs):
            return {"ok": True}

    fake_client = types.ModuleType("mcp.client")
    fake_client_session = types.ModuleType("mcp.client.session")
    fake_client_session.ClientSession = FakeClientSession
    fake_server = types.ModuleType("mcp.server")
    fake_server.Server = FakeServer

    sys.modules["mcp"] = types.ModuleType("mcp")
    sys.modules["mcp.client"] = fake_client
    sys.modules["mcp.client.session"] = fake_client_session
    sys.modules["mcp.server"] = fake_server


class TestLITAdapter(unittest.TestCase):
    def setUp(self):
        global _captured_events
        _captured_events.clear()
        _setup_mcp_modules()
        from probe_uni import ProbeUni

        self._orig_emit = ProbeUni.emit

        def capture_emit(self, event_type, payload):
            _captured_events.append({"event_type": event_type, "payload": payload})

        ProbeUni.emit = capture_emit

    def tearDown(self):
        from probe_uni import ProbeUni

        ProbeUni.emit = self._orig_emit
        for mod in list(sys.modules.keys()):
            if mod.startswith("mcp"):
                del sys.modules[mod]

    def test_adapter_imports_without_error(self):
        from adapters.probe_lit import init_lit_probe

        self.assertTrue(callable(init_lit_probe))

    def test_monkey_patch_applies_to_call_tool(self):
        from adapters.probe_lit import init_lit_probe

        probe = init_lit_probe()
        from mcp.client.session import ClientSession

        self.assertNotEqual(ClientSession.call_tool.__name__, "call_tool")

    def test_monkey_patch_applies_to_read_resource(self):
        from adapters.probe_lit import init_lit_probe

        probe = init_lit_probe()
        from mcp.client.session import ClientSession

        self.assertNotEqual(ClientSession.read_resource.__name__, "read_resource")


if __name__ == "__main__":
    unittest.main()
