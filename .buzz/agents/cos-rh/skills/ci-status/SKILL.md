---
name: ci-status
description: Recent Gitea Actions runs for the rh/agents and rh/cluster repos. Use for CI and deploy questions and on each heartbeat.
---

Your gitea token (account cos-rh, read only) is mounted as a per-agent vault-secret and its path is in the `GITEA_TOKEN_FILE` environment variable. If `GITEA_TOKEN_FILE` is empty or the file is missing, the token is not provisioned yet: report that CI is not available this run and stop, do not improvise. Egress on OpenShift is direct (no allowlist), but reaching gitea.bouterse.casa from the cluster is not yet confirmed; if curl cannot reach it, say so and stop. Never print the token.

```bash
[ -n "${GITEA_TOKEN_FILE:-}" ] && [ -r "$GITEA_TOKEN_FILE" ] || { echo "ci-status: no gitea token mounted (GITEA_TOKEN_FILE), CI not available"; exit 0; }
G=https://gitea.bouterse.casa/api/v1/repos/rh
for r in agents cluster; do
  curl -s -H "Authorization: token $(cat "$GITEA_TOKEN_FILE")" "$G/$r/actions/runs?limit=5" \
    | jq -r --arg r "$r" '.workflow_runs[]? | "\($r) #\(.run_number) \(.status) \(.conclusion // "-") \(.display_title // .head_sha[0:7]) \(.started_at)"'
done
```

Status is `completed`/`in_progress`; conclusion is `success`, `failure`, `cancelled` or `-` while running. A failed run in rh/agents leaves the previously deployed agents running once agents are on-cluster; say which commit failed. You cannot read Flux or pod state (no Kubernetes access), so the deployed state check from the Personal version does not apply to you; report only what Gitea shows.
