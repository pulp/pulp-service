# Goals for the RH GM (priority order, top first; Brian's yes edits this file)

- staff-move: cos-rh, then gm-rh, then auditor-rh run on hcmais01ue1 the same way, and the interim serrano pods are retired. cos-rh is first on OpenShift: before its serrano copy goes to zero, prove SIGTERM drains an in-flight turn (gate 1, not proven on Echo, which has no preStop) and that a mention after several idle hours still wakes the pod. Outcome goal.
- determinism: convert LLM instructions that are really procedures into deterministic, verifiable code where it makes sense (swamp where it earns its place). Standing goal.
- cost-metering: per-agent cost and transcript metering for RH agents. Gated on PulpBot's cutover onto OpenShift; it starts once PulpBot moves. Split out of cos-heartbeat (below). Outcome goal.

# Met and removed
- cos-heartbeat (met 2026-10-20): cos-rh runs the full hourly heartbeat on its OpenShift pod, verified end to end (CI/fleet, the 48h #staff scan, the approved-commit push check, and the daily review). Evidence: cos-rh daily report feb88f21 (relayed at 85a7beaa), summarized in #gm post b6dcce5b. Removed from the active list on Brian's yes (#gm 793be4a8, 2026-10-20). The remaining cost and transcript metering gap moved to the cost-metering line above.
