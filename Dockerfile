FROM registry.access.redhat.com/ubi9/ubi
ARG PYTHON_VERSION=3.9

ENV PYTHONUNBUFFERED=0
ENV DJANGO_SETTINGS_MODULE=pulpcore.app.settings
ENV PULP_SETTINGS=/etc/pulp/settings.py
ENV _BUILDAH_STARTED_IN_USERNS=""
ENV BUILDAH_ISOLATION=chroot
ENV PULP_GUNICORN_TIMEOUT=${PULP_GUNICORN_TIMEOUT:-90}
ENV PULP_API_WORKERS=${PULP_API_WORKERS:-2}
ENV PULP_CONTENT_WORKERS=${PULP_CONTENT_WORKERS:-2}

ENV PULP_GUNICORN_RELOAD=${PULP_GUNICORN_RELOAD:-false}
ENV PULP_WORKERS=2
ENV PULP_HTTPS=false
ENV PULP_STATIC_ROOT=/var/lib/operator/static/


# Install updates & dnf plugins before disabling python36 to prevent errors
# COPY images/repos.d/*.repo /etc/yum.repos.d/
COPY images/repos.d/centos9-crb.repo /etc/yum.repos.d/
COPY images/repos.d/centos9-appstream.repo /etc/yum.repos.d/
RUN dnf -y install dnf-plugins-core && \
    dnf -y install https://dl.fedoraproject.org/pub/epel/epel-release-latest-9.noarch.rpm && \
    # dnf config-manager --set-enabled powertools && \
    dnf -y update

# lsof & procps-ng(`ps`) are needed for running pytests (unit/functional)
#
# glibc-langpack-en is needed to provide the en_US.UTF-8 locale, which Pulp
# seems to need.
#
# The last 5 lines (before clean) are needed until python3-createrepo_c gets an
# RPM upgrade to 0.16.2. Until then, we install & build it from PyPI.
#
# TODO: Investigate differences between `dnf builddep createrepo_c` vs the list
# of dependencies below. For example, drpm-devel.
RUN dnf -y install python${PYTHON_VERSION} python${PYTHON_VERSION}-cryptography python${PYTHON_VERSION}-devel python${PYTHON_VERSION}-pip && \
    dnf -y install openssl openssl-devel && \
    dnf -y install wget git && \
    dnf -y install lsof procps-ng && \
    dnf -y install python${PYTHON_VERSION}-psycopg2 && \
    dnf -y install redhat-rpm-config gcc && \
    dnf -y install glibc-langpack-en && \
    dnf -y install python${PYTHON_VERSION}-setuptools && \
    dnf -y install swig && \
    dnf -y install ostree-libs ostree --allowerasing --nobest && \
    dnf -y install patch && \
    dnf -y install jq && \
    dnf -y install zstd

RUN dnf clean all

RUN python${PYTHON_VERSION} -m venv --system-site-packages /usr/local/lib/pulp

ENV PATH="/usr/local/lib/pulp/bin:${PATH}"

# Needed to prevent the wrong version of cryptography from being installed,
# which would break PyOpenSSL.
# Need to install optional dep, rhsm, for pulp-certguard
RUN pip install --upgrade pip setuptools wheel && \
    rm -rf /root/.cache/pip && \
    pip install  \
         rhsm \
         setproctitle \
         gunicorn \
         python-nginx \
         django-storages\[boto3,azure]\>=1.12.2 \
         requests\[use_chardet_on_py3] \
         importlib-metadata \
         watchtower && \
         rm -rf /root/.cache/pip


COPY pulp_service/ /tmp/pulp_service

RUN pip install /tmp/pulp_service && \
  rm -rf /root/.cache/pip

RUN groupadd -g 700 --system pulp
RUN useradd -d /var/lib/pulp --system -u 700 -g pulp pulp

RUN mkdir -p /etc/pulp/certs \
             /etc/ssl/pulp \
             /var/lib/operator/static \
             /var/lib/pgsql \
             /var/lib/pulp/assets \
             /var/lib/pulp/media \
             /var/lib/pulp/scripts \
             /var/lib/pulp/tmp

RUN chown pulp:pulp -R /var/lib/pulp \
                       /var/lib/operator/static

COPY images/assets/route_paths.py /usr/bin/route_paths.py
COPY images/assets/wait_on_postgres.py /usr/bin/wait_on_postgres.py
COPY images/assets/wait_on_database_migrations.sh /usr/bin/wait_on_database_migrations.sh
COPY images/assets/set_init_password.sh /usr/bin/set_init_password.sh
COPY images/assets/add_signing_service.sh /usr/bin/add_signing_service.sh
COPY images/assets/pulp-api /usr/bin/pulp-api
COPY images/assets/pulp-content /usr/bin/pulp-content
COPY images/assets/pulp-resource-manager /usr/bin/pulp-resource-manager
COPY images/assets/pulp-worker /usr/bin/pulp-worker

USER pulp:pulp
RUN PULP_STATIC_ROOT=/var/lib/operator/static/ PULP_CONTENT_ORIGIN=localhost \
       pulpcore-manager collectstatic --clear --noinput --link
USER root:root

# This path seems to be hardcoded in tests
RUN ln -s /usr/local/lib/pulp/bin/pulpcore-manager /usr/local/bin/pulpcore-manager

RUN chmod 2775 /var/lib/pulp/{scripts,media,tmp,assets}
RUN chown :root /var/lib/pulp/{scripts,media,tmp,assets}

COPY images/assets/patches/0005-Add-a-configurable_route-for-the-pypi-endpoint.patch /tmp/
RUN patch -p1 -d /usr/local/lib/pulp/lib/python${PYTHON_VERSION}/site-packages < /tmp/0005-Add-a-configurable_route-for-the-pypi-endpoint.patch

COPY images/assets/patches/0007-Add-a-new-setting-to-use-a-different-BASE_CONTENT_UR.patch /tmp/
RUN patch -p1 -d /usr/local/lib/pulp/lib/python${PYTHON_VERSION}/site-packages < /tmp/0007-Add-a-new-setting-to-use-a-different-BASE_CONTENT_UR.patch

COPY images/assets/patches/0010-Added-ability-to-return-a-URL-for-a-blob.patch /tmp/
RUN patch -p1 -d /usr/local/lib/pulp/lib/python${PYTHON_VERSION}/site-packages < /tmp/0010-Added-ability-to-return-a-URL-for-a-blob.patch

COPY images/assets/patches/0011-ocistorage-backend-changes.patch /tmp/
RUN patch -p1 -d /usr/local/lib/pulp/lib/python${PYTHON_VERSION}/site-packages < /tmp/0011-ocistorage-backend-changes.patch

COPY images/assets/patches/0012-content-otel-instrumentation-exception.patch /tmp/
RUN patch -p1 -d /usr/local/lib/pulp/lib/python${PYTHON_VERSION}/site-packages < /tmp/0012-content-otel-instrumentation-exception.patch

COPY images/assets/patches/0014-Add-Content-Sources-periodic-telemetry-task.patch /tmp/
RUN patch -p1 -d /usr/local/lib/pulp/lib/python${PYTHON_VERSION}/site-packages < /tmp/0014-Add-Content-Sources-periodic-telemetry-task.patch

COPY images/assets/patches/0016-Fix-pulp_content_origin_with_prefix-fixture.patch /tmp/
RUN patch -p1 -d /usr/local/lib/pulp/lib/python${PYTHON_VERSION}/site-packages < /tmp/0016-Fix-pulp_content_origin_with_prefix-fixture.patch

COPY images/assets/patches/0017-Fix-distribution.base_url-in-tests.patch /tmp/
RUN patch -p1 -d /usr/local/lib/pulp/lib/python${PYTHON_VERSION}/site-packages < /tmp/0017-Fix-distribution.base_url-in-tests.patch

COPY images/assets/patches/0018-Re-root-the-registry-API-at-api-pulp-v2.patch /tmp/
RUN patch -p1 -d /usr/local/lib/pulp/lib/python${PYTHON_VERSION}/site-packages < /tmp/0018-Re-root-the-registry-API-at-api-pulp-v2.patch

COPY images/assets/patches/0020-Allow-extra-logging-for-requests.patch /tmp/
RUN patch -p1 -d /usr/local/lib/pulp/lib/python${PYTHON_VERSION}/site-packages < /tmp/0020-Allow-extra-logging-for-requests.patch

COPY images/assets/patches/0019-Adds-LabelsMixin-to-ReadOnlyContentViewSet.patch /tmp/
RUN patch -p1 -d /usr/local/lib/pulp/lib/python${PYTHON_VERSION}/site-packages < /tmp/0019-Adds-LabelsMixin-to-ReadOnlyContentViewSet.patch


RUN mkdir /licenses
COPY LICENSE /licenses/LICENSE

EXPOSE 80
