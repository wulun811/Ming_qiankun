# migrate.py —— v0.11.9m Schema 迁移执行器
# 职责：版本检测 + SQLite 版本兼容 + 三层防护（备份 + 降级 + --skip-migrate）
# 纯标准库，零第三方依赖
import os, sqlite3, shutil, time
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent
CURRENT_VERSION = 3  # v0.11.9m 最终版本


def _sqlite_version():
    """获取 SQLite 版本号元组"""
    ver = sqlite3.sqlite_version
    return tuple(int(x) for x in ver.split("."))


def _patch_legacy_sql(sql):
    """SQLite <3.35.0 兼容：替换 ADD COLUMN IF NOT EXISTS"""
    import re

    # 移除 IF NOT EXISTS
    sql = re.sub(
        r"ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS", "ADD COLUMN", sql, flags=re.IGNORECASE
    )
    return sql


def run_migrations(db_path, skip=False):
    """执行迁移，返回 (success, message)"""
    if skip:
        print("[migrate] 跳过迁移（--skip-migrate）")
        return True, "skipped"

    db_path = Path(db_path)
    if not db_path.exists():
        print("[migrate] 数据库不存在，跳过迁移")
        return True, "no_db"

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")

    sqlite_ver = _sqlite_version()
    print(f"[migrate] SQLite 版本: {'.'.join(map(str, sqlite_ver))}")

    # 创建版本表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at REAL DEFAULT (unixepoch())
        )
    """)

    current = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
    print(f"[migrate] 当前 schema: v{current}")

    if current >= CURRENT_VERSION:
        print("[migrate] schema 已是最新")
        conn.close()
        return True, "up_to_date"

    # 备份
    backup_path = Path(f"{db_path}.bak.v{current}")
    if not backup_path.exists():
        print(f"[migrate] 备份至 {backup_path}")
        try:
            if sqlite_ver >= (3, 27, 0):
                conn.execute("VACUUM INTO ?", (str(backup_path),))
            else:
                conn.close()
                shutil.copy2(db_path, backup_path)
                conn = sqlite3.connect(str(db_path))
        except Exception as e:
            print(f"[migrate] 备份失败: {e}，尝试 shutil.copy")
            conn.close()
            try:
                shutil.copy2(db_path, backup_path)
            except Exception as e2:
                print(f"[migrate] 备份完全失败: {e2}")
            conn = sqlite3.connect(str(db_path))

    # 执行迁移
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    for mf in migration_files:
        version = int(mf.name.split("_")[0])
        if version <= current:
            continue

        print(f"[migrate] 应用 {mf.name}...")
        sql = mf.read_text(encoding="utf-8")

        # SQLite <3.35.0 兼容
        if sqlite_ver < (3, 35, 0):
            sql = _patch_legacy_sql(sql)

        try:
            # 逐条执行 SQL，以便跳过已存在的列
            for stmt in sql.split(";"):
                stmt = stmt.strip()
                if not stmt:
                    continue
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as e:
                    # 如果列已存在，跳过
                    if (
                        "duplicate column" in str(e).lower()
                        or "already exists" in str(e).lower()
                    ):
                        continue
                    raise
            conn.execute(
                "INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (version,)
            )
            conn.commit()
            print(f"[migrate] 完成，schema 版本 {version}")
        except Exception as e:
            conn.rollback()
            conn.close()
            print(f"[migrate] 失败：{e}，进入降级模式")
            print(f"[migrate] 恢复命令：cp {backup_path} {db_path}")
            return False, f"failed: {e}"

    conn.close()
    print(f"[migrate] schema 已升级至 v{CURRENT_VERSION}")
    return True, "success"


def main():
    import sys

    skip = "--skip-migrate" in sys.argv
    db_path = os.getenv("MING_DB_PATH", str(Path.home() / ".ming" / "ming.db"))
    success, msg = run_migrations(db_path, skip)
    if not success:
        print(f"[migrate] 迁移失败: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
