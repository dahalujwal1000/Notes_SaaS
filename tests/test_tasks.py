"""Kanban task API tests — shares helpers/fixtures with test_api via conftest."""

from conftest import auth_headers, signup, unique_email


def test_tasks_require_auth(client):
    assert client.get("/tasks").status_code == 401
    assert client.post("/tasks", json={"title": "t"}).status_code == 401
    assert client.patch("/tasks/1", json={"title": "t"}).status_code == 401
    assert client.delete("/tasks/1").status_code == 401


def test_create_task_defaults_to_todo_column(client):
    headers = auth_headers(client)
    resp = client.post("/tasks", json={"title": "Write docs"}, headers=headers)
    assert resp.status_code == 201, resp.text
    task = resp.json()
    assert task["status"] == "todo"
    assert task["position"] == 1024  # appended to an empty column
    assert task["user_id"] == 1


def test_create_task_in_specific_status_and_list_ordering(client):
    headers = auth_headers(client)
    for title in ("first", "second", "third"):
        client.post(
            "/tasks", json={"title": title, "status": "doing"}, headers=headers
        )
    tasks = client.get("/tasks?status=doing", headers=headers).json()
    assert [t["title"] for t in tasks] == ["first", "second", "third"]
    assert tasks[0]["position"] < tasks[1]["position"] < tasks[2]["position"]


def test_move_task_between_columns(client):
    headers = auth_headers(client)
    task = client.post("/tasks", json={"title": "Review PR"}, headers=headers).json()
    moved = client.patch(
        f"/tasks/{task['id']}",
        json={"status": "review", "position": 512},
        headers=headers,
    )
    assert moved.status_code == 200
    assert moved.json()["status"] == "review"
    listing = client.get("/tasks", headers=headers).json()
    assert len(listing) == 1 and listing[0]["status"] == "review"


def test_rename_task_partial(client):
    headers = auth_headers(client)
    task = client.post("/tasks", json={"title": "Old"}, headers=headers).json()
    resp = client.patch(f"/tasks/{task['id']}", json={"title": "New"}, headers=headers)
    assert resp.json()["title"] == "New"
    assert resp.json()["status"] == "todo"  # untouched field survives


def test_reject_invalid_status(client):
    headers = auth_headers(client)
    resp = client.post(
        "/tasks", json={"title": "t", "status": "archived"}, headers=headers
    )
    assert resp.status_code == 422


def test_user_cannot_touch_other_users_tasks(client):
    owner_headers = auth_headers(client)
    intruder_headers = auth_headers(client)
    task = client.post("/tasks", json={"title": "Secret"}, headers=owner_headers).json()
    assert client.get(f"/tasks/{task['id']}", headers=intruder_headers).status_code in (404, 405)
    moved = client.patch(
        f"/tasks/{task['id']}", json={"status": "done"}, headers=intruder_headers
    )
    assert moved.status_code == 404
    assert client.delete(f"/tasks/{task['id']}", headers=intruder_headers).status_code == 404
    assert client.get("/tasks", headers=intruder_headers).json() == []


def test_delete_task_returns_204_then_404(client):
    headers = auth_headers(client)
    task = client.post("/tasks", json={"title": "Doomed"}, headers=headers).json()
    assert client.delete(f"/tasks/{task['id']}", headers=headers).status_code == 204
    assert (
        client.patch(f"/tasks/{task['id']}", json={"title": "x"}, headers=headers).status_code
        == 404
    )
