"""安全检查骨架(FR-09)。"""

from enum import StrEnum

from agent_host.router.router import Intent


class RiskLevel(StrEnum):
    """风险分级:L0 只读 / L1 可逆写 / L2 不可逆或影响他人(08 §1.2;宪法第 5 条)。"""

    L0 = "L0"
    L1 = "L1"
    L2 = "L2"


class SecurityGuard:
    """白名单校验 + 风险分级 + L2 确认回路。"""

    def __init__(self, whitelist_commands: list[str]) -> None:
        self._whitelist = frozenset(whitelist_commands)

    def check(self, intent: Intent) -> RiskLevel:
        """白名单外指令一律拒绝;按指令映射 L0/L1/L2。"""
        raise NotImplementedError

    async def request_confirm(
        self, confirm_id: str, record_id: str, title: str, timeout_s: int = 15
    ) -> bool:
        """下发 confirm.request 并等待 confirm.response;超时按 cancel 处理(登记册 §2.3)。"""
        raise NotImplementedError
