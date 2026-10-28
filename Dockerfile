# Base image for exec-repro.
#
# python:3.11-slim alone ships neither git nor a C toolchain. Eight repositories
# in an earlier campaign were recorded as failing to build because pip could not
# clone a `git+https://` requirement or could not link a C extension, which are
# properties of our image rather than of the artefact. The study's own policy is
# that harness defects must not be counted against the literature, so the image
# supplies what an ordinary reader's machine would have.
#
# It deliberately does NOT pre-install any Python package: what a repository can
# install is the thing being measured.
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential pkg-config \
    && rm -rf /var/lib/apt/lists/*
