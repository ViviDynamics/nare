# The benchmark sandbox. Installs the WORKING TREE's nare, not a release, so
# the suite measures the change under test.
FROM python:3.12-slim

# git for the fixture checkpoint the judge diffs against; ripgrep because the
# tool set has no grep tool and cases are expected to reach for `rg`.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ripgrep \
    && rm -rf /var/lib/apt/lists/*
# pip and its vendored certifi from verified wheels first: the base image's pip
# still trusts the distrusted emSign CAs (#110).
RUN python -m pip install --no-cache-dir --no-deps \
    "pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70" \
    "certifi @ https://files.pythonhosted.org/packages/c4/51/29046e48f693c54e5b7412216d447a1d8362c9653148d98c02cdf65fa733/certifi-2026.10.19-py3-none-any.whl#sha256=42d35340cc5b9ba78e818ddbff0c5b1c2b779de448a340fd1b989f5fa8670e1b"

RUN pip install --no-cache-dir pytest

WORKDIR /src
# pyproject references all three, so a build without them fails.
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
# hatch-vcs reads the version out of git, and .git is not in the build context
# on purpose: shipping the history would bloat every build and bust the layer
# cache on every commit. The benchmark never reads this version, so a stand-in
# is enough. The tree under test is still the working tree.
ARG SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0+bench
RUN pip install --no-cache-dir .

# The container runs as the invoking user so that files written into the bind
# mount are owned by them and `git diff` on the host does not trip over
# dubious ownership. That user has no home directory, and git and pip both
# need a writable one.
ENV HOME=/tmp
WORKDIR /work
