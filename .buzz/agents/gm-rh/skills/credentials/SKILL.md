---
name: credentials
description: How an RH credential reaches a pod on OpenShift (a Red Hat Vault path, mounted by an app-interface vault-secret entry) and how gm-rh names, mounts, rolls and rotates one without ever holding its value. Use for every credential task.
---

## Where RH credentials live
- For good: Red Hat Vault, path `app-interface/pulp/stage/agents/<slug>`, mounted into the OpenShift pod by a `vault-secret` entry in the namespace file under service/app-interface (see the OpenShift section of the charter). Only Brian or Brian's Claude can write there, from the Mac on the Red Hat VPN. You never read or write Vault; you have no Vault access and need none.
- The shared Vertex service account for all RH agents: `app-interface/pulp/stage/agents/vertex-ai`, key `application_default_credentials.json` (the ADC JSON), billed to Red Hat's Vertex project.
- Record of what exists: rh/cluster. The namespace file's `vault-secret` entry (staged under render/) is the catalogue entry; its comment says what the credential is for and which pod mounts it. The value is only ever in Vault, never in any repo, message or file.

## Taking a credential in
1. Ask an approver in #staff for the credential by name and purpose, never for its value in chat. The approver writes it to Vault at `app-interface/pulp/stage/agents/<slug>` from the Mac and tells you the path and keys exist.
2. You never hold the value. If a step would mint something you would hold (see below), it is still the approver who writes it into Vault; you only name where it goes.
3. Stage the `vault-secret` entry (the Vault path, the keys, `qontract.recycle: true` so the pod restarts on change) and the pod's mount in the namespace file under render/, propose it in the thread, and on a yes an approver files the app-interface MR.
4. After the MR merges and the pod restarts, verify from the relay or a harmless in-pod read (whatever you can reach), and say what is now available, never the value.

## Minting
- You have no Gitea admin, no Vault write and no GitLab token in this image, so you mint nothing on OpenShift that becomes a live credential. A Gitea token for an RH account, a Buzz identity (nsec) for a new agent, or a registry login is minted by an approver (Brian, or Brian's Claude from the Mac or on serrano with the Gitea admin) straight into Vault, never printed.
- Your own Gitea access is the `gitea-cos-rh`/`gitea-gm-rh` style token mounted from Vault at pod start; the entrypoint writes it to `$HOME/.git-credentials`. You never read it (secret-guard blocks any command naming `.git-credentials` or the secret mount); you use `git` and `gitea-api`, which read the stored credential for you.

## Rotating
- An approver writes the new value to Vault, then a one line app-interface MR bumps the pinned `version` in the namespace file's `vault-secret` entry (it pins a version, it does not auto track latest); `qontract.recycle` restarts the pod. You stage the version bump under render/, propose it, and on a yes the approver files it. After the pod restarts, verify and say so. Revoking the old value at its source (Gitea, Google, the relay) is the approver's step; name it in your done reply so it is not forgotten.
