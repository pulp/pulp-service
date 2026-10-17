---
name: platform-ops
description: How the RH platform fits together on OpenShift and the exact moves gm-rh uses most: the repos, how a change ships (a definition commit vs an image vs an app-interface MR), the clones, and gm-post. Use before any change to rh/cluster, rh/agents, or anything you stage for pulp-service or app-interface.
---

## The pieces
- rh/cluster (yours, on Gitea, you push): `render/pulp-service/` is your staging tree for the live platform. `render/pulp-service/.buzz/agents/<slug>/` holds an agent's git definition (agent.yaml, charter.md, heartbeat.md, mcp.json, skills/); `render/pulp-service/.buzz/images/buzz-agent-<slug>/` holds that agent's image (Containerfile + baked bin/ and config). `render/` also holds staged app-interface MR content (SaaS targets, vault-secret entries, NetworkPolicies). The interim serrano world still lives under `deploy/`, `images/` and `<ns>/secrets/*.sops.yaml` and is retired as each agent moves. You push to main; a documentation file or a digest bump that finishes an approved change is act-and-report, everything else needs a yes first.
- rh/agents (cos-rh's, on Gitea): staff notes, agent definitions and identities. Its protected areas (tools/, base-image/, .gitea/, secrets/) need a yes. You, as admin collaborator, lift and restore protection to change them when approved.
- pulp/pulp-service (Red Hat's, on GitHub, read only for you): the live source. At pod start each agent clones its `.buzz/agents/<slug>/` tree from here, and an in-pod watcher (WATCH_SECONDS=300) restarts buzz-acp when the definition commit changes. So a charter, heartbeat, skill, allowlist or agent.yaml change is a commit to `.buzz/agents/<slug>/` here and takes effect within about five minutes, no app-interface MR and no pod recreate. You clone it over HTTPS to read and diff; you never push. An approver carries your staged change from rh/cluster into pulp-service as a PR.
- service/app-interface (Red Hat's, on GitLab, no token in this image): declares the namespace, the vault-secret sources, the SaaS file (one target per agent, pinned image tags) and any NetworkPolicy. A new image, new agent, secret or resource change is an app-interface MR, reviewed by AppSRE. You stage the MR content under render/ and an approver files it from the Mac. Never self service.
- Images: per agent on ghcr.io/pulp (buzz-agent-<slug> = the shared ghcr.io/pulp/buzz-agent-base plus that agent's baked tamper-proof half, model C), built by pulp-service CI and pinned by tag in the SaaS target. An image change (a baked tool, goals.md, the approver list, a base bump) is an image rebuild and a SaaS tag bump, both staged then carried over by an approver.

## How a change ships, in one line each
- Charter / heartbeat / skill / allowlist / agent.yaml: stage in rh/cluster render/, propose, on a yes an approver PRs it into pulp-service .buzz/; the watcher restarts buzz-acp.
- A baked tool, goals.md, the approver list, a base bump (image): stage the Containerfile/bin/config change, propose, on a yes an approver PRs the image and files the SaaS tag bump MR.
- A credential, a namespace, a resource, a NetworkPolicy: stage the MR content, propose, on a yes an approver files the app-interface MR (see the credentials skill for Vault).

## Clones
Gitea HTTPS auth is provisioned at pod start (the entrypoint writes `$HOME/.git-credentials` from the Vault mounted token), so git pull/push to the two Gitea clones just works. The session never reads the token itself: secret-guard blocks any command that mentions the secret mount or `.git-credentials`. If a clone is missing, recreate it:
```
git clone https://gitea.bouterse.casa/rh/cluster.git /state/repos/cluster
git clone https://gitea.bouterse.casa/rh/agents.git /state/repos/agents
```
To read pulp-service (public, no auth): `git clone https://github.com/pulp/pulp-service.git /state/repos/pulp-service`. Never put a token in a remote URL and never cat it into a command: use `git` (it reads the stored credential) or `gitea-api` for Gitea API and registry calls. Pull before each task.

## Posting (gm-post)
Every message you send goes through `gm-post`, never raw `buzz messages send`. The body comes on stdin as a quoted heredoc; pass `--channel`, `--reply-to` and one `--mention <hex>` per person to notify:
```
gm-post --channel <uuid> --reply-to <root> --mention <hex> <<'MSG'
@Name ...
MSG
```
It refuses before sending (exit 1, nothing sent) a backticked mention, an @Name that matches no `--mention` display name, an empty body or a bad pubkey, and after sending checks `accepted` is true and every mention landed, exiting non-zero otherwise (exit 2 nothing sent, exit 3 sent but unverified: read the channel before retrying). On success it prints `gm-post: sent <event id> accepted, mentions <n>`. gm-backtick-scan stays as the after-the-fact net for the chief's and agents' posts, which gm-post does not guard.

## What you cannot do here, and what to do instead
You have no OpenShift API (no oc, no kubectl), no Flux, no Vault write and no GitLab token. Pod readiness, the SaaS rollout, Routes, TLS and in-pod state are an approver's to check with oc from the Mac; say so plainly rather than guess (gm-status reports only what you can reach: each agent's liveness on the relay, the repos and CI). A pod that needs a restart on OpenShift is the in-pod watcher's job (on a definition commit) or an approver's `oc` action, not yours. Done means verified by something you can actually read: a rendered file, a commit pushed and its CI green, an agent answering on the relay, a registry manifest present. Name that evidence in your done reply; never call a change live on the strength of a merge you cannot see land.
