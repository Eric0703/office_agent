"""数据库初始化:执行 backend/schema.sql 建表(08 §3,唯一建表入口)。"""

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schema.sql"

# 与 schema.sql 的 records 定义保持一致(仅 _upgrade_records_if_needed 使用)
_RECORDS_CREATE = """
CREATE TABLE records (
  id              TEXT PRIMARY KEY,
  device_id       TEXT REFERENCES devices(id),
  source          TEXT NOT NULL DEFAULT 'device_audio',
  mode            TEXT NOT NULL CHECK (mode IN ('auto','field','experience')),
  started_at      TEXT NOT NULL,
  duration_ms     INTEGER NOT NULL,
  audio_tmp_path  TEXT,
  status          TEXT NOT NULL CHECK (status IN
                    ('uploaded','transcribed','routed','done','failed')),
  transcript      TEXT,
  confidence      REAL,
  intent          TEXT,
  created_at      TEXT NOT NULL
)
"""


def _upgrade_records_if_needed(conn: sqlite3.Connection) -> None:
    """records 旧结构幂等升级(source 缺失或 device_id 仍 NOT NULL 时重建并复制数据)。

    仅针对 2026-07 来源中立化一次结构变更;旧记录 source 默认 device_audio。
    新库/已升级库零动作;不引入迁移框架(drafts 引用 records,重建时临时关外键)。
    """
    cols = conn.execute("PRAGMA table_info(records)").fetchall()
    if not cols:
        return
    has_source = any(c[1] == "source" for c in cols)
    device_not_null = any(c[1] == "device_id" and c[3] for c in cols)  # c[3]=notnull
    if has_source and not device_not_null:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    # legacy_alter_table=ON:重命名 records 时不改写 drafts 等引用方的外键目标
    # (默认 OFF 会把 REFERENCES records 改成 records_old,迁移完悬挂)
    conn.execute("PRAGMA legacy_alter_table = ON")
    conn.executescript(
        f"""
        ALTER TABLE records RENAME TO records_old;
        {_RECORDS_CREATE};
        INSERT INTO records
          (id, device_id, source, mode, started_at, duration_ms, audio_tmp_path,
           status, transcript, confidence, intent, created_at)
        SELECT id, device_id, 'device_audio', mode, started_at, duration_ms, audio_tmp_path,
               status, transcript, confidence, intent, created_at
        FROM records_old;
        DROP TABLE records_old;
        """
    )
    conn.execute("PRAGMA legacy_alter_table = OFF")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()


def init_db(db_path: str | Path, schema_path: str | Path | None = None) -> sqlite3.Connection:
    """按 schema.sql 建表(幂等,全部 IF NOT EXISTS)并返回连接(Row 工厂,外键开)。"""
    schema = Path(schema_path) if schema_path is not None else SCHEMA_PATH
    db_path = Path(db_path)
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    # check_same_thread=False:连接由单事件循环串行使用,但 TestClient/uvicorn 会跨线程
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # 每连接都要显式开(标准库默认关)
    conn.executescript(schema.read_text(encoding="utf-8"))
    _upgrade_records_if_needed(conn)
    conn.commit()
    return conn
