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
