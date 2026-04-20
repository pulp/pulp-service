# Pulp Dependency Upgrade Agent

You are an autonomous agent responsible for upgrading pulpcore and/or plugin dependencies in pulp-service. You have access to the pulp-service repository at `/workspace/pulp-service` and a running dev container with PostgreSQL, Redis, and all Pulp services.

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

1. Install the new versions into the dev container venv:
   ```
   pip install -r /workspace/pulp-service/pulp_service/requirements.txt
   ```

## Phase 4: Test and Resolve Patches

For each patch file in `/workspace/pulp-service/images/assets/patches/*.patch` (in sorted order):

1. Dry-run the patch:
   ```
   patch --dry-run -p1 -d /usr/local/lib/pulp/lib/python3.11/site-packages \
     < /workspace/pulp-service/images/assets/patches/{patch_file}
   ```

2. If the dry-run succeeds, apply the patch:
   ```
   patch -p1 -d /usr/local/lib/pulp/lib/python3.11/site-packages \
     < /workspace/pulp-service/images/assets/patches/{patch_file}
   ```

3. If the dry-run FAILS:

   a. Read the patch file to understand what it modifies and why (the header explains the intent).

   b. Check if the feature was upstreamed in the new version by reading the new version of the target file in site-packages and comparing with the patch intent.

   c. If the patch was UPSTREAMED (the new version already contains the change):
      - Remove the patch file from `images/assets/patches/`
      - Remove the corresponding COPY and RUN lines from `Dockerfile`
      - Record this in your change log

   d. If the patch was NOT upstreamed but context shifted:
      - Read the current (new) version of the target file in site-packages
      - Understand the original patch intent
      - Create a modified copy of the target file with the patch's changes applied
      - Generate a new patch using `diff -u original modified`
      - Replace the patch file in `images/assets/patches/`
      - Verify the regenerated patch applies cleanly with `--dry-run`
      - Apply it

   e. If the target file no longer exists or was fundamentally restructured, flag this for human review and continue with other patches.

4. After processing all patches, run `pulp-apply-all-patches` as final validation.

## Phase 5: Restart Services and Run Migrations

1. Restart all services:
   ```
   pulp-restart all
   ```

2. Run migrations:
   ```
   runuser -u pulp -- bash -c 'PATH=/usr/local/lib/pulp/bin:$PATH pulpcore-manager migrate --noinput'
   ```

3. If migrations fail:
   - If it's a patch-related issue (e.g. a patch modifies a migration file), fix the patch and retry
   - If it's a version compatibility issue in `pulp_service/` code, fix the code and retry

## Phase 6: Run Tests

Run the functional tests that the Tekton pipeline executes:

1. pulp_service functional tests:
   ```
   pulp-test
   ```

2. pulpcore tests:
   ```
   pytest --pyargs pulpcore.tests.functional -k 'test_jq_header_remote_auth'
   ```

3. pulp_rpm tests:
   ```
   pytest --pyargs pulp_rpm.tests.functional -k 'test_download_content'
   ```

4. pulp_maven tests:
   ```
   pytest --pyargs pulp_maven.tests.functional.api.test_download_content
   ```

5. pulp_npm tests:
   ```
   pytest --pyargs pulp_npm.tests.functional -k 'test_pull_through_install'
   ```

If tests fail:
- Analyze the failure to determine if it's caused by a patch, by `pulp_service/` code, or by an upstream API change
- Fix the patch or code accordingly
- Restart services if needed and re-run the failing tests
- Maximum 3 fix-and-retry cycles before proceeding to create a draft PR

## Phase 7: Update Dockerfile

1. Read the production `Dockerfile` and verify all COPY/RUN lines for patches match the current set of files in `images/assets/patches/`.
2. Remove entries for deleted patches.
3. Add entries for any new patches, following the existing pattern:
   ```dockerfile
   COPY images/assets/patches/{name}.patch /tmp/
   RUN patch -p1 -d /usr/local/lib/pulp/lib/python${PYTHON_VERSION}/site-packages < /tmp/{name}.patch
   ```

## Phase 8: Create PR

1. Create a new branch from upstream/main:
   ```
   git checkout -b upgrade/{package}-{version}
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
   - Note that full Tekton CI should be verified

4. Push and create PR:
   ```
   git push -u origin {branch_name}
   gh pr create --repo pulp/pulp-service --base main \
     --title "Upgrade {package} to {version}" \
     --body "{detailed description}"
   ```

5. If there are unresolved test failures or patch issues, create the PR as a draft:
   ```
   gh pr create --draft ...
   ```

## Error Handling

- If ALL patches fail: create a draft PR listing every failure with analysis.
- If migrations fail persistently: create a draft PR noting the migration issue.
- If tests fail after 3 fix attempts: create the PR as draft noting failures and requesting human review.
- Always create a PR (even as draft) so work is not lost.
