import json
import random
import time

import pytest

from nare.events import Event, LiveText, redact


def test_an_event_has_exactly_the_four_contract_fields() -> None:
    e = Event("progress", "working")
    assert set(e.wire()) == {"timestamp", "type", "text", "detail"}


def test_timestamp_is_iso_utc() -> None:
    assert Event("progress", "x").timestamp.endswith("+00:00")


def test_anthropic_keys_are_redacted_in_text() -> None:
    key = "sk-ant-api03-" + "A" * 40
    assert key not in Event("progress", f"exported {key} to env").text
    assert "[redacted]" in Event("progress", f"exported {key}").text


def test_github_tokens_are_redacted() -> None:
    assert "[redacted]" in redact("ghp_" + "b" * 36)


def test_assignments_are_redacted() -> None:
    assert redact("API_KEY=hunter2hunter2") == "[redacted]"
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


def test_a_keyword_in_prose_does_not_eat_the_sentence() -> None:
    # Before contract 3, "auth:" in prose redacted the word after it. The
    # value must now be secret-shaped, so ordinary narration survives.
    assert redact("covers auth: see section 3") == "covers auth: see section 3"


def test_affixed_credential_names_are_redacted() -> None:
    # The real-world forms. `_` is a word character, so a \b-anchored keyword
    # matched none of these.
    assert redact("ANTHROPIC_AUTH_TOKEN=abc123defghi") == "[redacted]"
    assert redact("AWS_SECRET_ACCESS_KEY=wJalrXUtnF3MI/K7") == "[redacted]"
    assert redact("GITHUB_TOKEN: Ab3_dE5fG7hJ") == "[redacted]"


def test_bearer_schemes_are_redacted_past_the_scheme_word() -> None:
    # The secret is the SECOND word after the colon here.
    assert "abc123xyz789secretvalue" not in redact(
        "Authorization: Bearer abc123xyz789secretvalue"
    )
    assert "hunter2" not in redact('"Authorization": "Bearer hunter2"')


# Code and config that looked like a secret to the old rule. Each line must
# come through byte-for-byte. The first group is the practice run's shapes
# (the keyword `auth` inside `author`, and `token`/`password` followed by a
# call or a member access); the rest are the same family.
FALSE_POSITIVES = [
    "authorId: number,",
    'token: text("token").primaryKey(),',
    'password: formData.get("password"),',
    'author: "Jason Berry"',
    "const tokenizer = new Tokenizer(config)",
    "authority = resolveAuthority(request)",
    "token = getAuthToken()",
    'password: "Enter your password 1 times"',
    "secret_name: db_secret_v2",
    "max_tokens: 4096",
    "password_field = input.value",
    "token_type = Bearer",
    "covers auth: see section 3",
    # A URL, a path, a number, a member access, a type name, and a header that
    # is only prose on the next line. None of them is a credential value.
    'tokenUrl: "https://v2.api.example.com/oauth"',
    '"tokenUrl": "https://auth2.example.com/token"',
    "auth: Auth0Client,",
    "const auth: Auth0Config2 = x;",
    "tokenFile: /var/run/secrets/token123abc",
    "POSTGRES_PASSWORD_FILE=/run/secrets/db_pass2",
    "token = self.token_value1",
    '{"token": 12345678e9}',
    "Authorization: Bearer\nNext line of prose here",
    # A kebab-case name, a shell default, and prose after a scheme word.
    "secretName: tls-cert-prod1",
    "PASSWORD=${DB_PASSWORD:-default1} ./run.sh",
    "auth: basic auth enabled",
    "auth: token expired, retry",
    # The run 11 shapes: a reference the runner fills in, never a secret.
    '"DATABASE_PASSWORD":"${DB_PASSWORD}"',
    "-e POSTGRES_PASSWORD=${PG_PASSWORD}",
    "PGPASSWORD=${DB_PASSWORD} psql -h 127.0.0.1",
    '"name":"DB_PASSWORD","service":"postgres","purpose":"database login"',
]


@pytest.mark.parametrize("line", FALSE_POSITIVES)
def test_code_that_looks_like_a_secret_is_left_alone(line: str) -> None:
    assert redact(line) == line


# Real credentials in the shapes that matter. Each must lose its value.
TRUE_POSITIVES = [
    ('password = "hunter2hunter2"', 'password = "[redacted]"'),
    ('{"password": "p@ss-w0rd!"}', '{"password": "[redacted]"}'),
    ("password: 'hunter2hunter2'", "password: '[redacted]'"),
    ("AUTH_TOKEN=Xk92mQ7vLp", "[redacted]"),
    ("API_KEY=hunter2hunter2", "[redacted]"),
    ("PGPASSWORD=hunter2hunter2", "[redacted]"),
    ("ANTHROPIC_AUTH_TOKEN=abc123defghi", "[redacted]"),
    ("AWS_SECRET_ACCESS_KEY=wJalrXUtnF3MI/K7", "[redacted]"),
    ("token: abc123def", "[redacted]"),
    ('authToken: "xY7kP2mQ9"', 'authToken: "[redacted]"'),
    ('"Authorization": "Bearer hunter2"', '"Authorization": "[redacted]"'),
    ("Authorization: Bearer abc123xyz789secretvalue", "[redacted]"),
    ("Authorization: Bearer privatevalue", "[redacted]"),
    # Symbols are part of a secret, so a bare value runs past them.
    ("password=P@ssw0rd123", "[redacted]"),
    ("DB_PASSWORD=hunter22!@#", "[redacted]"),
    ("password=Xk92mQ7vLp.9", "[redacted]"),
    # The `=` padding of base64 is not part of the value.
    ("token=abc123defghi==", "[redacted]=="),
    # A JWT is a dotted run, but it is a credential, not member access.
    (
        "token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2Q",
        "[redacted]",
    ),
    # An escaped quote inside a JSON string does not end the value early.
    ('{"password": "Xk92mQ7v\\"cd"}', '{"password": "[redacted]"}'),
]


@pytest.mark.parametrize(("line", "expected"), TRUE_POSITIVES)
def test_real_credentials_are_redacted_in_the_shapes_that_matter(
    line: str, expected: str
) -> None:
    assert redact(line) == expected


def test_a_short_bare_value_is_not_a_secret_shape() -> None:
    # Eight characters, letters and digits: the shape test's floor. A seven
    # character bare value is a deliberate miss, and the quoted form is the
    # way to be sure.
    assert redact("API_KEY=hunter2") == "API_KEY=hunter2"


def test_known_key_prefixes_are_redacted_wherever_they_appear() -> None:
    key = "sk-ant-api03-" + "A" * 40
    assert redact(f"see {key} here") == "see [redacted] here"
    assert "[redacted]" in redact("ghp_" + "b" * 36)


def test_a_structured_answer_of_references_still_parses_after_redaction() -> None:
    # The structured-answer shape from the nare#68 comment: a JSON document
    # whose only credential-ish text is references the runner fills in. The
    # old rule redacted eight of these and the document stopped parsing.
    answer = {
        "steps": [
            {
                "command": "PGPASSWORD=${DB_PASSWORD} psql -h 127.0.0.1 -c 'select 1'",
                "docker": "docker run -e POSTGRES_PASSWORD=${PG_PASSWORD} postgres",
            }
        ],
        "env": {"DATABASE_PASSWORD": "${DB_PASSWORD}", "AUTH_TOKEN": "${API_TOKEN}"},
        "credentials": [
            {
                "name": "DB_PASSWORD",
                "service": "postgres",
                "purpose": "database login",
            }
        ],
    }
    text = json.dumps(answer, indent=2)
    assert redact(text) == text
    assert json.loads(redact(text)) == answer


def test_a_secret_in_a_json_document_leaves_the_document_parseable() -> None:
    # A real value is redacted, and the quotes and the key around it stay, so
    # the document still parses. The match cannot run past a closing quote.
    text = (
        '{"env": {"DB_PASSWORD": "${DB_PASSWORD}", '
        '"API_PASSWORD": "hunter2hunter2", "NOTE": "ok"}}'
    )
    out = redact(text)
    assert "hunter2hunter2" not in out
    assert json.loads(out) == {
        "env": {
            "DB_PASSWORD": "${DB_PASSWORD}",
            "API_PASSWORD": "[redacted]",
            "NOTE": "ok",
        }
    }


@pytest.mark.parametrize(
    "text",
    [
        "x" * 40 + "=" + "y" * 40,
        "token" * 1000 + "=abc123defghi",
        "password: " * 500,
        "AUTH_TOKEN=" * 500,
        "token=token=token=" * 500,
    ],
)
def test_the_rules_stay_linear_on_pathological_input(text: str) -> None:
    start = time.perf_counter()
    redact(text)
    assert time.perf_counter() - start < 1.0


def test_one_mib_of_a_credential_name_is_redacted_well_under_a_second() -> None:
    # A megabyte of one credential name is a single long run. Its name is
    # checked word by word, so the cost is linear, not quadratic.
    text = "AUTH_TOKEN_" * (2**20 // 11)
    assert redact(text) == text

    start = time.perf_counter()
    match = redact(text + "=abc123defghi")
    elapsed = time.perf_counter() - start

    assert match == "[redacted]"
    assert elapsed < 1.0


def test_streamed_text_redacts_exactly_like_whole_text() -> None:
    # Live output is redacted in pieces, held at the boundary of a value. The
    # pieces must join to exactly what the whole text redacts to, or a secret
    # split across two deltas would leak or be mangled.
    rng = random.Random(7)
    alphabet = [
        "password",
        "token",
        "auth",
        "=",
        ": ",
        " ",
        '"',
        "'",
        "hunter2hunter2",
        "abc123defghi",
        "${DB}",
        "x",
        "\n",
        "Bearer ",
        "sk-ant-api03-",
        "A" * 20,
        "author",
    ]
    for _ in range(500):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 30)))
        live = LiveText()
        pieces: list[str] = []
        pos = 0
        while pos < len(text):
            step = rng.randint(1, 7)
            pieces.append(live.feed(text[pos : pos + step]))
            pos += step
        pieces.append(live.finish())
        assert "".join(pieces) == redact(text), repr(text)


def test_a_value_split_across_an_equals_sign_streams_like_whole_text() -> None:
    # The `=` ends a bare value, so a cut between a value and the name after it
    # must not change the decision. This is the split the review found.
    chunks = ["auth", "=c2d", "3", "e4a", "=aut", "h TO", "KEN "]
    live = LiveText()
    streamed = "".join(live.feed(chunk) for chunk in chunks) + live.finish()
    assert streamed == redact("".join(chunks))


def test_streaming_does_not_hold_back_a_run_of_credential_names() -> None:
    # Each `token:` names the next word, which cannot be a value. Only the word
    # still arriving may be held, so the output keeps flowing.
    live = LiveText()
    emitted = "".join(live.feed("token: ") for _ in range(200))
    assert len(emitted) > 1000
    assert len(live.pending) < 64


def test_streamed_text_matches_whole_text_across_many_chunkings() -> None:
    alphabet = [
        "token",
        "=",
        ":",
        "\n",
        " ",
        "abc12345",
        "Bearer",
        '"',
        "x9",
        "AAAAAAAA",
    ]
    for seed in range(4):
        rng = random.Random(1000 + seed)
        for _ in range(300):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 25)))
            live = LiveText()
            pieces: list[str] = []
            pos = 0
            while pos < len(text):
                step = rng.randint(1, 9)
                pieces.append(live.feed(text[pos : pos + step]))
                pos += step
            pieces.append(live.finish())
            assert "".join(pieces) == redact(text), repr(text)


def test_a_run_of_spaces_after_a_credential_name_streams_promptly() -> None:
    # The separator allows a few spaces, so a name whose value cannot follow is
    # released at once. Holding it would rescan the whole stream on every delta.
    live = LiveText()
    start = time.perf_counter()
    for _ in range(2**20 // 64):
        live.feed("token:" + " " * 59)
    elapsed = time.perf_counter() - start
    assert len(live.pending) < 64
    assert elapsed < 5.0
