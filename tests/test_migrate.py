# test_migrate.py —— 乾坤镜 migrations/migrate 测试

import sys, unittest, sqlite3, tempfile, shutil
from pathlib import Path

_src = Path(__file__).parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from migrations.migrate import (
    _sqlite_version,
    _patch_legacy_sql,
    run_migrations,
    CURRENT_VERSION,
    MIGRATIONS_DIR,
)


class TestMigrate(unittest.TestCase):
    def test_sqlite_version(self):
        ver = _sqlite_version()
        self.assertIsInstance(ver, tuple)
        self.assertGreaterEqual(len(ver), 3)

    def test_patch_legacy_sql(self):
        sql = "ALTER TABLE events ADD COLUMN IF NOT EXISTS new_col TEXT"
        result = _patch_legacy_sql(sql)
        self.assertNotIn("IF NOT EXISTS", result)
        self.assertIn("ADD COLUMN", result)

    def test_patch_legacy_sql_no_match(self):
        sql = "SELECT * FROM events"
        result = _patch_legacy_sql(sql)
        self.assertEqual(result, sql)

    def test_run_migrations_no_db(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "noexist.db"
            success, msg = run_migrations(db_path)
            self.assertTrue(success)
            self.assertEqual(msg, "no_db")

    def test_run_migrations_skip(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE foo (id INTEGER)")
            conn.close()
            success, msg = run_migrations(db_path, skip=True)
            self.assertTrue(success)
            self.assertEqual(msg, "skipped")

    def test_run_migrations_create_schema_version(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "fresh.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY)")
            conn.commit()
            conn.close()

    def test_current_version(self):
        self.assertIsInstance(CURRENT_VERSION, int)
        self.assertGreater(CURRENT_VERSION, 0)

    def test_migrations_dir_exists(self):
        self.assertTrue(MIGRATIONS_DIR.exists())


if __name__ == "__main__":
    unittest.main()
