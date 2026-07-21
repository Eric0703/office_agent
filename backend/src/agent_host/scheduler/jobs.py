"""调度器(FR-07):一次性定时提醒的最小到点触发。

每分钟(scan interval 30s)扫描到期 timer 卡,向在线设备广播 reminder.push;
触发记录仅保存在内存——进程重启后,过期未撤下的提醒会补触发一次(已在 08 §2 注明)。
简报定时生成(FR-06)与周期性重复提醒均不在本轮范围(Owner 决策,2026-07-21)。
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from agent_host.store.repos import CardRepo

SCAN_INTERVAL_S = 30.0

# 与 ConnectionManager.broadcast 同型;注入协议以便测试用假广播器
Broadcast = Callable[[str, dict[str, Any]], Awaitable[None]]


def card_payload(row: Any) -> dict[str, Any]:
    """timer 卡 → reminder.push 的 card 结构(登记册 §2.4,与 state.sync 同形)。"""
    return {
        "card_id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "body": row["body"],
        "remind_at": row["remind_at"],
        "ref_task_id": row["ref_task_id"],
    }


async def fire_due(
    cards: CardRepo,
    broadcast: Broadcast,
    fired: set[str],
    *,
    now: datetime | None = None,
) -> int:
    """扫描并广播到期提醒,返回本次新触发条数;fired 内存去重(测试可直接调用)。"""
    now_iso = (now or datetime.now().astimezone()).isoformat()
    count = 0
    for row in cards.list_due_active(now_iso):
        if row["id"] in fired:
            continue
        fired.add(row["id"])
        await broadcast("reminder.push", {"card": card_payload(row)})
        count += 1
    return count


async def reminder_loop(
    cards: CardRepo,
    broadcast: Broadcast,
    *,
    interval_s: float = SCAN_INTERVAL_S,
) -> None:
    """最小触发循环:周期扫描到期 timer 卡并广播;由 api 装配层在应用生命周期内启动。"""
    fired: set[str] = set()
    while True:
        await fire_due(cards, broadcast, fired)
        await asyncio.sleep(interval_s)
