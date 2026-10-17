# Goals for the RH GM (priority order, top first; Brian's yes edits this file)

- cos-heartbeat: cos-rh has the same hourly heartbeat as the Personal CoS (fleet changes, daily summary and daily review). Outcome goal.
- staff-move: cos-rh, then gm-rh, then auditor-rh run on hcmais01ue1 the same way, and the interim serrano pods are retired. cos-rh is first on OpenShift: before its serrano copy goes to zero, prove SIGTERM drains an in-flight turn (gate 1, not proven on Echo, which has no preStop) and that a mention after several idle hours still wakes the pod. Outcome goal.
- determinism: convert LLM instructions that are really procedures into deterministic, verifiable code where it makes sense (swamp where it earns its place). Standing goal.
