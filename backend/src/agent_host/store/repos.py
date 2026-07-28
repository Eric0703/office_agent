"""各表 repo(08 §3)。本任务卡实现核心闭环所需方法,其余保持骨架(签名 + docstring)。

约束:audit_log append-only,应用层不提供 UPDATE/DELETE(宪法第 5 条);
转写文本只存截断/哈希(规约 §8);时间一律 ISO8601 文本(UTC)。
"""

import sqlite3
import uuid
from datetime import UTC, datetime


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


class DeviceRepo:
    """devices 表:配对设备(FR-01;吊销即 revoked_at 非空,token 立即失效)。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, device_id: str) -> sqlite3.Row | None:
        """按 id 取设备。"""
        return self._conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()

    def create(self, device_id: str, name: str, token_hash: str, paired_at: str) -> None:
        """登记新配对设备;token 只存哈希(规约 §8)。已存在则忽略(幂等)。"""
        self._conn.execute(
            "INSERT OR IGNORE INTO devices (id, name, token_hash, paired_at, last_seen_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (device_id, name, token_hash, paired_at, paired_at),
        )
        self._conn.commit()

    def revoke(self, device_id: str, revoked_at: str) -> None:
        """写入 revoked_at,吊销立即生效。"""
        self._conn.execute(
            "UPDATE devices SET revoked_at = ? WHERE id = ?", (revoked_at, device_id)
        )
        self._conn.commit()

    def touch_last_seen(self, device_id: str, seen_at: str) -> None:
        """更新最近在线时间。"""
        self._conn.execute(
            "UPDATE devices SET last_seen_at = ? WHERE id = ?", (seen_at, device_id)
        )
        self._conn.commit()


class RecordRepo:
    """records 表:一次录音的管线状态(FR-02;id 即 record_id,幂等键)。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, record_id: str) -> sqlite3.Row | None:
        """按 record_id 取记录,幂等判断入口(08 §1.2)。"""
        return self._conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()

    def create(
        self,
        record_id: str,
        device_id: str | None,
        mode: str,
        started_at: str,
        duration_ms: int,
        audio_tmp_path: str | None = None,
        source: str = "device_audio",
    ) -> None:
        """音频受理后落记录,初始 status='uploaded';device_id 可空(来源为 PC 文字等无设备入口)。"""
        self._conn.execute(
            "INSERT INTO records"
            " (id, device_id, source, mode, started_at, duration_ms, audio_tmp_path, status,"
            " created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'uploaded', ?)",
            (record_id, device_id, source, mode, started_at, duration_ms, audio_tmp_path,
             _utc_now()),
        )
        self._conn.commit()

    def update_status(self, record_id: str, status: str) -> None:
        """推进管线状态(uploaded/transcribed/routed/done/failed)。"""
        self._conn.execute("UPDATE records SET status = ? WHERE id = ?", (status, record_id))
        self._conn.commit()

    def list_recent(self, limit: int = 20) -> list[sqlite3.Row]:
        """最近处理记录(创建时间倒序),PC 草稿工作台只读查询。"""
        return self._conn.execute(
            "SELECT * FROM records ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()

    def set_transcript(self, record_id: str, transcript: str, confidence: float) -> None:
        """写入转写文本与置信度;transcript 本地留存(宪法第 3 条)。"""
        self._conn.execute(
            "UPDATE records SET transcript = ?, confidence = ?, status = 'transcribed'"
            " WHERE id = ?",
            (transcript, confidence, record_id),
        )
        self._conn.commit()

    def set_intent(self, record_id: str, intent: str) -> None:
        """记录路由出的意图(kind/指令名)。"""
        self._conn.execute("UPDATE records SET intent = ? WHERE id = ?", (intent, record_id))
        self._conn.commit()

    def clear_audio_path(self, record_id: str) -> None:
        """音频删除后将 audio_tmp_path 置 NULL(宪法第 3 条)。"""
        self._conn.execute(
            "UPDATE records SET audio_tmp_path = NULL WHERE id = ?", (record_id,)
        )
        self._conn.commit()


class TaskRepo:
    """tasks 表:任务本地映射(source/source_id 对接外部系统)。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, task_id: str) -> sqlite3.Row | None:
        """按 id 取任务。"""
        return self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()

    def list_open(self) -> list[sqlite3.Row]:
        """全部 open 任务。"""
        return self._conn.execute(
            "SELECT * FROM tasks WHERE status = 'open' ORDER BY created_at"
        ).fetchall()

    def list_all(self, limit: int = 50) -> list[sqlite3.Row]:
        """全部任务(创建时间倒序),PC 工作台只读查询。"""
        return self._conn.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()

    def insert(
        self,
        title: str,
        due_at: str | None = None,
        source: str = "mock",
        task_id: str | None = None,
    ) -> str:
        """新建任务,返回 id;mock import 用固定 task_id 幂等(INSERT OR REPLACE)。"""
        tid = task_id or _new_id()
        now = _utc_now()
        self._conn.execute(
            "INSERT OR REPLACE INTO tasks"
            " (id, source, title, status, due_at, created_at, updated_at)"
            " VALUES (?, ?, ?, 'open', ?, ?, ?)",
            (tid, source, title, due_at, now, now),
        )
        self._conn.commit()
        return tid

    def mark_done(self, task_id: str, completed_via: str) -> None:
        """标记完成;completed_via ∈ voice/pc(语音完成 ≤5s 撤卡,08 §1.3)。"""
        self._conn.execute(
            "UPDATE tasks SET status = 'done', completed_via = ?, updated_at = ? WHERE id = ?",
            (completed_via, _utc_now(), task_id),
        )
        self._conn.commit()


class CalendarEventRepo:
    """calendar_events 表:日历事件(简报数据源,FR-06)。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def list_by_date(self, date: str) -> list[sqlite3.Row]:
        """按 YYYY-MM-DD 取当日事件。"""
        return self._conn.execute(
            "SELECT * FROM calendar_events WHERE start_at LIKE ? ORDER BY start_at",
            (f"{date}%",),
        ).fetchall()

    def insert(
        self,
        event_id: str,
        title: str,
        start_at: str,
        end_at: str,
        source: str = "mock",
        location: str | None = None,
    ) -> None:
        """按固定 id 幂等写入(INSERT OR REPLACE,供 mock import)。"""
        self._conn.execute(
            "INSERT OR REPLACE INTO calendar_events (id, source, title, start_at, end_at, location)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, source, title, start_at, end_at, location),
        )
        self._conn.commit()


class CardRepo:
    """cards 表:提醒卡片,仅 task/timer 两类(宪法第 4 条;生命周期见 08 §1.3)。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, card_id: str) -> sqlite3.Row | None:
        """按 id 取卡片。"""
        return self._conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()

    def list_active(self) -> list[sqlite3.Row]:
        """全部 active 卡片,state.sync 全量下发用。"""
        return self._conn.execute(
            "SELECT * FROM cards WHERE status = 'active' ORDER BY created_at"
        ).fetchall()

    def find_active_by_task(self, task_id: str) -> sqlite3.Row | None:
        """按关联任务取 active 卡片("说完即消"链路)。"""
        return self._conn.execute(
            "SELECT * FROM cards WHERE ref_task_id = ? AND status = 'active'", (task_id,)
        ).fetchone()

    def list_due_active(self, now_iso: str) -> list[sqlite3.Row]:
        """到点未撤下的 timer 卡(remind_at <= now),调度器到期扫描用。"""
        return self._conn.execute(
            "SELECT * FROM cards WHERE status = 'active' AND kind = 'timer'"
            " AND remind_at IS NOT NULL AND remind_at <= ? ORDER BY remind_at",
            (now_iso,),
        ).fetchall()

    def list_by_kind(self, kind: str, limit: int = 50) -> list[sqlite3.Row]:
        """按类型取卡片(创建时间倒序),PC 工作台只读查询。"""
        return self._conn.execute(
            "SELECT * FROM cards WHERE kind = ? ORDER BY created_at DESC LIMIT ?",
            (kind, limit),
        ).fetchall()

    def upsert(
        self,
        card_id: str,
        kind: str,
        title: str,
        body: str | None = None,
        remind_at: str | None = None,
        ref_task_id: str | None = None,
    ) -> None:
        """创建或更新卡片(按 id 幂等;重置为 active)。"""
        now = _utc_now()
        self._conn.execute(
            "INSERT OR REPLACE INTO cards"
            " (id, kind, ref_task_id, title, body, status, remind_at, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)",
            (card_id, kind, ref_task_id, title, body, remind_at, now, now),
        )
        self._conn.commit()

    def dismiss(self, card_id: str, reason: str) -> None:
        """撤下卡片;reason ∈ completed/cancelled/expired。"""
        self._conn.execute(
            "UPDATE cards SET status = 'dismissed', dismiss_reason = ?, updated_at = ?"
            " WHERE id = ?",
            (reason, _utc_now(), card_id),
        )
        self._conn.commit()


class BriefingRepo:
    """briefings 表:每日简报,条目含来源 task/event id 可追溯(FR-06)。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_by_date(self, date: str) -> sqlite3.Row | None:
        """按 YYYY-MM-DD 取简报。"""
        return self._conn.execute(
            "SELECT * FROM briefings WHERE date = ?", (date,)
        ).fetchone()

    def save(self, date: str, content_json: str, generated_at: str) -> int:
        """保存简报(date UNIQUE,重复生成覆盖),返回 briefing id。"""
        self._conn.execute(
            "INSERT INTO briefings (date, content_json, generated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(date) DO UPDATE SET"
            "   content_json = excluded.content_json, generated_at = excluded.generated_at",
            (date, content_json, generated_at),
        )
        self._conn.commit()
        row = self.get_by_date(date)
        return int(row["id"])

    def mark_pushed(self, date: str, pushed_at: str) -> None:
        """记录推送时间。"""
        ...


class DraftRepo:
    """drafts 表:待人工确认的草稿(宪法第 8 条:确认前一律是草稿)。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, draft_id: str) -> sqlite3.Row | None:
        """按 id 取草稿。"""
        return self._conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()

    def create(self, record_id: str | None, kind: str, content_md: str) -> str:
        """新建草稿,初始 status='pending',返回 draft id。"""
        draft_id = _new_id()
        self._conn.execute(
            "INSERT INTO drafts (id, record_id, kind, content_md, status, created_at)"
            " VALUES (?, ?, ?, ?, 'pending', ?)",
            (draft_id, record_id, kind, content_md, _utc_now()),
        )
        self._conn.commit()
        return draft_id

    def list_pending(self) -> list[sqlite3.Row]:
        """待确认队列。"""
        return self._conn.execute(
            "SELECT * FROM drafts WHERE status = 'pending' ORDER BY created_at"
        ).fetchall()

    def set_status(self, draft_id: str, status: str, file_path: str | None = None) -> None:
        """确认(confirmed,记录归档路径)或放弃(discarded)。"""
        ...


class ExperienceRepo:
    """experience_index 表:经验卡片索引,供检索引用(FR-10)。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, entry_id: str) -> sqlite3.Row | None:
        """按 id 取索引。"""
        ...

    def create(
        self, entry_id: str, title: str, domain: str, file_path: str, source: str
    ) -> None:
        """登记索引,初始 status='draft'。"""
        ...

    def list_active(self) -> list[sqlite3.Row]:
        """全部 active 经验卡片。"""
        ...


class AuditRepo:
    """audit_log 表:append-only(宪法第 5 条),不提供 UPDATE/DELETE。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def append(
        self,
        device_id: str,
        decision: str,
        record_id: str | None = None,
        intent: str | None = None,
        risk_level: str | None = None,
        tool: str | None = None,
        params_json: str | None = None,
        result: str | None = None,
        extra_json: str | None = None,
    ) -> None:
        """追加一条审计;params_json 中转写文本只存截断/哈希(规约 §8)。"""
        self._conn.execute(
            "INSERT INTO audit_log"
            " (ts, device_id, record_id, intent, risk_level, tool, params_json, decision,"
            "  result, extra_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _utc_now(),
                device_id,
                record_id,
                intent,
                risk_level,
                tool,
                params_json,
                decision,
                result,
                extra_json,
            ),
        )
        self._conn.commit()

    def list_range(self, start: str, end: str) -> list[sqlite3.Row]:
        """按时间范围导出(只读)。"""
        ...
