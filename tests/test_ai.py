"""AI assistant tests — run entirely against the offline mock provider
(conftest forces AI_PROVIDER=mock), so nothing here touches the network.

Covers: status flag, read tools, write tools, ownership isolation, the
delete proposal → confirm/cancel flow, the audit trail, rate limiting,
and auth requirements.
"""

import json

import pytest

import ai_config
import routers.ai as ai_router
from conftest import auth_headers, unique_email


def _chat(client, headers, message):
    return client.post("/ai/chat", json={"message": message}, headers=headers)


def _seed_task(client, headers, title="Seed task", status="todo"):
    resp = client.post("/tasks", json={"title": title, "status": status}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_status_reports_mock(client):
    resp = client.get("/ai/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["provider"] == "mock"


def test_chat_requires_auth(client):
    resp = client.post("/ai/chat", json={"message": "hi"})
    assert resp.status_code == 401


def test_chat_lists_tasks(client):
    headers = auth_headers(client)
    _seed_task(client, headers, "Write the docs")
    resp = _chat(client, headers, "show my tasks")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "Write the docs" in body["reply"]
    assert any(e["tool"] == "list_tasks" and e["ok"] for e in body["tool_events"])


def test_chat_creates_and_moves_task(client):
    headers = auth_headers(client)
    resp = _chat(client, headers, "create task Ship landing page in doing")
    assert resp.status_code == 200, resp.text

    tasks = client.get("/tasks", headers=headers).json()
    assert any(t["title"] == "Ship landing page" and t["status"] == "doing" for t in tasks)

    resp = _chat(client, headers, "move task Ship landing page to done")
    assert resp.status_code == 200, resp.text
    tasks = client.get("/tasks", headers=headers).json()
    target = next(t for t in tasks if t["title"] == "Ship landing page")
    assert target["status"] == "done"


def test_chat_searches_notes(client):
    headers = auth_headers(client)
    resp = client.post(
        "/notes", json={"title": "Groceries", "content": "milk, eggs, bread"}, headers=headers
    )
    assert resp.status_code == 201
    resp = _chat(client, headers, "find notes about groceries")
    assert resp.status_code == 200
    assert "Groceries" in resp.json()["reply"]


def test_chat_isolated_per_user(client):
    user_a = auth_headers(client)
    user_b = auth_headers(client)
    _seed_task(client, user_a, "Alice private task")

    resp = _chat(client, user_b, "show my tasks")
    assert resp.status_code == 200
    assert "Alice private task" not in resp.json()["reply"]


def test_delete_requires_confirmation_then_executes(client):
    headers = auth_headers(client)
    task = _seed_task(client, headers, "Delete me")

    # Proposal only — the task must still exist afterwards.
    resp = _chat(client, headers, f"delete task {task['title']}")
    assert resp.status_code == 200
    body = resp.json()
    proposals = [a for a in body["actions"] if a["status"] == "proposed"]
    assert len(proposals) == 1
    action_id = proposals[0]["action_id"]
    assert "Delete me" in body["reply"]

    tasks = client.get("/tasks", headers=headers).json()
    assert any(t["id"] == task["id"] for t in tasks), "task deleted without confirmation!"

    # Confirm via the UI endpoint.
    resp = client.post(f"/ai/actions/{action_id}/confirm", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True

    tasks = client.get("/tasks", headers=headers).json()
    assert not any(t["id"] == task["id"] for t in tasks)

    # Confirming twice fails cleanly.
    resp = client.post(f"/ai/actions/{action_id}/confirm", headers=headers)
    assert resp.status_code == 400


def test_delete_cancel_keeps_task(client):
    headers = auth_headers(client)
    task = _seed_task(client, headers, "Keep me")

    resp = _chat(client, headers, f"delete task {task['title']}")
    action_id = next(
        a["action_id"] for a in resp.json()["actions"] if a["status"] == "proposed"
    )
    resp = client.post(f"/ai/actions/{action_id}/cancel", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    tasks = client.get("/tasks", headers=headers).json()
    assert any(t["id"] == task["id"] for t in tasks)

    # A cancelled proposal can no longer be confirmed.
    resp = client.post(f"/ai/actions/{action_id}/confirm", headers=headers)
    assert resp.status_code == 400


def test_confirm_is_scoped_to_owner(client):
    owner = auth_headers(client)
    attacker = auth_headers(client)
    task = _seed_task(client, owner, "Secret task")

    resp = _chat(client, owner, f"delete task {task['title']}")
    action_id = next(
        a["action_id"] for a in resp.json()["actions"] if a["status"] == "proposed"
    )

    # Another user can neither see nor confirm someone else's action.
    resp = client.post(f"/ai/actions/{action_id}/confirm", headers=attacker)
    assert resp.status_code == 404

    tasks = client.get("/tasks", headers=owner).json()
    assert any(t["id"] == task["id"] for t in tasks)


def test_delete_via_chat_confirmation_flow(client):
    """The full conversational path: propose → 'confirm action N' → deleted."""
    headers = auth_headers(client)
    task = _seed_task(client, headers, "Old stuff")

    resp = _chat(client, headers, f"delete task {task['title']}")
    action_id = next(
        a["action_id"] for a in resp.json()["actions"] if a["status"] == "proposed"
    )
    resp = _chat(client, headers, f"confirm action {action_id}")
    assert resp.status_code == 200
    assert "Deleted task 'Old stuff'" in resp.json()["reply"]

    tasks = client.get("/tasks", headers=headers).json()
    assert not any(t["id"] == task["id"] for t in tasks)


def test_unknown_task_title_returns_error(client):
    headers = auth_headers(client)
    resp = _chat(client, headers, "move task Does Not Exist to done")
    assert resp.status_code == 200
    body = resp.json()
    assert any(
        not e["ok"] and "not found" in (e["error"] or "").lower() for e in body["tool_events"]
    )


def test_audit_trail_records_mutations(client):
    headers = auth_headers(client)
    _chat(client, headers, "create task Audited task")
    _seed_task(client, headers, "Audit delete")
    resp = _chat(client, headers, "delete task Audit delete")
    action_id = next(
        a["action_id"] for a in resp.json()["actions"] if a["status"] == "proposed"
    )
    client.post(f"/ai/actions/{action_id}/confirm", headers=headers)

    from database import SessionLocal
    import models

    db = SessionLocal()
    try:
        rows = db.query(models.AIAction).filter(models.AIAction.tool == "create_task").all()
        assert rows and rows[-1].status == "direct"
        rows = db.query(models.AIAction).filter(models.AIAction.tool == "delete_task").all()
        assert rows and rows[-1].status == "executed"
        assert json.loads(rows[-1].result)["ok"] is True
    finally:
        db.close()


def test_rate_limit(monkeypatch, client):
    monkeypatch.setattr(ai_config, "AI_RATE_LIMIT_PER_HOUR", 2)
    ai_router._chat_windows.clear()
    headers = auth_headers(client)
    for _ in range(2):
        assert _chat(client, headers, "show my tasks").status_code == 200
    resp = _chat(client, headers, "show my tasks")
    assert resp.status_code == 429


def test_disabled_returns_403(monkeypatch, client):
    monkeypatch.setattr(ai_config, "AI_ENABLED", False)
    headers = auth_headers(client)
    resp = _chat(client, headers, "show my tasks")
    assert resp.status_code == 403
