# Pulp PrometheusRule Alert Definitions

Alert definitions from app-interface PrometheusRules for Pulp services.

## Production Alerts

### PulpApiDown
- **Severity:** critical
- **For:** 5m
- **Expression:** `sum(up{service="pulp-api-svc", namespace="pulp-prod"}) == 0`
- **Message:** pulp-api pod down for 5 minutes.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/PulpApiDown.md

### PulpContentDown
- **Severity:** critical
- **For:** 1m
- **Expression:** `sum(up{service="pulp-content-svc", namespace="pulp-prod"}) == 0`
- **Message:** pulp-content pod down for 1 minute.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/PulpContentDown.md

### PulpWorkerDown
- **Severity:** critical
- **For:** 5m
- **Expression:** `sum(up{service="pulp-worker", namespace="pulp-prod"}) == 0`
- **Message:** pulp-worker pod down for 5 minutes.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/PulpWorkersDown.md

### PulpOOMKilled
- **Severity:** medium
- **For:** 1m
- **Expression:** `kube_pod_container_status_last_terminated_reason{namespace="pulp-prod", pod=~"^pulp-.*$", reason="OOMKilled"} == 1`
- **Message:** Pulp container '{{ $labels.container }}' of pod '{{ $labels.pod }}' is OOMKilled
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/PulpCrashing.md

### PulpCrashing
- **Severity:** medium
- **For:** 1m
- **Expression:** `sum(increase(kube_pod_container_status_restarts_total{namespace="pulp-prod",pod=~"^pulp-.*$"}[5m])) by (pod,container) >= 1`
- **Message:** Pulp container '{{ $labels.container }}' of pod '{{ $labels.pod }}' is crashing
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/PulpCrashing.md

### PulpApiError1hrBudgetBurn
- **Severity:** medium
- **For:** (not specified)
- **Expression:** `pulp_api_errors_bucket:rate1h > (6.72 * 0.05) and pulp_api_errors_bucket:rate5m > (6.72 * 0.05)`
- **Message:** High Pulp Production API requests errors: {{ $value | humanizePercentage }} within last 1 hour.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/ApiSuccessRateSloDetails.md

### PulpApiError3hrBudgetBurn
- **Severity:** medium
- **For:** (not specified)
- **Expression:** `pulp_api_errors_bucket:rate3h > (4.48 * 0.05) and pulp_api_errors_bucket:rate15m > (4.48 * 0.05)`
- **Message:** High Pulp Production API requests errors: {{ $value | humanizePercentage }} within last 3 hours.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/ApiSuccessRateSloDetails.md

### PulpApiError12hrBudgetBurn
- **Severity:** medium
- **For:** (not specified)
- **Expression:** `pulp_api_errors_bucket:rate12h > (2.8 * 0.05) and pulp_api_errors_bucket:rate1h > (2.8 * 0.05)`
- **Message:** High Pulp Production API requests errors: {{ $value | humanizePercentage }} within last 12 hours.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/ApiSuccessRateSloDetails.md

### PulpApiError1dBudgetBurn
- **Severity:** medium
- **For:** (not specified)
- **Expression:** `pulp_api_errors_bucket:rate1d > (2.1 * 0.05) and pulp_api_errors_bucket:rate2h > (2.1 * 0.05)`
- **Message:** High Pulp Production API requests errors: {{ $value | humanizePercentage }} within last 1 day.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/ApiSuccessRateSloDetails.md

### PulpApiError3dBudgetBurn
- **Severity:** medium
- **For:** (not specified)
- **Expression:** `pulp_api_errors_bucket:rate3d > (1.87 * 0.05) and pulp_api_errors_bucket:rate6h > (1.87 * 0.05)`
- **Message:** High Pulp Production API requests errors: {{ $value | humanizePercentage }} within last 3 days.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/ApiSuccessRateSloDetails.md

### PulpContentError1hrBudgetBurn
- **Severity:** medium
- **For:** (not specified)
- **Expression:** `pulp_content_errors_bucket:rate1h > (6.72 * 0.05) and pulp_content_errors_bucket:rate5m > (6.72 * 0.05)`
- **Message:** High Pulp Production content requests errors: {{ $value | humanizePercentage }} within last 1 hour.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/ContentSuccessRateSloDetails.md

### PulpContentError3hrBudgetBurn
- **Severity:** medium
- **For:** (not specified)
- **Expression:** `pulp_content_errors_bucket:rate3h > (4.48 * 0.05) and pulp_content_errors_bucket:rate15m > (4.48 * 0.05)`
- **Message:** High Pulp Production content requests errors: {{ $value | humanizePercentage }} within last 3 hours.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/ContentSuccessRateSloDetails.md

### PulpContentError12hrBudgetBurn
- **Severity:** medium
- **For:** (not specified)
- **Expression:** `pulp_content_errors_bucket:rate12h > (2.8 * 0.05) and pulp_content_errors_bucket:rate1h > (2.8 * 0.05)`
- **Message:** High Pulp Production content requests errors: {{ $value | humanizePercentage }} within last 12 hours.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/ContentSuccessRateSloDetails.md

### PulpContentError1dBudgetBurn
- **Severity:** medium
- **For:** (not specified)
- **Expression:** `pulp_content_errors_bucket:rate1d > (2.1 * 0.05) and pulp_content_errors_bucket:rate2h > (2.1 * 0.05)`
- **Message:** High Pulp Production content requests errors: {{ $value | humanizePercentage }} within last 1 day.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/ContentSuccessRateSloDetails.md

### PulpContentError3dBudgetBurn
- **Severity:** medium
- **For:** (not specified)
- **Expression:** `pulp_content_errors_bucket:rate3d > (1.87 * 0.05) and pulp_content_errors_bucket:rate6h > (1.87 * 0.05)`
- **Message:** High Pulp Production content requests errors: {{ $value | humanizePercentage }} within last 3 days.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/ContentSuccessRateSloDetails.md

## Production Database Alerts

### PulpProdServiceRDSLowStorageSpace
- **Severity:** critical
- **For:** 1h
- **Expression:** `aws_rds_free_storage_space_average{job=~"cloudwatch-exporter.*", dbinstance_identifier="pulp-prod"} offset 10m < (750 * 1024^3)`
- **Message:** The current free storage space of DB instance {{ $labels.dbinstance_identifier }} is under 750 GB.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/PulpProdServiceRDSLowStorageSpace.md
- **Dashboard:** https://grafana.devshift.net/d/de3tjlxrsxr7kf/pulp-troubleshooting

## Stage Alerts

### PulpApiDown
- **Severity:** medium
- **For:** 5m
- **Expression:** `sum(up{service="pulp-api-svc", namespace="pulp-stage"}) == 0`
- **Message:** pulp-api pod down for 5 minutes.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/PulpApiDown.md

### PulpContentDown
- **Severity:** medium
- **For:** 1m
- **Expression:** `sum(up{service="pulp-content-svc", namespace="pulp-stage"}) == 0`
- **Message:** pulp-content pod down for 1 minute.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/PulpContentDown.md

### PulpWorkerDown
- **Severity:** medium
- **For:** 5m
- **Expression:** `sum(up{service="pulp-worker", namespace="pulp-stage"}) == 0`
- **Message:** pulp-worker pod down for 5 minutes.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/PulpWorkersDown.md

### PulpOOMKilled
- **Severity:** medium
- **For:** 1m
- **Expression:** `kube_pod_container_status_last_terminated_reason{namespace="pulp-stage", pod=~"^pulp-.*$", reason="OOMKilled"} == 1`
- **Message:** Pulp container '{{ $labels.container }}' of pod '{{ $labels.pod }}' is OOMKilled
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/PulpCrashing.md

### PulpCrashing
- **Severity:** medium
- **For:** 1m
- **Expression:** `sum(increase(kube_pod_container_status_restarts_total{namespace="pulp-stage",pod=~"^pulp-.*$"}[5m])) by (pod,container) >= 1`
- **Message:** Pulp container '{{ $labels.container }}' of pod '{{ $labels.pod }}' is crashing
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/PulpCrashing.md

### PulpApiError1hrBudgetBurn
- **Severity:** medium
- **For:** (not specified)
- **Expression:** `pulp_api_errors_bucket:rate1h > (6.72 * 0.05) and pulp_api_errors_bucket:rate5m > (6.72 * 0.05)`
- **Message:** High Pulp Stage API requests errors: {{ $value | humanizePercentage }} within last 1 hour.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/ApiSuccessRateSloDetails.md

### PulpApiError3hrBudgetBurn
- **Severity:** medium
- **For:** (not specified)
- **Expression:** `pulp_api_errors_bucket:rate3h > (4.48 * 0.05) and pulp_api_errors_bucket:rate15m > (4.48 * 0.05)`
- **Message:** High Pulp Stage API requests errors: {{ $value | humanizePercentage }} within last 3 hours.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/ApiSuccessRateSloDetails.md

### PulpApiError12hrBudgetBurn
- **Severity:** medium
- **For:** (not specified)
- **Expression:** `pulp_api_errors_bucket:rate12h > (2.8 * 0.05) and pulp_api_errors_bucket:rate1h > (2.8 * 0.05)`
- **Message:** High Pulp Stage API requests errors: {{ $value | humanizePercentage }} within last 12 hours.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/ApiSuccessRateSloDetails.md

### PulpApiError1dBudgetBurn
- **Severity:** medium
- **For:** (not specified)
- **Expression:** `pulp_api_errors_bucket:rate1d > (2.1 * 0.05) and pulp_api_errors_bucket:rate2h > (2.1 * 0.05)`
- **Message:** High Pulp Stage API requests errors: {{ $value | humanizePercentage }} within last 1 day.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/ApiSuccessRateSloDetails.md

### PulpApiError3dBudgetBurn
- **Severity:** medium
- **For:** (not specified)
- **Expression:** `pulp_api_errors_bucket:rate3d > (1.87 * 0.05) and pulp_api_errors_bucket:rate6h > (1.87 * 0.05)`
- **Message:** High Pulp Stage API requests errors: {{ $value | humanizePercentage }} within last 3 days.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/ApiSuccessRateSloDetails.md

### PulpContentError1hrBudgetBurn
- **Severity:** medium
- **For:** (not specified)
- **Expression:** `pulp_content_errors_bucket:rate1h > (6.72 * 0.05) and pulp_content_errors_bucket:rate5m > (6.72 * 0.05)`
- **Message:** High Pulp Stage content requests errors: {{ $value | humanizePercentage }} within last 1 hour.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/ContentSuccessRateSloDetails.md

### PulpContentError3hrBudgetBurn
- **Severity:** medium
- **For:** (not specified)
- **Expression:** `pulp_content_errors_bucket:rate3h > (4.48 * 0.05) and pulp_content_errors_bucket:rate15m > (4.48 * 0.05)`
- **Message:** High Pulp Stage content requests errors: {{ $value | humanizePercentage }} within last 3 hours.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/ContentSuccessRateSloDetails.md

### PulpContentError12hrBudgetBurn
- **Severity:** medium
- **For:** (not specified)
- **Expression:** `pulp_content_errors_bucket:rate12h > (2.8 * 0.05) and pulp_content_errors_bucket:rate1h > (2.8 * 0.05)`
- **Message:** High Pulp Stage content requests errors: {{ $value | humanizePercentage }} within last 12 hours.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/ContentSuccessRateSloDetails.md

### PulpContentError1dBudgetBurn
- **Severity:** medium
- **For:** (not specified)
- **Expression:** `pulp_content_errors_bucket:rate1d > (2.1 * 0.05) and pulp_content_errors_bucket:rate2h > (2.1 * 0.05)`
- **Message:** High Pulp Stage content requests errors: {{ $value | humanizePercentage }} within last 1 day.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/ContentSuccessRateSloDetails.md

### PulpContentError3dBudgetBurn
- **Severity:** medium
- **For:** (not specified)
- **Expression:** `pulp_content_errors_bucket:rate3d > (1.87 * 0.05) and pulp_content_errors_bucket:rate6h > (1.87 * 0.05)`
- **Message:** High Pulp Stage content requests errors: {{ $value | humanizePercentage }} within last 3 days.
- **Runbook:** https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/docs/tenant-services/pulp/sop/ContentSuccessRateSloDetails.md
