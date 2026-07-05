FROM debian:bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        cmake \
        coreutils \
        findutils \
        g++ \
        gawk \
        gcc \
        grep \
        make \
        python3 \
        python3-tomli \
        python3-yaml \
        sed \
        tar \
        xz-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

CMD ["/bin/bash"]
