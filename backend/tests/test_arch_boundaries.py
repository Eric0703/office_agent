"""架构边界守卫(软硬件解耦最小校准):Web 框架依赖只许停留在 api/ 与 gateway/transport.py。

- 核心层(router/skills/security/audit/store/adapters/scheduler/core)零 Web 框架依赖;
- gateway 内仅 transport.py 允许 import fastapi/starlette(WebSocket 包装与断连异常转换);
- 失败信息指出具体 文件:行号,便于定位违规引入点。
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "agent_host"
CORE_DIRS = ("router", "skills", "security", "audit", "store", "adapters", "scheduler", "core")
WEB_ROOTS = ("fastapi", "starlette", "websockets")


def _web_import_hits(path: Path) -> list[str]:
    """返回文件内 fastapi/starlette/websockets 的 import 命中(文件:行号 描述)。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in WEB_ROOTS:
                    hits.append(f"{path}:{node.lineno} import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[0] in WEB_ROOTS:
                names = ", ".join(alias.name for alias in node.names)
                hits.append(f"{path}:{node.lineno} from {module} import {names}")
    return hits


def test_core_layer_has_no_web_framework_imports() -> None:
    """核心层全部 .py 禁止 import fastapi/starlette/websockets。"""
    offenders: list[str] = []
    for dirname in CORE_DIRS:
        for path in sorted((SRC / dirname).rglob("*.py")):
            offenders.extend(_web_import_hits(path))
    assert not offenders, "核心层禁止 Web 框架依赖,违规:\n" + "\n".join(offenders)


def test_gateway_only_transport_may_import_web_framework() -> None:
    """gateway 内仅 transport.py 允许 Web 框架 import(manager.py 等一律禁止)。"""
    offenders: list[str] = []
    for path in sorted((SRC / "gateway").rglob("*.py")):
        if path.name == "transport.py":
            continue
        offenders.extend(_web_import_hits(path))
    assert not offenders, "gateway 仅 transport.py 可依赖 Web 框架,违规:\n" + "\n".join(offenders)


def test_transport_is_the_sole_fastapi_websocket_wrapper() -> None:
    """transport.py 必须存在且确为 fastapi WebSocket 的包装点(防误删后守卫空转)。"""
    path = SRC / "gateway" / "transport.py"
    hits = _web_import_hits(path)
    assert any("WebSocket" in hit for hit in hits), (
        f"{path} 应包装 fastapi 的 WebSocket,当前 Web 框架 import: {hits or '无'}"
    )
