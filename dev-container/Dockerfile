# Stage 0: Build the alcove-shim binary
FROM docker.io/library/golang:1.26 AS shim-builder
WORKDIR /src
RUN git clone --depth=1 https://github.com/bmbouter/alcove.git . && \
    CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /out/alcove-shim ./cmd/shim

FROM registry.access.redhat.com/ubi9/ubi

ARG PYTHON_VERSION=3.11
ENV PYTHONUNBUFFERED=0
ENV DJANGO_SETTINGS_MODULE=pulpcore.app.settings
ENV PULP_SETTINGS=/etc/pulp/settings.py
ENV PULP_GUNICORN_TIMEOUT=90
ENV PULP_API_WORKERS=2
ENV PULP_CONTENT_WORKERS=2
ENV PULP_GUNICORN_RELOAD=true
ENV PULP_WORKERS=2
ENV PULP_HTTPS=false
ENV PULP_STATIC_ROOT=/var/lib/operator/static/
ENV PYTHON_VERSION=${PYTHON_VERSION}
ENV PATH="/usr/local/lib/pulp/bin:${PATH}"
COPY images/repos.d/centos9-crb.repo /etc/yum.repos.d/
COPY images/repos.d/centos9-appstream.repo /etc/yum.repos.d/

RUN dnf -y install dnf-plugins-core && \
    dnf -y install https://dl.fedoraproject.org/pub/epel/epel-release-latest-9.noarch.rpm && \
    dnf -y update

RUN dnf -y install \
        python${PYTHON_VERSION} python${PYTHON_VERSION}-cryptography \
        python${PYTHON_VERSION}-devel python${PYTHON_VERSION}-pip \
        openssl openssl-devel \
        wget git \
        lsof procps-ng \
        python${PYTHON_VERSION}-psycopg2 \
        redhat-rpm-config gcc \
        glibc-langpack-en \
        python${PYTHON_VERSION}-setuptools \
        swig \
        ostree-libs ostree --allowerasing --nobest && \
    dnf -y install patch jq zstd vim-minimal less findutils

RUN dnf -y install https://download.postgresql.org/pub/repos/yum/reporpms/EL-9-x86_64/pgdg-redhat-repo-latest.noarch.rpm && \
    dnf -y module disable postgresql && \
    dnf -y install postgresql16-server postgresql16 postgresql16-contrib && \
    dnf -y install redis && \
    dnf clean all

ENV PATH="/usr/pgsql-16/bin:${PATH}"
ENV PGDATA=/var/lib/pgsql/16/data

RUN python${PYTHON_VERSION} -m venv --system-site-packages /usr/local/lib/pulp

RUN pip install --upgrade pip setuptools wheel && \
    pip install \
        rhsm setproctitle \
        "gunicorn>=22.0,<25.1.0" \
        python-nginx \
        "django-storages[boto3,azure]>=1.12.2" \
        "requests[use_chardet_on_py3]" \
        importlib-metadata watchtower \
        supervisor \
    && rm -rf /root/.cache/pip

COPY pulp_service/ /tmp/pulp_service
RUN pip install /tmp/pulp_service && rm -rf /root/.cache/pip

RUN groupadd -g 700 --system pulp && \
    useradd -d /var/lib/pulp --system -u 700 -g pulp pulp

RUN mkdir -p /etc/pulp/certs \
             /etc/ssl/pulp \
             /var/lib/operator/static \
             /var/lib/pgsql \
             /var/lib/pulp/assets \
             /var/lib/pulp/media \
             /var/lib/pulp/scripts \
             /var/lib/pulp/tmp \
             /workspace \
             /var/log/pulp

RUN chown pulp:pulp -R /var/lib/pulp /var/lib/operator/static /var/log/pulp

COPY images/assets/route_paths.py /usr/bin/route_paths.py
COPY images/assets/wait_on_postgres.py /usr/bin/wait_on_postgres.py
COPY images/assets/wait_on_database_migrations.sh /usr/bin/wait_on_database_migrations.sh
COPY images/assets/set_init_password.sh /usr/bin/set_init_password.sh
COPY images/assets/add_signing_service.sh /usr/bin/add_signing_service.sh
COPY images/assets/pulp-api /usr/bin/pulp-api
COPY images/assets/pulp-content /usr/bin/pulp-content
COPY images/assets/pulp-resource-manager /usr/bin/pulp-resource-manager
COPY images/assets/pulp-worker /usr/bin/pulp-worker
COPY images/assets/log_middleware.py /usr/bin/log_middleware.py

USER pulp:pulp
RUN PULP_STATIC_ROOT=/var/lib/operator/static/ PULP_CONTENT_ORIGIN=localhost \
    pulpcore-manager collectstatic --clear --noinput --link
USER root:root

RUN ln -sf /usr/local/lib/pulp/bin/pulpcore-manager /usr/local/bin/pulpcore-manager

RUN chmod 2775 /var/lib/pulp/{scripts,media,tmp,assets} && \
    chown :root /var/lib/pulp/{scripts,media,tmp,assets}

COPY images/assets/patches/ /tmp/patches/
RUN for patch_file in /tmp/patches/*.patch; do \
        echo "Applying $(basename $patch_file)..." && \
        patch -p1 -d /usr/local/lib/pulp/lib/python${PYTHON_VERSION}/site-packages < "$patch_file" || \
        echo "WARNING: Failed to apply $(basename $patch_file)"; \
    done && rm -rf /tmp/patches

RUN openssl rand -base64 32 > /etc/pulp/certs/database_fields.symmetric.key && \
    chown pulp:pulp /etc/pulp/certs/database_fields.symmetric.key && \
    chmod 600 /etc/pulp/certs/database_fields.symmetric.key

# PostgreSQL: remove the data directory created by the RPM, so initdb can
# create it at runtime with the correct UID ownership. On OpenShift, the
# runtime UID is arbitrary (e.g., 1000830000) and initdb requires creating
# PGDATA itself with 0700 permissions.
RUN mkdir -p /var/run/postgresql && \
    rm -rf /var/lib/pgsql/16/data && \
    chown -R 700:0 /var/run/postgresql /var/lib/pgsql && \
    chmod -R g+rwX /var/run/postgresql /var/lib/pgsql

# Ensure all runtime directories are writable by any UID in GID 0 (OpenShift convention)
RUN chown -R 700:0 /var/run/postgresql /var/lib/pgsql /var/log/pulp \
                   /var/lib/pulp /usr/local/lib/pulp /etc/pulp && \
    chmod -R g+rwX /var/run/postgresql /var/lib/pgsql /var/log/pulp \
                   /var/lib/pulp /usr/local/lib/pulp /etc/pulp && \
    mkdir -p /var/run/supervisord && \
    chown 700:0 /var/run/supervisord && \
    chmod g+rwX /var/run/supervisord && \
    chmod 777 /workspace

COPY dev-container/settings.py /etc/pulp/settings.py
COPY dev-container/supervisord.conf /etc/supervisord.conf
COPY dev-container/entrypoint.sh /entrypoint.sh
COPY dev-container/scripts/ /usr/local/bin/

# Install the alcove-shim binary for dev container communication
COPY --from=shim-builder /out/alcove-shim /usr/local/bin/alcove-shim

RUN chmod +x /entrypoint.sh /usr/local/bin/pulp-* /usr/local/bin/alcove-shim

# Run as pulp user — no root required at runtime
USER 700

VOLUME ["/workspace"]
EXPOSE 24817 24816

ENTRYPOINT ["/entrypoint.sh"]
