#!/usr/bin/env python3
# test_plugin_runner_p3.py —— P3 插件执行器补全测试
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

import plugin_runner


class TestPluginRunnerP3(unittest.TestCase):
    """P3 plugin_runner 补全测试"""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="ming_plugin_test_"))
        cls._orig_plugins = plugin_runner.PLUGINS
        cls._orig_state = plugin_runner.STATE
        cls._orig_log = plugin_runner.LOG
        plugin_runner.PLUGINS = cls.temp_dir / "plugins"
        plugin_runner.STATE = cls.temp_dir / ".plugin_state.json"
        plugin_runner.LOG = cls.temp_dir / ".plugin_runner.log"

    @classmethod
    def tearDownClass(cls):
        plugin_runner.PLUGINS = cls._orig_plugins
        plugin_runner.STATE = cls._orig_state
        plugin_runner.LOG = cls._orig_log
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def setUp(self):
        plugin_runner.PLUGINS.mkdir(parents=True, exist_ok=True)
        yaml_content = (
            "name: test_echo\n"
            "version: 0.1.0\n"
            "type: cron\n"
            'description: "测试插件"\n'
            "trigger:\n"
            "  type: cron\n"
            '  cron: "*/60 * * * * *"\n'
            "install:\n"
            '  entrypoint: "echo hello"\n'
            "output:\n"
            "  format: json\n"
            '  path: ""\n'
        )
        test_plugin = plugin_runner.PLUGINS / "test_echo"
        test_plugin.mkdir(parents=True, exist_ok=True)
        (test_plugin / "test_echo.skill.yaml").write_text(
            yaml_content, encoding="utf-8"
        )

    def test_01_list_plugins(self):
        plugins = plugin_runner.list_plugins()
        self.assertEqual(len(plugins), 1, "插件数 != 1")
        self.assertEqual(plugins[0]["name"], "test_echo", "name != test_echo")
        self.assertTrue(plugins[0]["enabled"], "enabled != True")
        print("  PASS: list_plugins")

    def test_02_enable_disable(self):
        plugin_runner.disable("test_echo")
        self.assertFalse(
            plugin_runner.is_enabled("test_echo"), "禁用后 is_enabled != False"
        )
        plugin_runner.enable("test_echo")
        self.assertTrue(
            plugin_runner.is_enabled("test_echo"), "启用后 is_enabled != True"
        )
        print("  PASS: enable/disable")

    def test_03_schema_validation(self):
        valid, reason = plugin_runner._validate_schema(
            {"name": "test", "version": "0.1.0", "type": "cron", "description": "ok"}
        )
        self.assertTrue(valid, "有效配置应通过校验")
        valid, reason = plugin_runner._validate_schema({"name": "test"})
        self.assertFalse(valid, "缺少字段应不通过")
        valid, reason = plugin_runner._validate_schema(
            {"name": "test", "version": "bad", "type": "cron", "description": "ok"}
        )
        self.assertFalse(valid, "版本格式错误应不通过")
        valid, reason = plugin_runner._validate_schema(
            {"name": "test", "version": "0.1.0", "type": "invalid", "description": "ok"}
        )
        self.assertFalse(valid, "type 无效应不通过")
        print("  PASS: Schema 校验")

    def test_04_run_plugin(self):
        result = plugin_runner.run_plugin("test_echo")
        self.assertTrue(result, "执行失败")
        hb = plugin_runner.PLUGINS / "test_echo" / ".plugin_heartbeat"
        self.assertTrue(hb.exists(), "心跳文件不存在")
        print("  PASS: run_plugin")

    def test_05_logging(self):
        plugin_runner.run_plugin("test_echo")
        log = plugin_runner.LOG
        self.assertTrue(log.exists(), "日志文件不存在")
        if log.exists():
            content = log.read_text(encoding="utf-8")
            self.assertGreater(len(content), 0, "日志内容为空")
        print("  PASS: 日志")

    def test_06_output_validation(self):
        out_path = self.temp_dir / "test_output.json"
        out_path.write_text('{"status": "ok"}', encoding="utf-8")
        cfg = {"output": {"format": "json", "path": str(out_path)}}
        valid, reason = plugin_runner._validate_output(
            plugin_runner.PLUGINS / "test_echo", cfg
        )
        self.assertTrue(valid, "有效 JSON 应通过")
        out_path.write_text("{invalid}", encoding="utf-8")
        valid, reason = plugin_runner._validate_output(
            plugin_runner.PLUGINS / "test_echo", cfg
        )
        self.assertFalse(valid, "无效 JSON 应不通过")
        print("  PASS: 输出格式校验")

    def test_07_run_all(self):
        plugin_runner.run_all()
        self.assertTrue(True, "run_all 执行完成")
        print("  PASS: run_all")


if __name__ == "__main__":
    unittest.main(verbosity=2)
