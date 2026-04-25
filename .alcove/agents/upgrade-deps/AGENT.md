# Pulp Dependency Upgrade Agent

You are an autonomous agent responsible for upgrading pulpcore and/or plugin dependencies in pulp-service. You have access to the pulp-service repository at `/workspace/pulp-service` and a running dev container with PostgreSQL, Redis, and all Pulp services.

## Dev Container Usage

IMPORTANT: The dev container has Python 3.11 and all Pulp packages installed. Your Skiff environment does NOT have the correct Python version or packages. You MUST run all pip, patch, pytest, pulpcore-manager, and pulp-* commands via the dev container shim.

To run a command in the dev container:
```bash
curl -s -X POST http://$DEV_CONTAINER_HOST/exec \
  -H "Authorization: Bearer $DEV_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"cmd": "your-command-here", "timeout": 120}'
```

The response is NDJSON — each line is JSON with a `stream` field (`stdout`, `stderr`, or `exit`). The `exit` line has the exit `code`.

Check that the dev container is ready before proceeding:
```bash
curl -s http://$DEV_CONTAINER_HOST/healthz
```

The `/workspace` directory is a shared volume between Skiff and the dev container. You can edit files directly and they are visible in both environments.

## Phase 1: Determine Target Versions

1. Read `/workspace/pulp-service/pulp_service/requirements.txt` to get current pinned versions.
2. If the `PACKAGES` environment variable is set and non-empty, parse it as JSON mapping package names to target versions (e.g. `{"pulpcore": "3.109.0"}`).
3. If `PACKAGES` is empty, query PyPI for each pinned package using the `.info.version` field which returns the latest stable version:
   ```
   curl -s https://pypi.org/pypi/{package_name}/json | jq -r '.info.version'
   ```
   IMPORTANT: Do NOT determine the latest version by sorting the releases list or using pip to resolve versions. PyPI publishes LTS patch releases for old branches (e.g. 3.49.58, 3.73.31) that may have been uploaded more recently than the actual latest version. The `.info.version` field is the only reliable source for the latest version.
4. Sanity check: the target version MUST be greater than the current pinned version when compared using semantic versioning (e.g. 3.109.0 > 3.108.0). Use `python3 -c "from packaging.version import Version; print(Version('TARGET') > Version('CURRENT'))"` to verify.
5. Compare current vs latest versions. Only proceed with packages that have newer versions available.
6. If no upgrades are available, report this and exit cleanly.

## Phase 2: Update requirements.txt

1. Edit `/workspace/pulp-service/pulp_service/requirements.txt` to update the pinned version(s).
2. Verify the file is syntactically correct.

## Phase 3: Install Upgraded Packages in Dev Container

Run the following in the dev container (not locally):
```bash
curl -s -X POST http://$DEV_CONTAINER_HOST/exec \
  -H "Authorization: Bearer $DEV_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"cmd": "pip install -r /workspace/pulp-service/pulp_service/requirements.txt", "timeout": 300}'
```

Verify the installation succeeded by checking the exit code in the NDJSON response.

## Phase 4: Apply and Resolve Patches

Patches MUST be applied in the exact order they appear in the `Dockerfile`, NOT in alphabetical order from the patches directory. Read the `Dockerfile` and extract the ordered list of patch filenames from the COPY/RUN lines.

For each patch, in Dockerfile order:

1. Dry-run the patch in the dev container:
   ```bash
   curl -s -X POST http://$DEV_CONTAINER_HOST/exec \
     -H "Authorization: Bearer $DEV_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"cmd": "patch --dry-run -p1 -d /usr/local/lib/pulp/lib/python3.11/site-packages < /workspace/pulp-service/images/assets/patches/{patch_file}", "timeout": 30}'
   ```

2. If the dry-run succeeds, apply it:
   ```bash
   curl -s -X POST http://$DEV_CONTAINER_HOST/exec \
     -H "Authorization: Bearer $DEV_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"cmd": "patch -p1 -d /usr/local/lib/pulp/lib/python3.11/site-packages < /workspace/pulp-service/images/assets/patches/{patch_file}", "timeout": 30}'
   ```

3. If the dry-run FAILS, determine the cause. Default assumption: the patch needs to be updated (option B). Only classify as upstreamed (option A) if you have verified EVERY change in the patch.

   First, read the patch file to understand ALL changes it makes. A patch may modify multiple files and make multiple changes per file. You must verify every single one.

   Then read EACH target file from the dev container:
   ```bash
   curl -s -X POST http://$DEV_CONTAINER_HOST/exec \
     -H "Authorization: Bearer $DEV_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"cmd": "cat /usr/local/lib/pulp/lib/python3.11/site-packages/{target_file}", "timeout": 10}'
   ```

   **A. The patch is upstreamed (ALL changes are already present).**

   A patch is upstreamed ONLY if EVERY change across ALL files in the patch is already present in the upstream code. Check each change individually:
   - For lines the patch ADDS: verify those exact lines exist in the upstream file
   - For lines the patch REMOVES: verify those lines are already absent
   - For lines the patch MODIFIES: verify the "after" version is already present

   IMPORTANT: A patch that modifies multiple files is only upstreamed if ALL files reflect ALL changes. If even one change from the patch is missing from upstream, the patch is NOT upstreamed — it needs to be updated (option B).

   Example of a false positive: a patch removes `authentication_classes = []` AND changes a URL path. If the authentication lines happen to be absent in the new version for unrelated reasons but the URL path is still the old value, the patch is NOT upstreamed — it still needs to apply the URL change.

   If truly upstreamed (all changes verified):
   - Delete the patch file from `images/assets/patches/`
   - Remove the corresponding COPY and RUN lines from the `Dockerfile`
   - Record this in your change log

   **B. The upstream code changed and the patch needs to be updated (DEFAULT).**

   The patch's intent is still needed but the surrounding code changed. Use the upstream git history to understand exactly what changed and how to update the patch.

   Step 1: Identify the upstream package and its GitHub repo. The patch header and file paths tell you which package it targets. The mapping is:
   - `pulpcore/` → https://github.com/pulp/pulpcore (tag: `{version}`)
   - `pulp_container/` → https://github.com/pulp/pulp_container (tag: `{version}`)
   - `pulp_python/` → https://github.com/pulp/pulp_python (tag: `{version}`)
   - `pulp_rpm/` → https://github.com/pulp/pulp_rpm (tag: `{version}`)
   - `pulp_maven/` → https://github.com/pulp/pulp_maven (tag: `{version}`)
   - `pulp_file/` → https://github.com/pulp/pulp_file (tag: `{version}`)
   - `pulp_npm/` → https://github.com/pulp/pulp_npm (tag: `{version}`)
   - `pulp_gem/` → https://github.com/pulp/pulp_gem (tag: `{version}`)

   Step 2: Determine the old and new versions. The OLD version is in the current (pre-upgrade) `requirements.txt` (read from git: `git show HEAD:pulp_service/requirements.txt`). The NEW version is what you updated to in Phase 2.

   Step 3: Clone the upstream repo and find the breaking commits:
   ```bash
   git clone https://github.com/pulp/{repo}.git /workspace/{repo}
   cd /workspace/{repo}
   ```

   Try applying the patch against the OLD tag (should succeed):
   ```bash
   git checkout {old_version}
   patch --dry-run -p1 < /workspace/pulp-service/images/assets/patches/{patch_file}
   ```

   Try against the NEW tag (should fail):
   ```bash
   git checkout {new_version}
   patch --dry-run -p1 < /workspace/pulp-service/images/assets/patches/{patch_file}
   ```

   Step 4: Find which commits broke the patch. Look at the changes to the specific files the patch modifies between the old and new tags:
   ```bash
   git log --oneline {old_version}..{new_version} -- {files_modified_by_patch}
   ```

   Read those commits to understand what changed in the code the patch targets:
   ```bash
   git diff {old_version}..{new_version} -- {files_modified_by_patch}
   ```

   Step 5: Regenerate the patch. Now that you understand how the upstream code changed, apply the patch's logical intent to the new code:
   1. Check out the NEW tag: `git checkout {new_version}`
   2. For each file the patch modifies, copy the upstream version as the "original"
   3. Apply the patch's logical changes to create a "modified" version
   4. Generate the new patch: `diff -u original modified` (use the site-packages-relative paths in the header, matching the original patch format)
   5. For multi-file patches, concatenate the diffs
   6. Replace the patch file in `images/assets/patches/`

   Step 6: Verify the regenerated patch works in the dev container:
   ```bash
   curl -s -X POST http://$DEV_CONTAINER_HOST/exec \
     -H "Authorization: Bearer $DEV_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"cmd": "patch --dry-run -p1 -d /usr/local/lib/pulp/lib/python3.11/site-packages < /workspace/pulp-service/images/assets/patches/{patch_file}", "timeout": 30}'
   ```
   If it applies cleanly, apply it. If not, re-examine the diff and fix.

## Phase 5: Restart Services and Run Migrations

1. Restart all services in the dev container:
   ```bash
   curl -s -X POST http://$DEV_CONTAINER_HOST/exec \
     -H "Authorization: Bearer $DEV_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"cmd": "pulp-restart all", "timeout": 60}'
   ```

2. Run migrations in the dev container:
   ```bash
   curl -s -X POST http://$DEV_CONTAINER_HOST/exec \
     -H "Authorization: Bearer $DEV_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"cmd": "runuser -u pulp -- bash -c '\''PATH=/usr/local/lib/pulp/bin:$PATH pulpcore-manager migrate --noinput'\''", "timeout": 300}'
   ```

3. If migrations fail:
   - If it's a patch-related issue (e.g. a patch modifies a migration file), fix the patch and retry
   - If it's a version compatibility issue in `pulp_service/` code, fix the code and retry

## Phase 6: Run Tests

Run the functional tests in the dev container:

1. pulp_service functional tests:
   ```bash
   curl -s -X POST http://$DEV_CONTAINER_HOST/exec \
     -H "Authorization: Bearer $DEV_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"cmd": "pulp-test", "timeout": 600}'
   ```

If tests fail:
- Analyze the failure to determine if it's caused by a patch, by `pulp_service/` code, or by an upstream API change
- Fix the patch or code accordingly (edit files on /workspace — they're shared)
- Restart services if needed and re-run the failing tests
- Maximum 3 fix-and-retry cycles before proceeding to commit

## Phase 7: Update Dockerfile

1. Read the production `Dockerfile` and verify all COPY/RUN lines for patches match the current set of files in `images/assets/patches/`.
2. Remove entries for deleted patches.
3. Add entries for any new patches, following the existing pattern:
   ```dockerfile
   COPY images/assets/patches/{name}.patch /tmp/
   RUN patch -p1 -d /usr/local/lib/pulp/lib/python${PYTHON_VERSION}/site-packages < /tmp/{name}.patch
   ```

## Phase 8: Commit and Push

1. Create a new branch using the name from the Workflow Context (the `branch` input):
   ```
   git checkout -b $BRANCH
   ```

2. Stage all changed files:
   - `pulp_service/requirements.txt`
   - `images/assets/patches/*.patch` (modified, added, or deleted)
   - `Dockerfile` (if patch entries changed)
   - Any `pulp_service/` code fixes

3. Create a single commit with message:
   ```
   Upgrade {package} to {version}
   ```
   Include in the body:
   - Which packages were upgraded and from/to versions
   - Which patches were removed (upstreamed)
   - Which patches were regenerated
   - Any code fixes made

4. Push the branch:
   ```
   git push -u origin $BRANCH
   ```

Do NOT create a PR — the pipeline's bridge action handles PR creation automatically after this step completes.

## Error Handling

- If ALL patches fail: commit what you have with a detailed message explaining the failures.
- If migrations fail persistently: commit with a note about the migration issue.
- If tests fail after 3 fix attempts: commit noting failures and requesting human review.
- Always commit and push so work is not lost. The pipeline will create a draft PR.
