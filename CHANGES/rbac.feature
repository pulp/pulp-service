Added RBAC permissions to pulp-service model viewsets (VulnerabilityReport, PyPIYankMonitor,
FeatureContentGuard, TaskViewSet, CreateDomainView, MigrateDomainView) using pulpcore's standard
AccessPolicy framework layered on top of the existing DomainBasedPermission.
