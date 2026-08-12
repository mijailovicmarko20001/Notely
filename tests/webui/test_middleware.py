"""Sanity checks for the S2 origin/host protections -- not explicitly in
T1's checklist, but cheap to pin down and easy to silently break while
touching main.py for anything else."""


def test_same_origin_state_changing_request_is_allowed(client):
    resp = client.put(
        "/api/settings",
        json={"OCR_LANG": "eng"},
        headers={"origin": "http://localhost"},
    )
    assert resp.status_code == 200


def test_cross_origin_state_changing_request_is_rejected(client):
    resp = client.put(
        "/api/settings",
        json={"OCR_LANG": "eng"},
        headers={"origin": "http://evil.example.com"},
    )
    assert resp.status_code == 403


def test_cross_origin_get_request_is_not_blocked(client):
    # origin_check only guards state-changing verbs
    resp = client.get("/api/state", headers={"origin": "http://evil.example.com"})
    assert resp.status_code == 200


def test_untrusted_host_header_is_rejected(client):
    resp = client.get("/api/state", headers={"host": "evil.example.com"})
    assert resp.status_code == 400
