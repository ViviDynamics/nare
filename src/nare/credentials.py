"""Provider keys stored once: `nare auth login|status|logout`.

The file is NARE_HOME/credentials.json, mode 0600 in a 0700 folder:

    {"anthropic": {"api_key": "..."}, "openai": {"api_key": "..."}}

An environment variable always wins over it, and nothing here ever prompts
without a terminal. A stored key reaches the provider as the transport's
api_key, so it is never in argv, in nare's environment, or in what bash sees,
and nare writes it nowhere else.
"""

from __future__ import annotations

import getpass
import json
import os
import sys
import tempfile
from pathlib import Path

from nare.session import nare_home, private_folder

# The providers a key can be stored for, and the variable that overrides it.
KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


def credentials_path() -> Path | None:
    home = nare_home()
    return None if home is None else home / "credentials.json"


def _load() -> dict[str, dict[str, str]]:
    path = credentials_path()
    if path is None or not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path} is not readable JSON: {exc}") from exc
    if not isinstance(loaded, dict) or not all(
        isinstance(entry, dict) for entry in loaded.values()
    ):
        raise ValueError(f"{path} must hold an object of provider entries")
    return loaded


def _save(entries: dict[str, dict[str, str]]) -> Path:
    path = credentials_path()
    if path is None:
        raise ValueError("no home directory to keep a key in: set NARE_HOME")
    private_folder(path.parent)
    # mkstemp is 0600 from the start, and the rename leaves no partial file.
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(entries, handle, indent=2)
        handle.write("\n")
    try:
        os.replace(handle.name, path)
    except OSError:
        os.unlink(handle.name)
        raise
    return path


def stored_key(provider: str) -> str | None:
    key = _load().get(provider, {}).get("api_key")
    return key if isinstance(key, str) and key else None


def store_key(provider: str, key: str) -> Path:
    entries = _load()
    entries[provider] = {"api_key": key}
    return _save(entries)


def remove_key(provider: str) -> bool:
    entries = _load()
    if provider not in entries:
        return False
    del entries[provider]
    _save(entries)
    return True


def key_for(provider: str) -> str | None:
    """The stored key a transport should be given: only when the provider's
    environment variable is unset, which keeps the environment first."""
    env = KEY_ENV.get(provider)
    if env is None or os.environ.get(env):
        return None
    return stored_key(provider)


def source(provider: str) -> tuple[bool, str]:
    """Whether `provider` has a credential, and where it comes from, never the
    credential itself."""
    if provider == "fake":
        return True, "no key needed: it replays a script"
    if provider == "bedrock":
        from nare.transport import bedrock

        if os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
            return True, "AWS_BEARER_TOKEN_BEDROCK (environment)"
        try:
            found = bedrock._credentials_resolve(bedrock._aws_session())
        except ValueError as exc:
            return False, f"the AWS credential chain: {exc}"
        if found:
            return True, "the AWS credential chain (profile, environment or role)"
        return False, "not set: the AWS credential chain found nothing"
    env = KEY_ENV[provider]
    if os.environ.get(env):
        return True, f"{env} (environment)"
    if stored_key(provider):
        return True, f"stored in {credentials_path()}"
    return False, f"not set: set {env}, or run nare auth login --provider {provider}"


def terminal() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError, OSError):
        return False


def hidden_input(prompt: str) -> str:
    return getpass.getpass(prompt)


def read_key(provider: str, *, from_stdin: bool) -> str:
    """The key, from stdin (--with-api-key) or typed with echo off. Never from
    argv, which other users can read."""
    if from_stdin:
        key = sys.stdin.read()
    elif terminal():
        key = hidden_input(f"{provider} API key (input hidden): ")
    else:
        raise ValueError(
            "no terminal to type the key at: pipe it in with --with-api-key, "
            f"or set {KEY_ENV[provider]}"
        )
    key = key.strip()
    if not key:
        raise ValueError("no key given")
    return key


def missing_key_line(provider: str) -> str | None:
    """The one line a person-facing run starts with when it has no key, or
    None when it has one (or the provider needs none)."""
    env = KEY_ENV.get(provider)
    if env is None or os.environ.get(env):
        return None
    try:
        if stored_key(provider):
            return None
    except ValueError as exc:
        return str(exc)
    name = "Anthropic" if provider == "anthropic" else "OpenAI-compatible"
    return (
        f"no {name} API key: run nare auth login"
        + ("" if provider == "anthropic" else f" --provider {provider}")
        + f" to store one, or set {env}"
    )
