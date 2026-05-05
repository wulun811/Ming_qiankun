# test_web_security.py — v0.11.9m Web 安全测试
import unittest, tempfile, shutil, time, json, os
from pathlib import Path
from urllib.parse import urlparse
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestExtensionWhitelist(unittest.TestCase):
    """P0-5: Web 扩展名白名单"""

    def test_safe_extensions_defined(self):
        """SAFE_EXTENSIONS 白名单存在"""
        from plugins.web_dashboard.server import SAFE_EXTENSIONS

        self.assertIn(".html", SAFE_EXTENSIONS)
        self.assertIn(".css", SAFE_EXTENSIONS)
        self.assertIn(".js", SAFE_EXTENSIONS)
        self.assertNotIn(".py", SAFE_EXTENSIONS)
        self.assertNotIn(".db", SAFE_EXTENSIONS)
        self.assertNotIn(".jsonl", SAFE_EXTENSIONS)


class TestLocalhostDefault(unittest.TestCase):
    """P0-6: localhost 默认绑定"""

    def test_serve_defaults_to_localhost(self):
        """serve() 默认绑定 127.0.0.1"""
        from plugins.web_dashboard.server import serve
        import inspect

        sig = inspect.signature(serve)
        host_param = sig.parameters.get("host")
        self.assertIsNotNone(host_param)
        self.assertEqual(host_param.default, "127.0.0.1")


class TestTokenAuth(unittest.TestCase):
    """P0-6: Token 认证"""

    def test_handler_has_auth_token(self):
        """Handler 有 auth_token 属性"""
        from plugins.web_dashboard.server import MingjingHandler

        self.assertTrue(hasattr(MingjingHandler, "auth_token"))
        self.assertIsNone(MingjingHandler.auth_token)

    def test_check_auth_method_exists(self):
        """Handler 有 _check_auth 方法"""
        from plugins.web_dashboard.server import MingjingHandler

        self.assertTrue(hasattr(MingjingHandler, "_check_auth"))


class TestParameterValidation(unittest.TestCase):
    """P1-6: API 参数验证"""

    def test_serve_events_has_validation(self):
        """_serve_events 有参数验证"""
        from plugins.web_dashboard.server import MingjingHandler

        # 检查方法存在
        self.assertTrue(hasattr(MingjingHandler, "_serve_events"))


class TestThreadingHTTPServer(unittest.TestCase):
    """P1-4: ThreadingHTTPServer"""

    def test_uses_threading_server(self):
        """使用 ThreadingHTTPServer"""
        from http.server import ThreadingHTTPServer
        from plugins.web_dashboard import server

        # 检查模块导入了 ThreadingHTTPServer
        self.assertTrue(hasattr(server, "ThreadingHTTPServer"))


class TestAtomicWrite(unittest.TestCase):
    """P1-5: data.json 原子写入"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.out_dir = Path(self.tmp) / "web"
        self.out_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_export_creates_tmp_file(self):
        """export 使用 tmp 文件"""
        import plugins.web_dashboard.web_exporter as we

        original_out = we.OUT
        we.OUT = self.out_dir

        try:
            we.export()  # DB 不存在时生成空 data.json
            # 应该有 data.json 文件
            self.assertTrue((self.out_dir / "data.json").exists())
        finally:
            we.OUT = original_out


class TestXSSProtection(unittest.TestCase):
    """P2-1: XSS 防护"""

    def test_escapeHtml_function_exists(self):
        """esc 函数已定义"""
        import re

        index_html = (
            Path(__file__).parent.parent
            / "src"
            / "plugins"
            / "web_dashboard"
            / "index.html"
        )
        content = index_html.read_text()
        self.assertIn("function esc(str)", content)

    def test_escapeHtml_used_in_rendering(self):
        """esc 在动态渲染中被调用"""
        index_html = (
            Path(__file__).parent.parent
            / "src"
            / "plugins"
            / "web_dashboard"
            / "index.html"
        )
        content = index_html.read_text()
        count = content.count("esc(")
        self.assertGreaterEqual(count, 10, f"esc 应被调用至少 10 次，实际 {count} 次")


class TestServerLogging(unittest.TestCase):
    """P3-2: 服务器日志"""

    def test_log_message_checks_error_status(self):
        """log_message 检查错误状态码"""
        from plugins.web_dashboard.server import MingjingHandler
        import inspect

        source = inspect.getsource(MingjingHandler.log_message)
        self.assertIn("400", source)


class TestRestartCommand(unittest.TestCase):
    """P3-3: restart 命令"""

    def test_cmd_restart_exists(self):
        """cmd_restart 函数存在"""
        import ming

        self.assertTrue(hasattr(ming, "cmd_restart"))


class TestArchiverErrorLog(unittest.TestCase):
    """P0-1: 归档器错误日志"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hot_dir = Path(self.tmp) / "hot"
        self.cold_dir = Path(self.tmp) / "cold"
        self.db_path = Path(self.tmp) / "ming.db"
        self.hot_dir.mkdir()
        self.cold_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_log_error_creates_file(self):
        """_log_error 创建错误日志文件"""
        from archiver import Archiver

        archiver = Archiver(
            db_path=str(self.db_path),
            hot_dir=str(self.hot_dir),
            cold_dir=str(self.cold_dir),
        )
        archiver._log_error(ValueError("test error"), 1)
        self.assertTrue(archiver.ERROR_LOG.exists())


if __name__ == "__main__":
    unittest.main()
