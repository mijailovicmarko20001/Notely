def test_state_endpoint_returns_configured_lectures(client):
    resp = client.get("/api/state")
    assert resp.status_code == 200
    body = resp.json()
    ids = [lec["id"] for lec in body["lectures"]]
    assert ids == ["lecture01", "lecture02"]
    assert body["busy"] is False
