#!/bin/bash
set -e

PG_BIN=/usr/pgsql-16/bin
PG_DATA=/var/lib/pgsql/16/data

echo "=== Pulp Dev Container Starting (rootless) ==="

# Start PostgreSQL (runs as current user — no runuser needed)
echo "Starting PostgreSQL..."
$PG_BIN/pg_ctl -D $PG_DATA start -l /var/lib/pgsql/pg.log -w

# Wait for PostgreSQL
until $PG_BIN/pg_isready -h localhost -q; do
    sleep 1
done

# Create pulp database (idempotent — current user is the DB superuser)
$PG_BIN/psql -d postgres -tc "SELECT 1 FROM pg_database WHERE datname = 'pulp'" | grep -q 1 || \
    $PG_BIN/psql -d postgres -c "CREATE DATABASE pulp"

# Start Redis (runs as current user)
echo "Starting Redis..."
redis-server --bind 127.0.0.1 --daemonize yes --protected-mode yes

# If /workspace/pulp-service has source, install in dev mode
if [ -d "/workspace/pulp-service/pulp_service" ]; then
    echo "Installing pulp-service from /workspace in development mode..."
    pip install -e /workspace/pulp-service/pulp_service --quiet 2>&1 || true
fi

# Run database migrations (already running as pulp)
echo "Running database migrations..."
pulpcore-manager migrate --noinput

# Set admin password
echo "Setting admin password..."
pulpcore-manager reset-admin-password --password "${PULP_DEFAULT_ADMIN_PASSWORD:-password}" 2>/dev/null || true

# Stop PostgreSQL and Redis — supervisord will manage them
$PG_BIN/pg_ctl -D $PG_DATA stop -m fast -w
redis-cli shutdown 2>/dev/null || true

echo "=== Initialization complete. Starting services via supervisord ==="
exec supervisord -n -c /etc/supervisord.conf
