"""S4: `.env` line injection through PUT /api/settings. `write_settings`
rejects control characters/newlines and over-long values before they ever
reach `set_key`, which would otherwise let a value like
"medium\\nANTHROPIC_API_KEY=attacker" inject an extra line stage_env() then
feeds into every stage subprocess's environment."""


def test_put_settings_rejects_newline_injection(client, project_root):
    resp = client.put(
        "/api/settings",
        json={
            "WHISPER_MODEL": "medium\nANTHROPIC_API_KEY=attacker",
        },
    )
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


# --- cloud transcription settings (Feature C) --------------------------------


def test_put_settings_accepts_whisper_backend_and_it_round_trips(client):
    resp = client.put("/api/settings", json={"WHISPER_BACKEND": "groq"})
    assert resp.status_code == 200
    assert resp.json()["settings"]["WHISPER_BACKEND"] == "groq"

    resp2 = client.get("/api/settings")
    assert resp2.json()["settings"]["WHISPER_BACKEND"] == "groq"


def test_whisper_backend_defaults_to_faster_whisper(client):
    resp = client.get("/api/settings")
    assert resp.json()["settings"]["WHISPER_BACKEND"] == "faster-whisper"


def test_put_settings_saves_and_masks_groq_api_key(client):
    resp = client.put("/api/settings", json={"GROQ_API_KEY": "gsk_abcdefghijklmnop"})
    assert resp.status_code == 200
    body = resp.json()["settings"]
    assert body["GROQ_API_KEY"].endswith("…")
    assert body["GROQ_API_KEY"] != "gsk_abcdefghijklmnop"  # never echoed raw
    assert body["has_groq_key"] is True


def test_put_settings_saves_and_masks_openai_api_key(client):
    resp = client.put("/api/settings", json={"OPENAI_API_KEY": "sk-proj-abcdefghijklmnop"})
    assert resp.status_code == 200
    body = resp.json()["settings"]
    assert body["OPENAI_API_KEY"].endswith("…")
    assert body["has_openai_key"] is True


def test_put_settings_masked_groq_key_roundtrip_is_a_noop(client):
    resp = client.put("/api/settings", json={"GROQ_API_KEY": "gsk_abcdefgh…"})
    assert resp.status_code == 200
    assert resp.json()["settings"]["has_groq_key"] is False


def test_has_groq_and_openai_key_false_when_unset(client):
    resp = client.get("/api/settings")
    body = resp.json()["settings"]
    assert body["has_groq_key"] is False
    assert body["has_openai_key"] is False


def test_put_settings_accepts_groq_and_openai_model_overrides(client):
    resp = client.put(
        "/api/settings",
        json={"GROQ_WHISPER_MODEL": "whisper-large-v3", "OPENAI_TRANSCRIBE_MODEL": "whisper-1"},
    )
    assert resp.status_code == 200
    body = resp.json()["settings"]
    assert body["GROQ_WHISPER_MODEL"] == "whisper-large-v3"
    assert body["OPENAI_TRANSCRIBE_MODEL"] == "whisper-1"


def test_put_settings_rejects_newline_injection_via_groq_key(client, project_root):
    # same S4 guard as ANTHROPIC_API_KEY (test_put_settings_rejects_newline_
    # injection above), now generalized across every secret key -- a real
    # regression risk once masking/control-char checks stopped being
    # hardcoded to one key name.
    resp = client.put("/api/settings", json={"GROQ_API_KEY": "gsk_x\nOPENAI_API_KEY=attacker"})
    assert resp.status_code == 400
    env_path = project_root / ".env"
    if env_path.exists():
        assert "OPENAI_API_KEY=attacker" not in env_path.read_text()
