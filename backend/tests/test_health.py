"""FR-12:HTTP 端点骨架 —— /health 可用性。"""

from fastapi.testclient import TestClient

from agent_host.main import app


def test_fr12_health_ok() -> None:
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
