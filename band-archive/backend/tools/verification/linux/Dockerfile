# Verification-only image. Never use this recipe for production deployment.
FROM registry.fly.io/band-archive@sha256:156133750a9ec26589a677023d66142f9d097e7b94e1659ea226a93e118cd376
ARG PG_PACKAGE_VERSION=17.11-0+deb13u1
USER root
COPY support /verification-support
COPY source /verification/source
RUN mkdir /verification-evidence && \
    python /verification-support/runtime_manifest.py capture /verification-evidence/runtime-before.json && \
    python /verification-support/os_inventory.py capture /verification-evidence/os-before.json && \
    python /verification-support/verify_image_source.py
RUN apt-get update && \
    apt-get -s --no-install-recommends --no-upgrade install \
        postgresql-17=${PG_PACKAGE_VERSION} postgresql-client-17=${PG_PACKAGE_VERSION} libpq5=${PG_PACKAGE_VERSION} \
        > /verification-evidence/apt-plan.txt && \
    python /verification-support/os_inventory.py check-plan /verification-evidence/apt-plan.txt && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends --no-upgrade --no-remove \
        postgresql-17=${PG_PACKAGE_VERSION} postgresql-client-17=${PG_PACKAGE_VERSION} libpq5=${PG_PACKAGE_VERSION} && \
    python /verification-support/os_inventory.py compare /verification-evidence/os-before.json /verification-evidence/os-after.json && \
    python /verification-support/runtime_manifest.py capture /verification-evidence/runtime-after-tools.json && \
    python /verification-support/runtime_manifest.py compare /verification-evidence/runtime-before.json /verification-evidence/runtime-after-tools.json && \
    /usr/lib/postgresql/17/bin/postgres --version > /verification-evidence/postgresql-version.txt && \
    rm -rf /var/lib/apt/lists/*
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin verification && \
    chown -R 10001:10001 /verification/source && \
    chown 10001:10001 /var/run/postgresql && \
    chmod -R a+rX /verification-support /verification-evidence
USER 10001:10001
WORKDIR /verification/source/backend
ENTRYPOINT ["python", "/verification-support/container_entrypoint.py"]
