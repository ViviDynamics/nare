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

ARG WHEEL
COPY ${WHEEL} /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm -rf /tmp/*.whl

USER nare
WORKDIR /work

# No ENTRYPOINT wrapper: `docker run ghcr.io/vividynamics/nare run --yes ...`
# is the same argv as the CLI, so a caller's command does not change shape when
# it moves into a container.
ENTRYPOINT ["nare"]
