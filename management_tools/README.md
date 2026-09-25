# Management Tools

## Python Metadata Scripts

Scripts for repairing and verifying PEP 658 metadata on Python repositories.
Both use `~/.netrc` for authentication and can target stage or prod.

### repair-python-metadata.py

Triggers the `repair_metadata` endpoint on all Python repositories across all
domains (excluding content-sources). Each call dispatches an async Pulp task.

```bash
# Dry run — list repos that would be repaired
uv run management_tools/repair-python-metadata.py --env stage --dry-run

# Run the repair
uv run management_tools/repair-python-metadata.py --env stage
uv run management_tools/repair-python-metadata.py --env prod
```

### verify-python-metadata.py

Verifies that `repair_metadata` worked by checking simple pages for each
repository. For each package, it checks that:
1. All wheel links have a `data-core-metadata` attribute with a sha256 hash.
2. The `.metadata` file is downloadable (appending `.metadata` to the wheel URL).

By default, samples one package per repo. Use `--thorough` to check all packages.

```bash
# Sample verification (one package per repo)
uv run management_tools/verify-python-metadata.py --env stage

# Thorough verification (all packages in every repo)
uv run management_tools/verify-python-metadata.py --env prod --thorough
```

Exit code is 0 if all repos pass, 1 if any fail.

---

## tasks-cli.py

# RUNNING FROM SOURCE

* installing dependencies
```
sudo dnf install -y matplotlib
pip install requests
```


```
mkdir /tmp/tasks-cli
```

* running

    ```
    cd management_tools
    ```

    * stage
    ```
    HTTPS_PROXY=http://<internal proxy address> python tasks-cli.py -c <cert file> -k <cert key> --base_address https://mtls.internal.console.stage.redhat.com
    ```

    * prod
    ```
    python tasks-cli.py -c <cert file> -k <cert key> --base_address https://mtls.internal.console.redhat.com
    ```


# RUNNING AS A CONTAINER

* building
```
cd management_tools/
podman build -t tasks-cli:latest .
```

* running
    * ephemeral
    ```
    podman run -v /tmp/tasks-cli:/tmp/tasks-cli -it --rm tasks-cli python tasks-cli.py \
      --base_address http://$(oc get routes pulp-api -ojsonpath='{.spec.host}') \
      --password $(oc extract secret/pulp-admin-password --to=-)

    ```

    * stage
    ```
    podman run -v <path with the cert/key files>:/tmp/cert-dir/ -v /tmp/tasks-cli:/tmp/tasks-cli -it --rm tasks-cli HTTPS_PROXY=<internal proxy address> python tasks-cli.py \
    --period 2 --bucket_size 600 \
    --base_address https://mtls.internal.console.stage.redhat.com \
    -c /tmp/cert-dir/<cert file> \
    -k /tmp/cert-dir/<cert key> \
    --output /tmp/tasks-cli/<metrics file> \
    -g /tmp/tasks-cli/<graph file>
    ```

    * prod
    ```
    podman run -v <path with the cert/key files>:/tmp/cert-dir/ -v /tmp/tasks-cli:/tmp/tasks-cli -it --rm tasks-cli python tasks-cli.py \
    --period 2 --bucket_size 600 \
    --base_address https://mtls.internal.console.redhat.com \
    -c /tmp/cert-dir/<cert file> \
    -k /tmp/cert-dir/<cert key> \
    --output /tmp/tasks-cli/<metrics file> \
    -g /tmp/tasks-cli/<graph file>
    ```

---

## audit-content-guards.py

See [audit-content-guards.md](audit-content-guards.md) for the report format,
interpretation guidance, retry workflow, and `jq` examples.

Read-only inventory of domains, distributions, and content guards for stage or
production. The report excludes `public-*` domains by default and resolves
domain default guards, explicit distribution guards, and composite guard
children. It does not infer Akamai, NSWG, VPN, or cluster-route protection from
Pulp metadata. It uses typed plugin distribution endpoints and intentionally
skips Pulp's generic `/distributions/` aggregation endpoint, which can return
HTTP 500 for stale or malformed subtype records.

Authentication uses credentials from `~/.netrc`.

The script uses the system OpenSSL trust store before Requests' bundled CA
store. If the system trust store is not available in the execution environment,
set `REQUESTS_CA_BUNDLE` to an approved Red Hat CA bundle.

```bash
# Write a stage report
uv run management_tools/audit-content-guards.py \
  --env stage --output reports/content-guards-stage.json

# Optional bounded concurrency (the default is one worker)
uv run management_tools/audit-content-guards.py \
  --env stage --workers 2 --output reports/content-guards-stage.json

# Increase timeout only when fetching guard details (default: 120 seconds)
uv run management_tools/audit-content-guards.py \
  --env stage --guard-detail-timeout 180 \
  --output reports/content-guards-stage.json

# Save failed domains in a resumable manifest
uv run management_tools/audit-content-guards.py \
  --env stage \
  --failed-operations-output reports/content-guards-stage-retry.json \
  --output reports/content-guards-stage.json

# Retry only the remaining failed domains into a new report and manifest
uv run management_tools/audit-content-guards.py \
  --env stage \
  --retry-manifest reports/content-guards-stage-retry.json \
  --failed-operations-output reports/content-guards-stage-retry-2.json \
  --output reports/content-guards-stage-retry-report.json

# Write a production report, including public domains
uv run management_tools/audit-content-guards.py \
  --env prod --include-public --output reports/content-guards-prod.json

# Optionally observe an unauthenticated route (results are endpoint-specific)
uv run management_tools/audit-content-guards.py \
  --env stage \
  --probe-base-url https://packages.stage.redhat.com/api/pulp-content \
  --output reports/content-guards-stage.json
```

The command exits nonzero when inventory is incomplete. Optional probes use
unauthenticated `HEAD` requests, fall back to streamed `GET` when `HEAD` returns
405, do not follow redirects, and are reported as `probe_allowed`,
`probe_denied`, or `probe_indeterminate`; a probe result is not evidence about
other routes. Requests are rate-limited globally across
workers; stage defaults to 4 requests per second and production to 2. Use
`--quiet` to suppress progress messages. Failed-operation manifests are
checkpointed atomically after each domain and are scope-bound to the original
environment, public-domain setting, probes, and distribution endpoints. A
retry produces a new report; it never merges snapshots. The normal API read
timeout is 30 seconds; `--guard-detail-timeout` controls only content-guard
detail retrieval and defaults to 120 seconds.
