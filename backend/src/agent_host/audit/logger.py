"""审计日志骨架(FR-11;08 §3 audit_log 表)。"""

from dataclasses import dataclass

from agent_host.store.repos import AuditRepo


@dataclass(frozen=True)
class AuditEvent:
    """一条审计事件;decision ∈ executed/confirmed/cancelled/timeout/failed。"""

    device_id: str
    decision: str
    record_id: str | None = None
    intent: str | None = None
    risk_level: str | None = None  # L0 / L1 / L2
    tool: str | None = None
    params_json: str | None = None  # 转写文本只存截断/哈希(规约 §8)
    result: str | None = None
    extra_json: str | None = None


class AuditLogger:
    """append-only:只有 log/export,不提供任何改写入口。"""

    def __init__(self, repo: AuditRepo) -> None:
        self._repo = repo

    def log(self, event: AuditEvent) -> None:
        """追加一条审计记录(ts 由库层生成)。"""
        self._repo.append(
            device_id=event.device_id,
            decision=event.decision,
            record_id=event.record_id,
            intent=event.intent,
            risk_level=event.risk_level,
            tool=event.tool,
            params_json=event.params_json,
            result=event.result,
            extra_json=event.extra_json,
        )

    def export(self, start: str, end: str) -> list[dict[str, object]]:
        """按时间范围导出(只读,供 CLI 日志导出)。"""
        raise NotImplementedError
