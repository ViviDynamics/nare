# nare in a container, for a caller that is not a Python project.
#
# The wheel is built outside and copied in, so the image carries no build
# toolchain and no source tree: what ships is the same artifact attached to the
# release, installed once.
FROM python:3.12-slim

# Not root. nare runs model-generated shell commands, and the container is the
# containment the README tells callers to provide; running those as root inside
# it would hand that away.
RUN useradd --create-home --uid 10001 nare

# pip and its vendored certifi from verified wheels first: the base image's pip
# still trusts the distrusted emSign CAs (#110).
RUN python -m pip install --no-cache-dir --no-deps \
    "pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70" \
    "certifi @ https://files.pythonhosted.org/packages/c4/51/29046e48f693c54e5b7412216d447a1d8362c9653148d98c02cdf65fa733/certifi-2026.10.19-py3-none-any.whl#sha256=42d35340cc5b9ba78e818ddbff0c5b1c2b779de448a340fd1b989f5fa8670e1b"

ARG WHEEL
COPY ${WHEEL} /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm -rf /tmp/*.whl

USER nare
WORKDIR /work

# No ENTRYPOINT wrapper: `docker run ghcr.io/vividynamics/nare run --yes ...`
# is the same argv as the CLI, so a caller's command does not change shape when
# it moves into a container.
ENTRYPOINT ["nare"]
