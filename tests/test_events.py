from dataclasses import asdict

from nare.events import Event, redact


def test_event_fields_match_conductors_backend_event() -> None:
    e = Event("progress", "working")
    assert set(asdict(e)) == {"timestamp", "type", "text", "detail"}


def test_timestamp_is_iso_utc() -> None:
    assert Event("progress", "x").timestamp.endswith("+00:00")


def test_anthropic_keys_are_redacted_in_text() -> None:
    key = "sk-ant-api03-" + "A" * 40
    assert key not in Event("progress", f"exported {key} to env").text
    assert "[redacted]" in Event("progress", f"exported {key}").text


def test_github_tokens_are_redacted() -> None:
    assert "[redacted]" in redact("ghp_" + "b" * 36)


def test_assignments_are_redacted() -> None:
    assert redact("API_KEY=hunter2") == "[redacted]"
    assert redact("token: abc123def") == "[redacted]"


def test_ordinary_text_survives() -> None:
    assert redact("read src/nare/loop.py") == "read src/nare/loop.py"


def test_detail_is_redacted_recursively() -> None:
    key = "sk-ant-api03-" + "C" * 40
    e = Event("tool_use", "bash", {"command": f"echo {key}", "nested": {"k": [key]}})
    assert key not in str(e.detail)


def test_non_string_detail_values_pass_through() -> None:
    e = Event("cost", "tokens", {"input": 10, "output": 2, "ok": True})
    assert e.detail == {"input": 10, "output": 2, "ok": True}
