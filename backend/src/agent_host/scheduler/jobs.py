"""调度器骨架(FR-06/07):简报定时生成、提醒到期扫描。"""


class Scheduler:
    """cron/interval 调度;实现选型后续任务卡定(选型表外依赖需批准,规约 §4)。"""

    def __init__(self, briefing_time: str = "08:30") -> None:
        self._briefing_time = briefing_time

    async def start(self) -> None:
        """启动调度循环:到点生成简报、周期扫描到期提醒。"""
        raise NotImplementedError

    async def stop(self) -> None:
        """停止调度循环。"""
        raise NotImplementedError
