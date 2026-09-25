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

## Identity content guard migration

For the complete migration procedure, guard model, partial-apply workflow, and
troubleshooting guidance, see
[`docs/identity-content-guard-migration.md`](../docs/identity-content-guard-migration.md).

`apply-identity-content-guards.py` consumes one or more JSON reports produced by
the content-guard audit. It uses the `hosted-pulp` CLI to create or reuse the
canonical `hosted-pulp-default-identity-check` HeaderContentGuard in each
affected domain. Its API-compatible presence-check values are
`header_value=identity-present` and `jq_filter='"identity-present"'`. The tool
then applies it to unguarded distributions. It also
provisions/reuses `hosted-pulp-default-vpn-check` (`X-Pulp-VPN-Verified: true`)
and the `hosted-pulp-default-identity-or-vpn` OR composite containing both
guards. The composite is not assigned to distributions by this tool.

The default mode is a plan. Use `--apply --yes` to mutate Pulp. Multiple audit
reports can be supplied when a second report fills domains missed by the first:

```bash
uv run management_tools/apply-identity-content-guards.py \
  --report content-guards-stage-report.json \
  --report content-guards-stage-missing-domains.json \
  --allow-incomplete-report \
  --profile stage-tbr \
  --max-changes 1

# Apply when the primary report is supplemented by a complete, error-free
# report covering every failed domain.
uv run management_tools/apply-identity-content-guards.py \
  --report content-guards-stage-report.json \
  --report content-guards-stage-missing-domains.json \
  --allow-incomplete-report \
  --profile stage-tbr \
  --apply --yes \
  --output content-guard-application.json

# Alternatively, apply only successfully audited domains. This returns exit
# code 3 and leaves unresolved audit domains recorded as pending.
uv run management_tools/apply-identity-content-guards.py \
  --report content-guards-stage-report.json \
  --profile stage-tbr \
  --partial-apply --apply --yes \
  --max-changes 9 \
  --output content-guard-partial-application.json
```

The report is deduplicated by distribution `pulp_href`. Public-prefixed
domains and distributions that already have an effective or explicit guard
are skipped. Result and rollback state files are written atomically with
owner-only permissions. Incomplete or errored reports may be used for planning
with `--allow-incomplete-report`. Apply mode also accepts an incomplete primary
report only when a complete, error-free supplemental report with matching audit
scope covers every failed domain. `--partial-apply` is the explicit exception:
it applies only successfully audited candidates, returns exit code 3, and
records unresolved domains as pending. Before each mutation, the tool reads the
live domain and distribution, records the prior state, waits for the update,
and verifies the assigned guard afterward. The rollback manifest records the
exact prior explicit/effective guard state and should be used to restore a
distribution only after checking that its current guard still matches the
guard assigned by this run. Human-readable progress is written to stderr while
stdout remains a machine-readable final summary.

Live distribution preflight reads use a bounded concurrency of eight. Guard
creation and distribution assignments remain serialized per domain; assignment
convergence polling backs off to a five-second maximum interval.

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
