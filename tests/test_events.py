"""Event API tests — shares helpers/fixtures with the other modules via conftest."""

from conftest import auth_headers


def test_events_require_auth(client):
    assert client.get("/events").status_code == 401
    assert client.post(
        "/events", json={"title": "x", "event_date": "2026-09-01"}
    ).status_code == 401


def test_create_and_list_events_ordered_by_date(client):
    headers = auth_headers(client)
    assert client.post(
        "/events", json={"title": "Later", "event_date": "2026-12-01"}, headers=headers
    ).status_code == 201
    assert client.post(
        "/events",
        json={"title": "Sooner", "event_date": "2026-09-05", "description": "kickoff"},
        headers=headers,
    ).status_code == 201

    events = client.get("/events", headers=headers).json()
    assert [e["title"] for e in events] == ["Sooner", "Later"]  # soonest first
    assert events[0]["description"] == "kickoff"
    assert events[0]["event_date"] == "2026-09-05"
    assert events[0]["user_id"] == 1


def test_update_event_partial(client):
    headers = auth_headers(client)
    event = client.post(
        "/events", json={"title": "Demo", "event_date": "2026-10-10"}, headers=headers
    ).json()
    resp = client.patch(
        f"/events/{event['id']}", json={"event_date": "2026-10-12"}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["event_date"] == "2026-10-12"
    assert resp.json()["title"] == "Demo"  # untouched field survives


def test_invalid_date_rejected(client):
    headers = auth_headers(client)
    resp = client.post(
        "/events", json={"title": "Bad", "event_date": "not-a-date"}, headers=headers
    )
    assert resp.status_code == 422


def test_user_cannot_touch_other_users_events(client):
    owner_headers = auth_headers(client)
    intruder_headers = auth_headers(client)
    event = client.post(
        "/events", json={"title": "Secret", "event_date": "2026-11-01"}, headers=owner_headers
    ).json()
    moved = client.patch(
        f"/events/{event['id']}", json={"title": "hacked"}, headers=intruder_headers
    )
    assert moved.status_code == 404
    assert client.delete(f"/events/{event['id']}", headers=intruder_headers).status_code == 404
    assert client.get("/events", headers=intruder_headers).json() == []


def test_delete_event_returns_204_then_404(client):
    headers = auth_headers(client)
    event = client.post(
        "/events", json={"title": "Doomed", "event_date": "2026-11-11"}, headers=headers
    ).json()
    assert client.delete(f"/events/{event['id']}", headers=headers).status_code == 204
    assert (
        client.patch(
            f"/events/{event['id']}", json={"title": "x"}, headers=headers
        ).status_code
        == 404
    )
