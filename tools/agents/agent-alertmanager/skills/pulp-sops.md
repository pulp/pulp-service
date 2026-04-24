# Pulp Alert SOPs

Standard Operating Procedures for Pulp alert investigation and response.

## PulpCrashing

Impact
------

The one or more Pulp components is restarting.

Summary
-------

This alert fires when any of the `pulp-api`,`pulp-content`, or `pulp-worker` pods are restarting.

Access required
---------------

- The production cluster to view the [pulp-prod-namespace]( https://console-openshift-console.apps.crcp01ue1.o9m8.p1.openshiftapps.com/k8s/ns/pulp-prod/).
- The stage cluster to view the [pulp-stage-namespace]( https://console-openshift-console.apps.crcs02ue1.urby.p1.openshiftapps.com/k8s/ns/pulp-stage/ ).
- The [pulp-stage logs]( https://grafana.app-sre.devshift.net/explore?schemaVersion=1&panes=%7B%22vse%22:%7B%22datasource%22:%22P1A97A9592CB7F392%22,%22queries%22:%5B%7B%22id%22:%22%22,%22region%22:%22us-east-1%22,%22namespace%22:%22%22,%22refId%22:%22A%22,%22queryMode%22:%22Logs%22,%22expression%22:%22fields%20@logStream,%20@message,%20%20kubernetes.namespace_name%20%7C%20filter%20@logStream%20like%20%5C%22var.log.pods.pulp-stage_pulp-%5C%22%5Cn%5Cn%5Cn%5Cn%22,%22statsGroups%22:%5B%5D,%22datasource%22:%7B%22type%22:%22cloudwatch%22,%22uid%22:%22P1A97A9592CB7F392%22%7D,%22logGroups%22:%5B%7B%22arn%22:%22arn:aws:logs:us-east-1:744086762512:log-group:crcs02ue1.pulp-stage:%2A%22,%22name%22:%22crcs02ue1.pulp-stage%22,%22accountId%22:%22744086762512%22%7D%5D%7D,%7B%22queryMode%22:%22Metrics%22,%22namespace%22:%22%22,%22metricName%22:%22%22,%22expression%22:%22%22,%22dimensions%22:%7B%7D,%22region%22:%22default%22,%22id%22:%22%22,%22statistic%22:%22Average%22,%22period%22:%22%22,%22metricQueryType%22:0,%22metricEditorMode%22:0,%22sqlExpression%22:%22%22,%22matchExact%22:true,%22refId%22:%22B%22,%22datasource%22:%7B%22type%22:%22cloudwatch%22,%22uid%22:%22P1A97A9592CB7F392%22%7D,%22label%22:%22%22%7D%5D,%22range%22:%7B%22from%22:%22now-30m%22,%22to%22:%22now%22%7D%7D%7D&orgId=1 ).
- The [pulp-prod logs]( https://grafana.app-sre.devshift.net/explore?schemaVersion=1&panes=%7B%22vse%22:%7B%22datasource%22:%22P1A97A9592CB7F392%22,%22queries%22:%5B%7B%22id%22:%22%22,%22region%22:%22us-east-1%22,%22namespace%22:%22%22,%22refId%22:%22A%22,%22queryMode%22:%22Logs%22,%22expression%22:%22fields%20@logStream,%20@message,%20%20kubernetes.namespace_name%20%7C%20filter%20@logStream%20like%20%5C%22var.log.pods.pulp-prod_pulp-%5C%22%5Cn%5Cn%5Cn%5Cn%22,%22statsGroups%22:%5B%5D,%22datasource%22:%7B%22type%22:%22cloudwatch%22,%22uid%22:%22P1A97A9592CB7F392%22%7D,%22logGroups%22:%5B%7B%22arn%22:%22arn:aws:logs:us-east-1:744086762512:log-group:crcp01ue1.pulp-prod:%2A%22,%22name%22:%22crcp01ue1.pulp-prod%22,%22accountId%22:%22744086762512%22%7D%5D%7D,%7B%22queryMode%22:%22Metrics%22,%22namespace%22:%22%22,%22metricName%22:%22%22,%22expression%22:%22%22,%22dimensions%22:%7B%7D,%22region%22:%22default%22,%22id%22:%22%22,%22statistic%22:%22Average%22,%22period%22:%22%22,%22metricQueryType%22:0,%22metricEditorMode%22:0,%22sqlExpression%22:%22%22,%22matchExact%22:true,%22refId%22:%22B%22,%22datasource%22:%7B%22type%22:%22cloudwatch%22,%22uid%22:%22P1A97A9592CB7F392%22%7D,%22label%22:%22%22%7D%5D,%22range%22:%7B%22from%22:%22now-30m%22,%22to%22:%22now%22%7D%7D%7D&orgId=1 ).
- The Pulp [Stage grafana]( https://grafana.stage.devshift.net/d/e50bb9f2-372c-4e94-aa61-fe1f1554812c/pulp-metrics?orgId=1&refresh=5s&var-datasource=PDD8BE47D10408F45 )
- The Pulp [Prod grafana]( https://grafana.app-sre.devshift.net/d/e50bb9f2-372c-4e94-aa61-fe1f1554812c/pulp-metrics?orgId=1&refresh=5s&var-datasource=PC1EAC84DCBBF0697 )

Steps
-----

- View the namespace, check pod console output to see what errors are produced before the restart.
- View events for the pods experiencing restarts. Identify if readiness or liveness probes are failing.
- View the dashboard to see the trend of errors.
- View the logs to identify specific errors.

Escalations
-----------

[Escalation policy](data/teams/pulp/escalation-policies/pulp.yml).

## PulpApiDown

Impact
------

The API server is down.  No REST API requests can be fulfilled.

Summary
-------

This alert fires when a `pulp-api` pods are no longer running.

Access required
---------------

- The production cluster to view the [pulp-prod-namespace]( https://console-openshift-console.apps.crcp01ue1.o9m8.p1.openshiftapps.com/k8s/ns/pulp-prod/).
- The stage cluster to view the [pulp-stage-namespace]( https://console-openshift-console.apps.crcs02ue1.urby.p1.openshiftapps.com/k8s/ns/pulp-stage/ ).
- The [pulp-stage logs]( https://grafana.app-sre.devshift.net/explore?schemaVersion=1&panes=%7B%22vse%22:%7B%22datasource%22:%22P1A97A9592CB7F392%22,%22queries%22:%5B%7B%22id%22:%22%22,%22region%22:%22us-east-1%22,%22namespace%22:%22%22,%22refId%22:%22A%22,%22queryMode%22:%22Logs%22,%22expression%22:%22fields%20@logStream,%20@message,%20%20kubernetes.namespace_name%20%7C%20filter%20@logStream%20like%20%5C%22var.log.pods.pulp-stage_pulp-api%5C%22%5Cn%5Cn%5Cn%5Cn%22,%22statsGroups%22:%5B%5D,%22datasource%22:%7B%22type%22:%22cloudwatch%22,%22uid%22:%22P1A97A9592CB7F392%22%7D,%22logGroups%22:%5B%7B%22arn%22:%22arn:aws:logs:us-east-1:744086762512:log-group:crcs02ue1.pulp-stage:%2A%22,%22name%22:%22crcs02ue1.pulp-stage%22,%22accountId%22:%22744086762512%22%7D%5D%7D,%7B%22queryMode%22:%22Metrics%22,%22namespace%22:%22%22,%22metricName%22:%22%22,%22expression%22:%22%22,%22dimensions%22:%7B%7D,%22region%22:%22default%22,%22id%22:%22%22,%22statistic%22:%22Average%22,%22period%22:%22%22,%22metricQueryType%22:0,%22metricEditorMode%22:0,%22sqlExpression%22:%22%22,%22matchExact%22:true,%22refId%22:%22B%22,%22datasource%22:%7B%22type%22:%22cloudwatch%22,%22uid%22:%22P1A97A9592CB7F392%22%7D,%22label%22:%22%22%7D%5D,%22range%22:%7B%22from%22:%22now-30m%22,%22to%22:%22now%22%7D%7D%7D&orgId=1 ).
- The [pulp-prod logs]( https://grafana.app-sre.devshift.net/explore?schemaVersion=1&panes=%7B%22vse%22:%7B%22datasource%22:%22P1A97A9592CB7F392%22,%22queries%22:%5B%7B%22id%22:%22%22,%22region%22:%22us-east-1%22,%22namespace%22:%22%22,%22refId%22:%22A%22,%22queryMode%22:%22Logs%22,%22expression%22:%22fields%20@logStream,%20@message,%20%20kubernetes.namespace_name%20%7C%20filter%20@logStream%20like%20%5C%22var.log.pods.pulp-prod_pulp-api%5C%22%5Cn%5Cn%5Cn%5Cn%22,%22statsGroups%22:%5B%5D,%22datasource%22:%7B%22type%22:%22cloudwatch%22,%22uid%22:%22P1A97A9592CB7F392%22%7D,%22logGroups%22:%5B%7B%22arn%22:%22arn:aws:logs:us-east-1:744086762512:log-group:crcp01ue1.pulp-prod:%2A%22,%22name%22:%22crcp01ue1.pulp-prod%22,%22accountId%22:%22744086762512%22%7D%5D%7D,%7B%22queryMode%22:%22Metrics%22,%22namespace%22:%22%22,%22metricName%22:%22%22,%22expression%22:%22%22,%22dimensions%22:%7B%7D,%22region%22:%22default%22,%22id%22:%22%22,%22statistic%22:%22Average%22,%22period%22:%22%22,%22metricQueryType%22:0,%22metricEditorMode%22:0,%22sqlExpression%22:%22%22,%22matchExact%22:true,%22refId%22:%22B%22,%22datasource%22:%7B%22type%22:%22cloudwatch%22,%22uid%22:%22P1A97A9592CB7F392%22%7D,%22label%22:%22%22%7D%5D,%22range%22:%7B%22from%22:%22now-30m%22,%22to%22:%22now%22%7D%7D%7D&orgId=1 ).
- The Pulp [Stage grafana]( https://grafana.stage.devshift.net/d/e50bb9f2-372c-4e94-aa61-fe1f1554812c/pulp-metrics?orgId=1&refresh=5s&var-datasource=PDD8BE47D10408F45 )
- The Pulp [Prod grafana]( https://grafana.app-sre.devshift.net/d/e50bb9f2-372c-4e94-aa61-fe1f1554812c/pulp-metrics?orgId=1&refresh=5s&var-datasource=PC1EAC84DCBBF0697 )

Steps
-----

- View the namespace, check pod console output to see if there are any messages when the pods are starting.
- View events for the pulp-api pods. Identify if readiness or liveness probes are failing.
- View the dashboard to see the trend of errors.
- View the logs to identify specific errors.

Escalations
-----------

[Escalation policy](data/teams/pulp/escalation-policies/pulp.yml).

## PulpContentDown

Impact
------

The Content server is down.  Client requests for packages can not be fulfilled.

Summary
-------

This alert fires when a `pulp-content` pods are no longer running.

Access required
---------------

- The production cluster to view the [pulp-prod-namespace]( https://console-openshift-console.apps.crcp01ue1.o9m8.p1.openshiftapps.com/k8s/ns/pulp-prod/).
- The stage cluster to view the [pulp-stage-namespace]( https://console-openshift-console.apps.crcs02ue1.urby.p1.openshiftapps.com/k8s/ns/pulp-stage/ ).
- The [pulp-stage logs]( https://grafana.app-sre.devshift.net/explore?schemaVersion=1&panes=%7B%22vse%22:%7B%22datasource%22:%22P1A97A9592CB7F392%22,%22queries%22:%5B%7B%22id%22:%22%22,%22region%22:%22us-east-1%22,%22namespace%22:%22%22,%22refId%22:%22A%22,%22queryMode%22:%22Logs%22,%22expression%22:%22fields%20@logStream,%20@message,%20%20kubernetes.namespace_name%20%7C%20filter%20@logStream%20like%20%5C%22var.log.pods.pulp-stage_pulp-content%5C%22%5Cn%5Cn%5Cn%5Cn%22,%22statsGroups%22:%5B%5D,%22datasource%22:%7B%22type%22:%22cloudwatch%22,%22uid%22:%22P1A97A9592CB7F392%22%7D,%22logGroups%22:%5B%7B%22arn%22:%22arn:aws:logs:us-east-1:744086762512:log-group:crcs02ue1.pulp-stage:%2A%22,%22name%22:%22crcs02ue1.pulp-stage%22,%22accountId%22:%22744086762512%22%7D%5D%7D,%7B%22queryMode%22:%22Metrics%22,%22namespace%22:%22%22,%22metricName%22:%22%22,%22expression%22:%22%22,%22dimensions%22:%7B%7D,%22region%22:%22default%22,%22id%22:%22%22,%22statistic%22:%22Average%22,%22period%22:%22%22,%22metricQueryType%22:0,%22metricEditorMode%22:0,%22sqlExpression%22:%22%22,%22matchExact%22:true,%22refId%22:%22B%22,%22datasource%22:%7B%22type%22:%22cloudwatch%22,%22uid%22:%22P1A97A9592CB7F392%22%7D,%22label%22:%22%22%7D%5D,%22range%22:%7B%22from%22:%22now-30m%22,%22to%22:%22now%22%7D%7D%7D&orgId=1 ).
- The [pulp-prod logs]( https://grafana.app-sre.devshift.net/explore?schemaVersion=1&panes=%7B%22vse%22:%7B%22datasource%22:%22P1A97A9592CB7F392%22,%22queries%22:%5B%7B%22id%22:%22%22,%22region%22:%22us-east-1%22,%22namespace%22:%22%22,%22refId%22:%22A%22,%22queryMode%22:%22Logs%22,%22expression%22:%22fields%20@logStream,%20@message,%20%20kubernetes.namespace_name%20%7C%20filter%20@logStream%20like%20%5C%22var.log.pods.pulp-prod_pulp-content%5C%22%5Cn%5Cn%5Cn%5Cn%22,%22statsGroups%22:%5B%5D,%22datasource%22:%7B%22type%22:%22cloudwatch%22,%22uid%22:%22P1A97A9592CB7F392%22%7D,%22logGroups%22:%5B%7B%22arn%22:%22arn:aws:logs:us-east-1:744086762512:log-group:crcp01ue1.pulp-prod:%2A%22,%22name%22:%22crcp01ue1.pulp-prod%22,%22accountId%22:%22744086762512%22%7D%5D%7D,%7B%22queryMode%22:%22Metrics%22,%22namespace%22:%22%22,%22metricName%22:%22%22,%22expression%22:%22%22,%22dimensions%22:%7B%7D,%22region%22:%22default%22,%22id%22:%22%22,%22statistic%22:%22Average%22,%22period%22:%22%22,%22metricQueryType%22:0,%22metricEditorMode%22:0,%22sqlExpression%22:%22%22,%22matchExact%22:true,%22refId%22:%22B%22,%22datasource%22:%7B%22type%22:%22cloudwatch%22,%22uid%22:%22P1A97A9592CB7F392%22%7D,%22label%22:%22%22%7D%5D,%22range%22:%7B%22from%22:%22now-30m%22,%22to%22:%22now%22%7D%7D%7D&orgId=1 ).
- The Pulp [Stage grafana]( https://grafana.stage.devshift.net/d/e50bb9f2-372c-4e94-aa61-fe1f1554812c/pulp-metrics?orgId=1&refresh=5s&var-datasource=PDD8BE47D10408F45 )
- The Pulp [Prod grafana]( https://grafana.app-sre.devshift.net/d/e50bb9f2-372c-4e94-aa61-fe1f1554812c/pulp-metrics?orgId=1&refresh=5s&var-datasource=PC1EAC84DCBBF0697 )

Steps
-----

- View the namespace, check pod console output to see if there are any messages when the pods are starting.
- View events for the pulp-content pods. Identify if readiness or liveness probes are failing.
- View the dashboard to see the trend of errors.
- View the logs to identify specific errors.

Escalations
-----------

[Escalation policy](data/teams/pulp/escalation-policies/pulp.yml).

## PulpWorkersDown

Impact
------

The workers are down. All asynchronous tasks such as repository syncing cannot be performed.

Summary
-------

This alert fires when a `pulp-worker` pods are no longer running.

Access required
---------------

- The production cluster to view the [pulp-prod-namespace]( https://console-openshift-console.apps.crcp01ue1.o9m8.p1.openshiftapps.com/k8s/ns/pulp-prod/).
- The stage cluster to view the [pulp-stage-namespace]( https://console-openshift-console.apps.crcs02ue1.urby.p1.openshiftapps.com/k8s/ns/pulp-stage/ ).
- The [pulp-stage logs]( https://grafana.app-sre.devshift.net/explore?schemaVersion=1&panes=%7B%22vse%22:%7B%22datasource%22:%22P1A97A9592CB7F392%22,%22queries%22:%5B%7B%22id%22:%22%22,%22region%22:%22us-east-1%22,%22namespace%22:%22%22,%22refId%22:%22A%22,%22queryMode%22:%22Logs%22,%22expression%22:%22fields%20@logStream,%20@message,%20%20kubernetes.namespace_name%20%7C%20filter%20@logStream%20like%20%5C%22var.log.pods.pulp-stage_pulp-worker%5C%22%5Cn%5Cn%5Cn%5Cn%22,%22statsGroups%22:%5B%5D,%22datasource%22:%7B%22type%22:%22cloudwatch%22,%22uid%22:%22P1A97A9592CB7F392%22%7D,%22logGroups%22:%5B%7B%22arn%22:%22arn:aws:logs:us-east-1:744086762512:log-group:crcs02ue1.pulp-stage:%2A%22,%22name%22:%22crcs02ue1.pulp-stage%22,%22accountId%22:%22744086762512%22%7D%5D%7D,%7B%22queryMode%22:%22Metrics%22,%22namespace%22:%22%22,%22metricName%22:%22%22,%22expression%22:%22%22,%22dimensions%22:%7B%7D,%22region%22:%22default%22,%22id%22:%22%22,%22statistic%22:%22Average%22,%22period%22:%22%22,%22metricQueryType%22:0,%22metricEditorMode%22:0,%22sqlExpression%22:%22%22,%22matchExact%22:true,%22refId%22:%22B%22,%22datasource%22:%7B%22type%22:%22cloudwatch%22,%22uid%22:%22P1A97A9592CB7F392%22%7D,%22label%22:%22%22%7D%5D,%22range%22:%7B%22from%22:%22now-30m%22,%22to%22:%22now%22%7D%7D%7D&orgId=1 ).
- The [pulp-prod logs]( https://grafana.app-sre.devshift.net/explore?schemaVersion=1&panes=%7B%22vse%22:%7B%22datasource%22:%22P1A97A9592CB7F392%22,%22queries%22:%5B%7B%22id%22:%22%22,%22region%22:%22us-east-1%22,%22namespace%22:%22%22,%22refId%22:%22A%22,%22queryMode%22:%22Logs%22,%22expression%22:%22fields%20@logStream,%20@message,%20%20kubernetes.namespace_name%20%7C%20filter%20@logStream%20like%20%5C%22var.log.pods.pulp-prod_pulp-worker%5C%22%5Cn%5Cn%5Cn%5Cn%22,%22statsGroups%22:%5B%5D,%22datasource%22:%7B%22type%22:%22cloudwatch%22,%22uid%22:%22P1A97A9592CB7F392%22%7D,%22logGroups%22:%5B%7B%22arn%22:%22arn:aws:logs:us-east-1:744086762512:log-group:crcp01ue1.pulp-prod:%2A%22,%22name%22:%22crcp01ue1.pulp-prod%22,%22accountId%22:%22744086762512%22%7D%5D%7D,%7B%22queryMode%22:%22Metrics%22,%22namespace%22:%22%22,%22metricName%22:%22%22,%22expression%22:%22%22,%22dimensions%22:%7B%7D,%22region%22:%22default%22,%22id%22:%22%22,%22statistic%22:%22Average%22,%22period%22:%22%22,%22metricQueryType%22:0,%22metricEditorMode%22:0,%22sqlExpression%22:%22%22,%22matchExact%22:true,%22refId%22:%22B%22,%22datasource%22:%7B%22type%22:%22cloudwatch%22,%22uid%22:%22P1A97A9592CB7F392%22%7D,%22label%22:%22%22%7D%5D,%22range%22:%7B%22from%22:%22now-30m%22,%22to%22:%22now%22%7D%7D%7D&orgId=1 ).
- The Pulp [Stage grafana]( https://grafana.stage.devshift.net/d/e50bb9f2-372c-4e94-aa61-fe1f1554812c/pulp-metrics?orgId=1&refresh=5s&var-datasource=PDD8BE47D10408F45 )
- The Pulp [Prod grafana]( https://grafana.app-sre.devshift.net/d/e50bb9f2-372c-4e94-aa61-fe1f1554812c/pulp-metrics?orgId=1&refresh=5s&var-datasource=PC1EAC84DCBBF0697 )

Steps
-----

- View the namespace, check pod console output to see if there are any messages when the pods are starting.
- View events for the pulp-worker pods. Identify if readiness or liveness probes are failing.
- View the dashboard to see the trend of errors.
- View the logs to identify specific errors.

Escalations
-----------

[Escalation policy](data/teams/pulp/escalation-policies/pulp.yml).

## PulpProdServiceRDSLowStorageSpace

Impact
------

The database is running out of space. Any repository operation will start to fail.

Summary
-------

This alert fires when the production database have less than 750GB available.
Note: The database have Storage AutoScaling enabled. The `max_allocated_storage` must be at least
10% higher than the `allocated_storage` to be enabled.

Access required
---------------

- The production cluster to view the [pulp-prod-namespace]( https://console-openshift-console.apps.crcp01ue1.o9m8.p1.openshiftapps.com/k8s/ns/pulp-prod/).
- The [pulp-prod logs]( https://grafana.app-sre.devshift.net/explore?schemaVersion=1&panes=%7B%22vse%22:%7B%22datasource%22:%22P1A97A9592CB7F392%22,%22queries%22:%5B%7B%22id%22:%22%22,%22region%22:%22us-east-1%22,%22namespace%22:%22%22,%22refId%22:%22A%22,%22queryMode%22:%22Logs%22,%22expression%22:%22fields%20@logStream,%20@message,%20%20kubernetes.namespace_name%20%7C%20filter%20@logStream%20like%20%5C%22var.log.pods.pulp-prod_pulp-worker%5C%22%5Cn%5Cn%5Cn%5Cn%22,%22statsGroups%22:%5B%5D,%22datasource%22:%7B%22type%22:%22cloudwatch%22,%22uid%22:%22P1A97A9592CB7F392%22%7D,%22logGroups%22:%5B%7B%22arn%22:%22arn:aws:logs:us-east-1:744086762512:log-group:crcp01ue1.pulp-prod:%2A%22,%22name%22:%22crcp01ue1.pulp-prod%22,%22accountId%22:%22744086762512%22%7D%5D%7D,%7B%22queryMode%22:%22Metrics%22,%22namespace%22:%22%22,%22metricName%22:%22%22,%22expression%22:%22%22,%22dimensions%22:%7B%7D,%22region%22:%22default%22,%22id%22:%22%22,%22statistic%22:%22Average%22,%22period%22:%22%22,%22metricQueryType%22:0,%22metricEditorMode%22:0,%22sqlExpression%22:%22%22,%22matchExact%22:true,%22refId%22:%22B%22,%22datasource%22:%7B%22type%22:%22cloudwatch%22,%22uid%22:%22P1A97A9592CB7F392%22%7D,%22label%22:%22%22%7D%5D,%22range%22:%7B%22from%22:%22now-30m%22,%22to%22:%22now%22%7D%7D%7D&orgId=1 ).
- The Pulp [Prod grafana]( https://grafana.app-sre.devshift.net/d/e50bb9f2-372c-4e94-aa61-fe1f1554812c/pulp-metrics?orgId=1&refresh=5s&var-datasource=PC1EAC84DCBBF0697 )

Steps
-----

- Check the Pulp Troubleshooting Dashboard and verify the space available.
- Add 50% more storage to the `max_allocated_storage` parameter [here](https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/resources/terraform/resources/pulp/production/postgres16-rds-1.yml).
- Confirm it was properly applied.
- Check the Pulp Troubleshooting Dashboard again to verify the new space available.

Escalations
-----------

[Escalation policy](data/teams/pulp/escalation-policies/pulp.yml).
