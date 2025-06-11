# Task Throughput Test Plan

<!--toc:start-->
- [Task Throughput Test Plan](#task-throughput-test-plan)
  - [Current State](#current-state)
  - [Metrics to follow](#metrics-to-follow)
  - [Test Plan](#test-plan)
  - [Results](#results)
  - [An example to calculate the number of tasks ingested](#an-example-to-calculate-the-number-of-tasks-ingested)
<!--toc:end-->

The idea of this test is to understand the load that Pulp imposes on hardware and database 
resources and specifically measure the rate of task completion under various load conditions.

## Current State
We're gonna use the perf cluster to run those tests, under the pulp-perf namespace.
Each pulp-api pod should replicate the same resource configuration from pulp-stage 
on stage cluster.

For database, we're using the AWS RDS PostgreSQL 16, on a db.m7g.2xlarge instance class.
In theory, the maximum number of connections is defined by `LEAST({DBInstanceClassMemory/9531392}, 5000)`
as written [here](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_Limits.html#RDS_Limits.MaxConnections)

## Metrics to follow
On database level:
- Active Connections / Sessions
- Connection wait time
- Slow queries
- CPU and Memory utilization

Most of those metrics can be checked [here](https://us-east-1.console.aws.amazon.com/rds/home?region=us-east-1#database:id=pulp-prod;is-cluster=false)

For the application, we need to follow the timeouts using the logs.
- Tasks Completed per Second (TPS)
- Task Success Rate
- Task Latency/Completion Time
- Error Rates
- Queue Lengths
You can start [here](https://grafana.app-sre.devshift.net/explore?schemaVersion=1&panes=%7B%22vse%22%3A%7B%22datasource%22%3A%22P1A97A9592CB7F392%22%2C%22queries%22%3A%5B%7B%22id%22%3A%22%22%2C%22region%22%3A%22us-east-1%22%2C%22namespace%22%3A%22%22%2C%22refId%22%3A%22A%22%2C%22queryMode%22%3A%22Logs%22%2C%22expression%22%3A%22fields+%40logStream%2C+%40message%2C++kubernetes.namespace_name+%7C+filter+%40logStream+like+%2Fpulp-stage_pulp-%28worker%7Capi%7Ccontent%29%2F%5Cn%5Cn%5Cn%5Cn%22%2C%22statsGroups%22%3A%5B%5D%2C%22datasource%22%3A%7B%22type%22%3A%22cloudwatch%22%2C%22uid%22%3A%22P1A97A9592CB7F392%22%7D%2C%22logGroups%22%3A%5B%7B%22arn%22%3A%22arn%3Aaws%3Alogs%3Aus-east-1%3A744086762512%3Alog-group%3Acrcs02ue1.pulp-stage%3A*%22%2C%22name%22%3A%22crcs02ue1.pulp-stage%22%2C%22accountId%22%3A%22744086762512%22%7D%5D%7D%5D%2C%22range%22%3A%7B%22from%22%3A%22now-30m%22%2C%22to%22%3A%22now%22%7D%7D%7D&orgId=1)

## Test Plan

1. Open a PR adding a new View that will dispatch 100 tasks that create a distribution. [here](https://github.com/pulp/pulp-service/pull/535)
2. Execute the test script that will trigger 10k tasks. It needs to request the API until it achieves that number.
2. Check the API to calculate the number of successful tasks, and the number of tasks completed per second.
3. Calculate the Task Latency using task data from the API.

## Results
Date of the test: YYYY/mm/dd

A possible template for the table could be:

| Run  | Pulp-Workers | Tasks Submitted | Tasks / Sec Processed | Avg. Wait time | Observations  |
|------|--------------|-----------------|-----------------------|----------------|---------------|
| 1    | 10           | 11546           |                       | 16678s         |               | 
| 2    | 20           | 12986           | {{value}}             | {{value}}      | Database got a huge load and topped 97% of CPU usage for most of the time processing tasks.     | 


**Key:**
- Use `-` for unavailable metrics.

## An example to calculate the number of tasks processed
