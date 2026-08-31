"""Characterization test for GET /api/jobs/current/events (previously zero
HTTP-level coverage -- test_jobs_scheduler.py exercises JobManager.events_since
directly, never the SSE route wrapping it).

Calls the route function directly with a fake Request rather than going
through TestClient's streaming machinery: the endpoint's generator only
exits on client disconnect or a 15s heartbeat interval, and TestClient's
ASGI transport doesn't deliver a disconnect signal promptly enough to keep
this test fast and non-hanging. A fake Request whose is_disconnected()
flips True after N checks gives full control over when the generator ends,
while still exercising the real route code."""

import json

import pytest

from webui import jobs
from webui.routes.jobs import job_events


class _FakeRequest:
    def __init__(self, headers=None, disconnect_after=1):
        self.headers = headers or {}
        self._checks = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self):
        self._checks += 1
        return self._checks > self._disconnect_after


async def _collect_chunks(request):
    response = await job_events(request)
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    return response, chunks


@pytest.mark.anyio
async def test_stream_emits_seeded_event_as_sse_data():
    evt = jobs.MANAGER._emit("stage_start", stage=1, lecture_id="lecture01")

    response, chunks = await _collect_chunks(_FakeRequest())

    assert response.media_type == "text/event-stream"
    body = "".join(chunks)
    lines = body.split("\n")
    assert lines[0] == f"id: {evt['seq']}"
    assert lines[1].startswith("data: ")
    payload = json.loads(lines[1][len("data: ") :])
    assert payload["type"] == "stage_start"
    assert payload["stage"] == 1
    assert payload["lecture_id"] == "lecture01"


@pytest.mark.anyio
async def test_stream_resumes_from_last_event_id_header():
    e1 = jobs.MANAGER._emit("stage_start", stage=1, lecture_id="lecture01")
    e2 = jobs.MANAGER._emit("stage_end", stage=1, lecture_id="lecture01")

    _, chunks = await _collect_chunks(_FakeRequest(headers={"last-event-id": str(e1["seq"])}))

    body = "".join(chunks)
    # only the event *after* seq=e1 is replayed, not e1 itself
    assert body.startswith(f"id: {e2['seq']}")
    assert f"id: {e1['seq']}" not in body


@pytest.mark.anyio
async def test_stream_emits_nothing_when_no_events_are_pending():
    _, chunks = await _collect_chunks(_FakeRequest(disconnect_after=1))
    assert chunks == []


@pytest.mark.anyio
async def test_malformed_last_event_id_header_falls_back_to_zero():
    evt = jobs.MANAGER._emit("stage_start", stage=1, lecture_id="lecture01")

    _, chunks = await _collect_chunks(_FakeRequest(headers={"last-event-id": "not-a-number"}))

    body = "".join(chunks)
    assert body.startswith(f"id: {evt['seq']}")
