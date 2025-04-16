# Worker Scalability Test Plan

<!--toc:start-->
- [Worker Scalability Test Plan](#worker-scalability-test-plan)
  - [Current State](#current-state)
  - [Metrics to follow](#metrics-to-follow)
  - [Test Plan](#test-plan)
  - [Results](#results)
<!--toc:end-->

The workers have a permanent connection to the database and keep sending [heartbeats]()
as signal that they are alive and ready to accomplish a task.
The idea of this test plan is to verify the maximum number of workers before their 
heartbeat starts to timeout.

## Current State
We're gonna use the Pulp stage instance to run those tests. Each pulp-worker pod requests 
2GiB of memory, limited but not guaranteed to 6GiB and have access to 250m of CPU, limited 
but not guaranteed to 2 CPUs.

For database, we're using the AWS RDS PostgreSQL 16, on a db.m7g.2xlarge instance class.
In theory, the maximum number of connections is defined by `LEAST({DBInstanceClassMemory/9531392}, 5000)`
as written [here](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_Limits.html#RDS_Limits.MaxConnections)

## Metrics to follow
On database level:
- Active Connections
- Connection wait time
- Slow queries
- Lock contention - `SELECT * FROM pg_locks WHERE granted = false;`
- CPU and Memory utilization

Most of those metrics can be checked [here](https://us-east-1.console.aws.amazon.com/rds/home?region=us-east-1#database:id=pulp-prod;is-cluster=false)

For the application, we need to follow the timeouts using the logs.
You can start [here](https://grafana.app-sre.devshift.net/explore?schemaVersion=1&panes=%7B%22vse%22%3A%7B%22datasource%22%3A%22P1A97A9592CB7F392%22%2C%22queries%22%3A%5B%7B%22id%22%3A%22%22%2C%22region%22%3A%22us-east-1%22%2C%22namespace%22%3A%22%22%2C%22refId%22%3A%22A%22%2C%22queryMode%22%3A%22Logs%22%2C%22expression%22%3A%22fields+%40logStream%2C+%40message%2C++kubernetes.namespace_name+%7C+filter+%40logStream+like+%2Fpulp-stage_pulp-%28worker%7Capi%7Ccontent%29%2F%5Cn%5Cn%5Cn%5Cn%22%2C%22statsGroups%22%3A%5B%5D%2C%22datasource%22%3A%7B%22type%22%3A%22cloudwatch%22%2C%22uid%22%3A%22P1A97A9592CB7F392%22%7D%2C%22logGroups%22%3A%5B%7B%22arn%22%3A%22arn%3Aaws%3Alogs%3Aus-east-1%3A744086762512%3Alog-group%3Acrcs02ue1.pulp-stage%3A*%22%2C%22name%22%3A%22crcs02ue1.pulp-stage%22%2C%22accountId%22%3A%22744086762512%22%7D%5D%7D%5D%2C%22range%22%3A%7B%22from%22%3A%22now-30m%22%2C%22to%22%3A%22now%22%7D%7D%7D&orgId=1)

## Test Plan

1. Warn the pulp stakeholders (@pulp-service-stakeholders) that a test gonna happen in stage, so some instability is expected.
2. Create a MR increasing the number of pulp-workers [here](https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/data/services/pulp/deploy.yml#L75).
It can be approved by anyone from the team. Just need to wait for the app-sre bot to merge it.
3. After it got merged, check for the database metrics and application logs. The results from the run will be down in this document.
4. When the test is done, you need a new MR reducing the number of workers to 0 (zero) and then a new MR returning the number to it's usual state (2 pulp-workers in staging)
5. Send a new message to the Pulp Stakeholders (@pulp-service-stakeholders) saying that the test is concluded.

## Results
To be added in the future.

Date of the test: YYYY/mm/dd

A possible template for the table could be:

| Instance Count | DB CPU (%) | DB Memory (%) | Active Connections | Connection Wait (%) | Errors/Sec | Bottleneck Identified            |
|----------------|------------|---------------|--------------------|---------------------|------------|-----------------------------------|
| 20             | {{value}}  | {{value}}     | {{value}}          | {{value}}           | {{value}}  | `None`                            |
| 40             | {{value}}  | {{value}}     | {{value}}          | {{value}}           | {{value}}  | `CPU Saturation`                  |
| 60             | {{value}}  | {{value}}     | {{value}}          | {{value}}           | {{value}}  | `Max Connections`                 |
| 80             | {{value}}  | {{value}}     | {{value}}          | {{value}}           | {{value}}  | `Connection Pool Exhaustion`      |

**Key:**
- **Connection Wait (%)**: Percentage of time connections spent waiting for database resources.
- **Errors/Sec**: Rate of `5xx` errors or connection failures.
- Use `-` for unavailable metrics.
