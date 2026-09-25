import random
import re
import time
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


def test_detail_containers_other_than_lists_are_redacted() -> None:
    key = "sk-ant-api03-" + "D" * 40
    e = Event("tool_use", "bash", {"argv": (key,), "flags": {key}})
    assert key not in str(e.detail)
    assert e.detail["argv"] == ["[redacted]"]
    assert e.detail["flags"] == ["[redacted]"]


def test_the_assignment_pattern_prefers_over_redaction_to_leaking() -> None:
    # Deliberate, not a bug: "auth:" in ordinary prose loses the word after
    # it. A mangled clause of narration is a cheaper failure than a leaked
    # credential, and every surface downstream trusts these events.
    assert redact("covers auth: see section 3") == "covers [redacted] section 3"


def test_affixed_credential_names_are_redacted() -> None:
    # The real-world forms. `_` is a word character, so a \b-anchored keyword
    # matched none of these.
    assert redact("ANTHROPIC_AUTH_TOKEN=abcdefghijk") == "[redacted]"
    assert redact("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI") == "[redacted]"
    assert redact("GITHUB_TOKEN: ghs_short") == "[redacted]"


def test_bearer_schemes_are_redacted_past_the_scheme_word() -> None:
    # The secret is the SECOND word after the colon here.
    assert "abc123xyz789secretvalue" not in redact(
        "Authorization: Bearer abc123xyz789secretvalue"
    )
    assert "hunter2" not in redact('"Authorization": "Bearer hunter2"')


# The rule set as it shipped before #31, verbatim. It is the oracle: the
# in-module redaction must reproduce it byte-for-byte, or callers piping text
# through `nare redact` would see different output than events carry.
_LEGACY = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(
        r"(?i)[\w.\-]*(?:api[_-]?key|auth|token|secret|password|passwd|credential)"
        r"[\w.\-]*[\"']?\s*[=:]\s*[\"']?(?:bearer|basic|token)?\s*\S+"
    ),
]


def _legacy_redact(text: str) -> str:
    for pattern in _LEGACY:
        text = pattern.sub("[redacted]", text)
    return text


def test_the_linear_rules_match_the_legacy_rules_exactly() -> None:
    # The shapes that make the legacy key/value rule's backtracking subtle:
    # multi-keyword runs, affixed names, scheme words, quoted values.
    cases = [
        "Authorization: Bearer sk-abc",
        "tokentoken=x",
        "tokentokentoken = x",
        "token secret = x",
        "secret: token = x",
        "password=x:y",
        "token=basic=creds",
        "Credentials: basic user pass",
        "==token==x==",
        "a token b c d = e",
        "no_keyword_here = x",
        "_token_=_value_",
        ".token.=.value.",
        "token==value==here",
        "MY.AUTH-token:someval",
        "É_token = ü_value",
    ]
    for case in cases:
        assert redact(case) == _legacy_redact(case), case


def test_the_linear_rules_stay_equivalent_on_random_text() -> None:
    # Seeded, so a failure reproduces. The alphabet is the backtracking
    # grammar's own alphabet: keywords, separators, quotes, word runs.
    rng = random.Random(31)
    alphabet = (
        "token seCret auth password passwd credential api_key apiKey"
        " x=1 :y\"'_-.=:,bearer1\n\tTOKen{}[]|"
    )
    for _ in range(3000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 120)))
        assert redact(text) == _legacy_redact(text), repr(text)


def test_one_mib_of_repeated_keywords_is_redacted_well_under_a_second() -> None:
    # The legacy key/value rule's open [\w.\-]* prefix backtracks quadratically
    # on a long run of word characters: 2 KiB already took seconds, and a
    # megabyte was out of reach. The acceptance line is "well under a second".
    text = "token" * (2**20 // 5)
    assert redact(text) == text

    with_value = text + "=x"
    start = time.perf_counter()
    no_match = redact(text)
    match = redact(with_value)
    elapsed = time.perf_counter() - start

    assert no_match == text
    assert match == "[redacted]"
    assert elapsed < 1.0
