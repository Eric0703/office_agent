"""数据库初始化:执行 backend/schema.sql 建表(08 §3,唯一建表入口)。"""

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schema.sql"


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
    conn.commit()
    return conn
