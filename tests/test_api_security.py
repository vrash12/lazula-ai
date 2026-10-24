"""Authentication, replay protection and tenant isolation (SECURITY.md §4.1, §4.4)."""

import time

import pytest

from tests.conftest import CAPTION_INPUT, Hub

pytestmark = pytest.mark.security


async def test_unsigned_request_is_rejected(hub: Hub) -> None:
    response = await hub.client.post("/v1/tasks", json={"task": "sermon.captions"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_signature"


async def test_wrong_secret_is_rejected(hub: Hub) -> None:
    response = await hub.run_task("sermon.captions", CAPTION_INPUT, secret="not-the-secret")
    assert response.status_code == 401


async def test_expired_timestamp_is_rejected(hub: Hub) -> None:
    response = await hub.run_task(
        "sermon.captions", CAPTION_INPUT, timestamp=int(time.time()) - 3600
    )
    assert response.status_code == 401


async def test_auth_runs_before_body_validation(hub: Hub) -> None:
    response = await hub.client.post(
        "/v1/tasks", json={"nonsense": True}, headers={"X-Lazula-App": "sermonpost"}
    )
    assert response.status_code == 401  # not 422: unsigned callers learn nothing about inputs


async def test_same_request_id_and_body_returns_cached_result_without_rerunning(hub: Hub) -> None:
    first = await hub.run_task("sermon.captions", CAPTION_INPUT, request_id="rq_retry_0001")
    second = await hub.run_task("sermon.captions", CAPTION_INPUT, request_id="rq_retry_0001")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(hub.fake.calls) == 1  # billed once


async def test_same_request_id_with_different_body_is_a_replay(hub: Hub) -> None:
    await hub.run_task("sermon.captions", CAPTION_INPUT, request_id="rq_replay_0001")
    other = {"context": "A different idea entirely, about the youth retreat next month."}
    response = await hub.run_task("sermon.captions", other, request_id="rq_replay_0001")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "replayed_request"


async def test_app_cannot_use_another_apps_tenant(hub: Hub) -> None:
    response = await hub.run_task(
        "sermon.captions", CAPTION_INPUT, app="mediateam", tenant="church_1"
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "tenant_not_found"


async def test_app_cannot_read_another_apps_task(hub: Hub) -> None:
    created = await hub.run_task("sermon.captions", CAPTION_INPUT)
    task_id = created.json()["id"]
    own = await hub.call("GET", f"/v1/tasks/{task_id}")
    other = await hub.call("GET", f"/v1/tasks/{task_id}", app="mediateam")
    assert own.status_code == 200
    assert other.status_code == 404


async def test_task_not_in_allow_list_is_forbidden(hub: Hub) -> None:
    response = await hub.run_task("listing.describe", {"facts": {}})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "task_not_allowed"


async def test_tenant_registration_is_scoped_to_the_calling_app(hub: Hub) -> None:
    created = await hub.call("PUT", "/v1/tenants/church_2", {"name": "Grace Church"})
    assert created.status_code == 200
    ok = await hub.run_task("sermon.captions", CAPTION_INPUT, tenant="church_2")
    blocked = await hub.run_task(
        "sermon.captions", CAPTION_INPUT, app="mediateam", tenant="church_2"
    )
    assert ok.status_code == 200
    assert blocked.status_code == 404


async def test_suspended_tenant_cannot_run_tasks(hub: Hub) -> None:
    await hub.call("PUT", "/v1/tenants/church_1", {"name": "Church", "status": "suspended"})
    response = await hub.run_task("sermon.captions", CAPTION_INPUT)
    assert response.status_code == 403
