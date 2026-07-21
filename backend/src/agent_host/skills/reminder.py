"""提醒调度:卡片生命周期、到期触发、"说完即消"(FR-07;生命周期见 08 §1.3)。

卡片仅 task/timer 两类(宪法第 4 条);语音完成任务 ≤5s 撤下。
"""

from agent_host.adapters.task import TaskAdapter


class ReminderSkill:
    """sync_cards() / dismiss(task_id)(08 §2)。"""

    def __init__(self, tasks: TaskAdapter) -> None:
        self._tasks = tasks

    def sync_cards(self, device_id: str) -> None:
        """向设备全量/增量同步 active 卡片(state.sync 的卡片来源)。"""
        raise NotImplementedError

    def dismiss(self, task_id: str, reason: str = "completed") -> None:
        """按任务撤下对应卡片;reason ∈ completed/cancelled/expired(登记册 §2.4)。"""
        raise NotImplementedError
