# Set up Nare in this project

Start in the checkout where you want to use Nare. You can complete the check
in a headless shell. Only opening the terminal UI needs an interactive terminal.

## Find the release binary

Run `nare --version`. If it prints a version, reuse it. If the shell cannot
find `nare` and uv is installed, try `"$(uv tool dir --bin)/nare" --version`.
If that works, run `export PATH="$(uv tool dir --bin):$PATH"` for this shell.
`uv tool update-shell` adds the directory for future shells.

For a fresh install on macOS or Linux, run the official release installer:

```sh
curl -fsSL https://github.com/ViviDynamics/nare/releases/latest/download/install.sh | sh
```

Follow any PATH instruction it prints, then run `nare --version` again.
The current installer installs uv when needed. If an older installer asks
for uv, install it using
[uv's installation guide](https://docs.astral.sh/uv/getting-started/installation/)
and rerun the installer. Also use the release installer if an older binary
lacks `--require-read` or the TUI. If `curl` is missing, install it with the system
package manager first (for example, `sudo apt install curl` on Ubuntu).
If the installer asks for a SHA-256 utility, install `coreutils` with the
system package manager and rerun it.

## Use the project's provider

Reuse the provider, model, endpoint and credentials from the project's normal
launch environment. Keep existing `NARE_PROVIDER`, `NARE_MODEL` and
`NARE_BASE_URL` settings. When checking whether credentials are present, test
only for presence (for example, `test -n "${ANTHROPIC_API_KEY:+set}"`). Avoid
printing the environment or opening secret files.

| Provider | What the verification shell needs |
| --- | --- |
| Anthropic (default) | `ANTHROPIC_API_KEY`. Use the default model, or set `NARE_MODEL` to an enabled model. |
| OpenAI or a compatible endpoint | `NARE_PROVIDER=openai`, `OPENAI_API_KEY` and `NARE_MODEL` set to the served model ID. Set `NARE_BASE_URL` for a proxy or local server. |
| Amazon Bedrock | `NARE_PROVIDER=bedrock`, a Bedrock model or inference-profile ID in `NARE_MODEL`, an AWS region and credentials from the AWS chain. See [Bedrock setup](../README.md#claude-on-amazon-bedrock). |

Export any newly chosen settings in the shell that will run Nare. When the
project has no explicit provider choice, reuse an unambiguous configured
account. Ask only about a provider, model or endpoint that is still ambiguous
or missing. For missing credentials, have the operator
launch the verification shell or coding agent through their secret manager.
For a one-time API key, the operator can also run the following in a private
Bash terminal and continue verification there. For an OpenAI-compatible
provider, replace both occurrences of `ANTHROPIC_API_KEY` with `OPENAI_API_KEY`
and change the prompt label.

```bash
set +x
IFS= read -rs -p 'Anthropic API key: ' ANTHROPIC_API_KEY && export ANTHROPIC_API_KEY
printf '\n'
```

Keep keys out of chats, command arguments, logs and project files. A headless
agent that lacks credentials should report the variable the operator needs to
supply and stop before making the model call.

## Verify one project read

Choose a small project file suitable for sharing with the configured provider.
The example uses `README.md`. Change the first line to a project-relative path
if this checkout has a different public-facing document.

```sh
project_file='README.md'
nare --version
nare run --yes --jsonl --root "$PWD" --tools read --require-read "$project_file" \
  "Use read on $project_file. Return its first nonempty line."
```

The last JSON line should have `"type": "result"`, `"status": "done"`, the
chosen path in `reads`, and the first line in `text`. Verify those fields
and exit code 0 before reporting success. The model has only the read tool.
You can repeat this check with the same settings.

If Bedrock reports a missing extra on a release install, add it using the
wheel already verified by the installer, then retry:

```sh
version=$(nare --version)
wheel="${XDG_DATA_HOME:-$HOME/.local/share}/nare/wheels/nare-$version-py3-none-any.whl"
uv tool install --force --python '>=3.12' "nare[tui,bedrock] @ file://$wheel"
```

If authentication fails, supply the selected provider's credential in this
shell. If the provider rejects the model, set `NARE_MODEL` to a tool-capable
model enabled for that account and endpoint. If the required read failed,
confirm the file exists inside the checkout and rerun the command.

Report the version and verified path. To continue interactively, open a
terminal in the same checkout with the same provider settings and run:

```sh
nare tui
```
