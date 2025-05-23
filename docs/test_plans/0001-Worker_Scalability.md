# Worker Scalability Test Plan

<!--toc:start-->
- [Worker Scalability Test Plan](#worker-scalability-test-plan)
  - [Current State](#current-state)
  - [Metrics to follow](#metrics-to-follow)
  - [Test Plan](#test-plan)
  - [Results](#results)
  - [An example to verify different worker names from time to time](#an-example-to-verify-different-worker-names-from-time-to-time)
<!--toc:end-->

The workers have a permanent connection to the database and keep sending [heartbeats]()
as signal that they are alive and ready to accomplish a task.
The idea of this test plan is to verify the maximum number of idle workers before its 
heartbeat starts to timeout.

## Current State
We're gonna use the Pulp stage instance to run those tests. Each pulp-worker pod requests 
must request the minimal amount needed, 128MiB of memory, and have access to 250m of CPU.

For database, we're using the AWS RDS PostgreSQL 16, on a db.m7g.2xlarge instance class.
In theory, the maximum number of connections is defined by `LEAST({DBInstanceClassMemory/9531392}, 5000)`
as written [here](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_Limits.html#RDS_Limits.MaxConnections)

## Metrics to follow
On database level:
- Active Connections
- Connection wait time
- Slow queries
- CPU and Memory utilization

Most of those metrics can be checked [here](https://us-east-1.console.aws.amazon.com/rds/home?region=us-east-1#database:id=pulp-prod;is-cluster=false)

For the application, we need to follow the timeouts using the logs.
You can start [here](https://grafana.app-sre.devshift.net/explore?schemaVersion=1&panes=%7B%22vse%22%3A%7B%22datasource%22%3A%22P1A97A9592CB7F392%22%2C%22queries%22%3A%5B%7B%22id%22%3A%22%22%2C%22region%22%3A%22us-east-1%22%2C%22namespace%22%3A%22%22%2C%22refId%22%3A%22A%22%2C%22queryMode%22%3A%22Logs%22%2C%22expression%22%3A%22fields+%40logStream%2C+%40message%2C++kubernetes.namespace_name+%7C+filter+%40logStream+like+%2Fpulp-stage_pulp-%28worker%7Capi%7Ccontent%29%2F%5Cn%5Cn%5Cn%5Cn%22%2C%22statsGroups%22%3A%5B%5D%2C%22datasource%22%3A%7B%22type%22%3A%22cloudwatch%22%2C%22uid%22%3A%22P1A97A9592CB7F392%22%7D%2C%22logGroups%22%3A%5B%7B%22arn%22%3A%22arn%3Aaws%3Alogs%3Aus-east-1%3A744086762512%3Alog-group%3Acrcs02ue1.pulp-stage%3A*%22%2C%22name%22%3A%22crcs02ue1.pulp-stage%22%2C%22accountId%22%3A%22744086762512%22%7D%5D%7D%5D%2C%22range%22%3A%7B%22from%22%3A%22now-30m%22%2C%22to%22%3A%22now%22%7D%7D%7D&orgId=1)

## Test Plan

1. Warn the pulp stakeholders (@pulp-service-stakeholders) that a test gonna happen in stage. All tasks must be stopped.
2. Create a MR reducing the resource for each worker, and increasing the number of workers. [here](https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/data/services/pulp/deploy.yml#L75).
It can be approved by anyone from the team. Just need to wait for the app-sre bot to merge it.
3. After it got merged, check for the database metrics and application logs. The results from the run will be down in this document.
4. We need to check if there's any new worker being started. For that, we need to use the `WorkersAPI` and check a set with the names from time to time.
An example of a script to check the workers from time to time is written [here](#an-example-to-verify-different-worker-names-from-time-to-time)
5. When the test is done, you need a new MR reducing the number of workers to 0 (zero) and then a new MR returning 
the number to its usual state (2 pulp-workers in staging) and to its proper resource utilization.
6. Send a new message to the Pulp Stakeholders (@pulp-service-stakeholders) saying that the test is concluded.

## Results
To be added in the future.

Date of the test: YYYY/mm/dd

A possible template for the table could be:

| Instance Count | Timeout(Yes/No) | Bottleneck Identified |
|----------------|-----------------|-----------------------|
| 100            | `No`            | `None`                |
| 250            | `No`            | `None`                 |
| 500            | `Yes`           | `We saw spikes in CPU and memory usage for some containers, and also a huge CPU spike on the database. The system was able to recover after sometime, yet the spikes keep occurring.` |
| 1000           | `Yes`           | `We saw spikes in CPU and memory usage for some containers, and also a huge CPU spike on the database. The system was able to recover after 30 min approx., yet the spikes keep occurring.`                 |`...`                 |

**Key:**
- Use `-` for unavailable metrics.

## An example to verify different worker names from time to time
```bash
#!/bin/bash

while true; do
    # Get current worker count
    curl -s -u admin:password http://pulp-api:8000/pulp/default/api/v3/workers/?online=true\&limit=1100 | \
        jq '.results | map(.name) | length'
    
    # Get first worker list
    export LIST1=$(curl -s -u admin:password \
        http://pulp-api:8000/api/pulp/default/api/v3/workers/?online=true\&limit=1100 | \
        jq '.results | map(.name) | sort')
    
    # Wait for any possible change
    sleep 20
    
    # Get second worker list
    export LIST2=$(curl -s -u admin:password \
        http://pulp-api:8000/api/pulp/default/api/v3/workers/?online=true\&limit=1100 | \
        jq '.results | map(.name) | sort')
    
    # Compare the two lists
    jq -n --argjson l1 "$LIST1" --argjson l2 "$LIST2" '($l1 | sort) == ($l2 | sort)'
    
    sleep 20
done
```
