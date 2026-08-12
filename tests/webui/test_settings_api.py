"""S4: `.env` line injection through PUT /api/settings. `write_settings`
rejects control characters/newlines and over-long values before they ever
reach `set_key`, which would otherwise let a value like
"medium\\nANTHROPIC_API_KEY=attacker" inject an extra line stage_env() then
feeds into every stage subprocess's environment."""


def test_put_settings_rejects_newline_injection(client, project_root):
    resp = client.put("/api/settings", json={
        "WHISPER_MODEL": "medium\nANTHROPIC_API_KEY=attacker",
    })
    assert resp.status_code == 400
    body = resp.json()
    assert "error" in body and "detail" in body
    # the malicious line must never have reached .env
    env_path = project_root / ".env"
    if env_path.exists():
        assert "ANTHROPIC_API_KEY=attacker" not in env_path.read_text()


def test_put_settings_rejects_carriage_return(client):
    resp = client.put("/api/settings", json={"OCR_LANG": "eng\rBAD=1"})
    assert resp.status_code == 400


def test_put_settings_rejects_other_control_characters(client):
    resp = client.put("/api/settings", json={"NOTES_MODEL": "claude\x00null"})
    assert resp.status_code == 400


def test_put_settings_rejects_overlong_value(client):
    resp = client.put("/api/settings", json={"OCR_LANG": "x" * 4001})
    assert resp.status_code == 400


def test_put_settings_accepts_value_at_length_cap(client):
    resp = client.put("/api/settings", json={"OCR_LANG": "x" * 4000})
    assert resp.status_code == 200


def test_put_settings_valid_update_round_trips(client):
    resp = client.put("/api/settings", json={"WHISPER_MODEL": "large-v3"})
    assert resp.status_code == 200
    assert resp.json()["settings"]["WHISPER_MODEL"] == "large-v3"

    resp2 = client.get("/api/settings")
    assert resp2.json()["settings"]["WHISPER_MODEL"] == "large-v3"


def test_put_settings_ignores_unknown_keys(client):
    resp = client.put("/api/settings", json={"NOT_A_REAL_SETTING": "value"})
    assert resp.status_code == 200
    assert "NOT_A_REAL_SETTING" not in resp.json()["settings"]


def test_put_settings_masked_api_key_roundtrip_is_a_noop(client):
    # the UI echoes back the masked "sk-ab…" placeholder on unrelated saves;
    # write_settings must not persist that literal masked value as the key
    resp = client.put("/api/settings", json={"ANTHROPIC_API_KEY": "sk-abcdefghij…"})
    assert resp.status_code == 200
    assert resp.json()["settings"]["has_api_key"] is False
