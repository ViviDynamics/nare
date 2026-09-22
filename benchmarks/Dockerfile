# The benchmark sandbox. Installs the WORKING TREE's nare, not a release, so
# the suite measures the change under test.
FROM python:3.12-slim

# git for the fixture checkpoint the judge diffs against; ripgrep because the
# tool set has no grep tool and cases are expected to reach for `rg`.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ripgrep \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pytest

WORKDIR /src
# pyproject references all three, so a build without them fails.
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN pip install --no-cache-dir .

# The container runs as the invoking user so that files written into the bind
# mount are owned by them and `git diff` on the host does not trip over
# dubious ownership. That user has no home directory, and git and pip both
# need a writable one.
ENV HOME=/tmp
WORKDIR /work
