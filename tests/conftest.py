def pytest_configure(config):
    config.addinivalue_line("markers", "stress: 压力测试（大版本才跑，日常跳过）")
    config.addinivalue_line("markers", "slow: 长时间测试（CI 可跑，本地开发跳过）")
