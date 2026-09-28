# Identity content guard migration

This runbook describes the management tool for [PULP-2419](https://redhat.atlassian.net/browse/PULP-2419). The tool reads content-guard audit reports and uses the `hosted-pulp` CLI to provision guards and protect distributions in the stage Pulp instance.

## Guard model

For every affected domain, the tool provisions or reuses the following guards:

| Guard | Configuration | Distribution assignment |
|---|---|---|
| `hosted-pulp-default-identity-check` | Header `x-rh-identity`; value `identity-present`; JQ filter `"identity-present"` | Assigned to eligible distributions |
| `hosted-pulp-default-vpn-check` | Header `X-Pulp-VPN-Verified`; value `true`; no JQ filter | Provisioned only |
| `hosted-pulp-default-identity-or-vpn` | OR composite containing the two guards above | Provisioned only |

The identity sentinel is used because the REST API requires a non-empty header value. The JQ filter accepts any valid JSON identity header without binding the guard to a specific organization or user.

The tool sets `hosted-pulp-default-identity-check` as the domain
`default_content_guard` when the domain has no default. It does not replace a
conflicting domain default, assign the VPN guard, or assign the composite
guard. It excludes public-prefixed domains and distributions that already have
effective or explicit protection.

## Reconcile domain defaults

Use `--domain-defaults-only` to process the report's top-level domain inventory
without reading or changing distributions. This mode includes domains with no
distributions and excludes public-prefixed domains and the system `default`
domain.

Plan the reconciliation:

```bash
uv run management_tools/apply-identity-content-guards.py \
  --report /path/content-guards-stage-report.json \
  --profile stage-tbr \
  --domain-defaults-only \
  --output domain-defaults-plan.json
```

Apply after reviewing the plan:

```bash
uv run management_tools/apply-identity-content-guards.py \
  --report /path/content-guards-stage-report.json \
  --profile stage-tbr \
  --domain-defaults-only \
  --apply --yes \
  --output domain-defaults-result.json
```

The mode sets `default_content_guard` only when it is empty. It fails closed on
a conflicting default and records the prior domain default for rollback. With
`--partial-apply`, it defers unresolved audit domains, returns exit code `3`,
and records those domains as pending. It must not be combined with
`--max-changes`.

## Prerequisites

Before running the tool, verify that:

1. The `hosted-pulp` CLI is installed and the `stage-tbr` profile is configured.
2. The audit report was generated for stage and uses the current distribution endpoint scope.
3. The report is complete and error-free, or a complete supplemental report covers every failed domain.
4. The output directory is writable and does not expose credentials.

Validate the CLI profile without making a Pulp request:

```bash
hosted-pulp -p stage-tbr config show
```

Do not use the `stage` mTLS profile. The migration uses the `stage-tbr` profile.

## Plan a migration

The default mode performs reads and produces a plan. It does not create guards or update distributions.

```bash
uv run management_tools/apply-identity-content-guards.py \
  --report /path/content-guards-stage-report.json \
  --profile stage-tbr \
  --max-changes 9 \
  --output stage-update-plan.json \
  --allow-incomplete-report
```

The `--max-changes` value applies to the whole run, not to each domain. Omit it to plan all eligible distributions.

When the primary report is incomplete, planning can use a second report:

```bash
uv run management_tools/apply-identity-content-guards.py \
  --report /path/content-guards-stage-report.json \
  --report /path/content-guards-stage-missing-domains.json \
  --profile stage-tbr \
  --allow-incomplete-report \
  --output stage-update-plan.json
```

## Apply a complete migration

Apply mode requires explicit confirmation. A complete supplemental report must cover every failed domain in the primary report.

```bash
uv run management_tools/apply-identity-content-guards.py \
  --report /path/content-guards-stage-report.json \
  --report /path/content-guards-stage-missing-domains.json \
  --profile stage-tbr \
  --allow-incomplete-report \
  --apply --yes \
  --output stage-update.json
```

The tool performs live checks before each domain and distribution update. It
sets and verifies the domain default before assigning the identity guard to
existing distributions. It waits for the distribution to show the expected
identity guard and records the dispatch response, task reference, and final
state. A conflicting existing domain default stops that domain without
overwriting it.

## Apply a partial migration

Use partial apply when failed audit domains must be retried separately. Partial apply changes only distributions from successfully audited domains and leaves candidates in unresolved domains pending.

```bash
uv run management_tools/apply-identity-content-guards.py \
  --report /path/content-guards-stage-report.json \
  --profile stage-tbr \
  --partial-apply --apply --yes \
  --max-changes 9 \
  --output stage-update-partial.json
```

Partial apply returns exit code `3` when the selected operations succeed. This status does not mean that the full audit scope is complete. The output records unresolved domains, deferred candidates, and the next audit work.

## Progress and output

Human-readable progress is written to standard error. The final summary is written to standard output. The result file is updated atomically during the run and contains:

- report paths and SHA-256 digests;
- candidate, excluded, deferred, changed, skipped, and failed counts;
- per-domain guard actions for all three guards;
- per-distribution live validation and assignment results;
- unresolved audit domains and raw audit errors; and
- rollback information containing the prior explicit and effective guard state.

The tool uses up to eight concurrent live preflight reads. Guard creation and distribution assignment remain serialized per domain. Assignment polling backs off to a maximum five-second interval.

## Troubleshooting

### `incomplete or errored reports`

Use `--allow-incomplete-report` for planning, or use `--partial-apply --apply --yes` for an explicit incremental migration. For a complete apply, supply a healthy supplemental report that covers all failed domains.

### `task poll returned status 404`

The tool does not use the hosted-pulp task waiter for distribution updates. It dispatches the update and polls the distribution until the guard assignment is visible. A timeout leaves a `pending_review` rollback entry.

### `incompatible settings` or `incompatible members`

The tool found an existing guard with the expected name but different settings or members. Do not overwrite it automatically. Inspect the guard and resolve the conflict before rerunning the migration.

### `header_value=--jq-filter`

This indicates that an older command passed an empty header value as a separate CLI argument. The current tool uses the non-empty `identity-present` sentinel and does not repair malformed existing guards automatically.
