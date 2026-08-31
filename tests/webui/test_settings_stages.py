"""GET /api/settings's `stages` field (Phase 7 of the cleanup plan): the
Run tab's stage checkboxes render from this instead of a hardcoded list in
index.html, which had drifted from the backend's own stage names (e.g.
"Match frames" vs notely.stages' "Match frames to slides")."""

from notely.stages import MAX_PIPELINE_STAGE


def test_settings_includes_stages_from_the_registry(client):
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    stages = resp.json()["stages"]

    assert [s["number"] for s in stages] == list(range(MAX_PIPELINE_STAGE + 1))
    by_number = {s["number"]: s["name"] for s in stages}
    assert by_number[4] == "Match frames to slides"
    assert by_number[7] == "Assemble study guide"


def test_settings_stages_excludes_export_stage(client):
    # Stage 8 (PDF export) isn't part of a pipeline run -- the Run tab has
    # no checkbox for it.
    resp = client.get("/api/settings")
    numbers = [s["number"] for s in resp.json()["stages"]]
    assert 8 not in numbers
