"""A0-6 冒烟脚本:模拟设备(headless-test)走通核心闭环。

链路:hello → state.sync → 上传 task_command 音频 → intent.result success
      → "周报撰写" done + 卡片 reminder.dismiss("说完即消")
      → 上传 field 音频 → drafts 表新增 pending + 音频 tmp 已删(宪法第 3 条)

前置:`agent-host mock import` 已执行且 `agent-host serve` 已启动。
用法:backend/.venv/bin/python scripts/smoke_device.py [--url http://127.0.0.1:8000]
"""

import argparse
import asyncio
import json
import sqlite3
import sys
import time
import uuid
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "agent.db"
AUDIO_DIR = ROOT / "testdata" / "l1_synthetic" / "audio" / "clean"
AUDIO_TMP = ROOT / "data" / "audio_tmp"

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def envelope(msg_type: str, payload: dict) -> str:
    return json.dumps(
        {
            "type": msg_type,
            "version": "1.0",
            "id": uuid.uuid4().hex,
            "ts": int(time.time() * 1000),
            "payload": payload,
        },
        ensure_ascii=False,
    )


async def wait_for(
    ws: websockets.ClientConnection, msg_type: str, timeout: float = 120
) -> dict | None:
    """读到指定类型的消息并返回;其余消息跳过(ack 等);超时返回 None。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(1.0, deadline - time.monotonic()))
        except TimeoutError:
            return None
        msg = json.loads(raw)
        if msg.get("type") == msg_type:
            return msg
    return None


def db_row(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    ws_url = args.url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"

    async with websockets.connect(ws_url) as ws:
        # 1. hello → hello.result ok → state.sync 含"周报撰写截止"卡片
        await ws.send(
            envelope(
                "device.hello",
                {
                    "device_id": "smoke-1",
                    "token": "dev",
                    "client": "headless-test",
                    "client_version": "0.1.0",
                    "display_profile": "400x300",
                },
            )
        )
        hello = await wait_for(ws, "device.hello.result", timeout=10)
        check("hello.result ok", hello is not None
              and hello["payload"].get("status") == "ok", str(hello))
        check("hello.result 回显 device_id(登记册 §2.1)",
              hello is not None and hello["payload"].get("device_id") == "smoke-1",
              str(hello))
        if hello is None:
            return 1
        sync = await wait_for(ws, "state.sync", timeout=10)
        card_titles = [c.get("title") for c in sync["payload"].get("cards", [])] if sync else []
        check("state.sync 含周报卡片", any("周报撰写" in (t or "") for t in card_titles),
              str(card_titles))

        # 2. task_command:把周报撰写标记为已完成 → success + 任务 done + 卡片撤下
        rid1 = uuid.uuid4().hex
        await ws.send(
            envelope(
                "record.start",
                {"record_id": rid1, "mode": "auto", "started_at": int(time.time() * 1000)},
            )
        )
        audio1 = (AUDIO_DIR / "TASK-001.wav").read_bytes()
        resp = httpx.post(
            f"{args.url}/audio/{rid1}",
            content=audio1,
            headers={
                "X-Device-Id": "smoke-1",
                "X-Token": "dev",
                "X-Audio-Format": "wav",
                "X-Duration-Ms": "2470",
            },
            timeout=30,
        )
        check("audio 受理", resp.status_code == 200 and resp.json().get("status") == "received",
              f"{resp.status_code} {resp.text}")
        await ws.send(envelope("record.stop", {"record_id": rid1, "duration_ms": 2470}))

        result1 = await wait_for(ws, "intent.result", timeout=180)
        p1 = result1["payload"] if result1 else {}
        if p1.get("status") == "clarify":
            # ASR 同音误转写(撰写→转写)时,Gate 0 语义:先澄清,不猜测执行;
            # 端侧选「周报撰写」后流程回到执行,仍应成功
            cand = next(
                (c for c in p1.get("candidates", []) if c.get("label") == "周报撰写"), None
            )
            check("clarify 候选含「周报撰写」(Gate 0 澄清语义)",
                  cand is not None, json.dumps(p1, ensure_ascii=False))
            if cand is None:
                return 1
            await ws.send(
                envelope("clarify.select",
                         {"record_id": rid1, "candidate_id": cand["candidate_id"]})
            )
            result1 = await wait_for(ws, "intent.result", timeout=60)
            p1 = result1["payload"] if result1 else {}
        check(
            "intent.result success(完成周报撰写)",
            p1.get("record_id") == rid1 and p1.get("status") == "success",
            json.dumps(p1, ensure_ascii=False),
        )
        dismiss = await wait_for(ws, "reminder.dismiss", timeout=30)
        pd = dismiss["payload"] if dismiss else {}
        check(
            "reminder.dismiss(说完即消)",
            pd.get("card_id") == "card-weekly-report" and pd.get("reason") == "completed",
            json.dumps(pd, ensure_ascii=False),
        )
        task = db_row("SELECT * FROM tasks WHERE id = 'task-weekly-report'")
        check("任务已 done(voice)",
              task is not None and task["status"] == "done" and task["completed_via"] == "voice")
        card = db_row("SELECT * FROM cards WHERE id = 'card-weekly-report'")
        check("卡片已 dismissed/completed",
              card is not None and card["status"] == "dismissed"
              and card["dismiss_reason"] == "completed")

        # 3. field:现场记录 → drafts 新增 pending + 音频即删
        rid2 = uuid.uuid4().hex
        await ws.send(
            envelope(
                "record.start",
                {"record_id": rid2, "mode": "auto", "started_at": int(time.time() * 1000)},
            )
        )
        audio2 = (AUDIO_DIR / "FIELD-001.wav").read_bytes()
        resp2 = httpx.post(
            f"{args.url}/audio/{rid2}",
            content=audio2,
            headers={
                "X-Device-Id": "smoke-1",
                "X-Token": "dev",
                "X-Audio-Format": "wav",
                "X-Duration-Ms": "6290",
            },
            timeout=30,
        )
        check("field audio 受理", resp2.status_code == 200
              and resp2.json().get("status") == "received", f"{resp2.status_code}")
        result2 = await wait_for(ws, "intent.result", timeout=180)
        p2 = result2["payload"] if result2 else {}
        check(
            "intent.result success(笔记草稿)",
            p2.get("record_id") == rid2 and p2.get("status") == "success",
            json.dumps(p2, ensure_ascii=False),
        )
        draft = db_row("SELECT * FROM drafts WHERE record_id = ?", (rid2,))
        check("drafts 新增 pending 草稿",
              draft is not None and draft["kind"] == "note" and draft["status"] == "pending")
        leftover = list(AUDIO_TMP.glob(f"{rid2}*")) if AUDIO_TMP.is_dir() else []
        check("音频 tmp 已删(宪法第 3 条)", not leftover, str(leftover))
        rec = db_row("SELECT * FROM records WHERE id = ?", (rid2,))
        check("records.audio_tmp_path 置 NULL",
              rec is not None and rec["audio_tmp_path"] is None and rec["status"] == "done")

    print()
    if FAILURES:
        print(f"冒烟失败 {len(FAILURES)} 项:{', '.join(FAILURES)}")
        return 1
    print("冒烟通过:hello / 指令闭环 / 笔记草稿 / 音频即删 全部 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
