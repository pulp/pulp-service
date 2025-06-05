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

| Run  | Pulp-Workers | Requests | Tasks Submitted | Tasks / Sec Processed | Observations  |
|------|---------|----------|------------------|--------------|-------------|--------------|
| 1    | 1       | 1000     | 990              | 10s          | 1/min       | `...`        | 
| 2              | {{value}}| {{value}}        | `...`        | `...`       | `...`        |
...


**Key:**
- Use `-` for unavailable metrics.

## An example to calculate the number of tasks processed
```python
import argparse
import requests
import time
import concurrent.futures

from datetime import datetime

def send_request_and_process_tasks(url, timeout):
    """
    Sends a request to the specified URL and calculates the number of tasks processed

    Args:
        url (str): The endpoint to send the request to.

    Returns:
        int: Number of tasks processed, or None if the request fails or times out.
    """
    try:
        start_time = time.time()

        # Send the request (adjust method, headers, or data as needed)
        response = requests.get(url, params={"timeout": timeout})
        response.raise_for_status()  # Raise an error for bad status codes

        tasks = response.json().get('tasks_executed')

        elapsed_time = time.time() - start_time

        print(f"Processed {tasks} tasks in {elapsed_time:.2f} seconds.")
        return tasks

    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        return None
    except Exception as e:
        print(f"Error processing tasks: {e}")
        return None

def run_with_timeout(url, timeout=25, max_workers=1):
    """
    Runs the request and task processing with a timeout.

    Returns:
        int: Number of tasks processed, or None if timeout or error occurs.
    """
    data = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(send_request_and_process_tasks, url, timeout) for _ in range(max_workers)]
        for future in concurrent.futures.as_completed(futures):
            try:
                data += future.result()
            except Exception as exp:
                print("%s" % (exp))

    return data

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a task with configurable timeout and executors.")

    # Add arguments
    parser.add_argument(
        "--url",
        type=str,
        default="https://api.example.com/tasks",
        help="The API endpoint URL to send the request to. (default: https://api.example.com/tasks)"
    )

    parser.add_argument(
        "--api_root",
        type=str,
        default="https://api.example.com/api",
        help="The API root of Pulp instance to be tested."
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=25,
        help="The maximum time in seconds to wait for the operation to complete. (default: 25)"
    )
    parser.add_argument(
        "--max_workers",
        type=int,
        default=1,
        help="The number of worker threads to use for concurrent tasks. (default: 1)"
    )
    parser.add_argument(
        "--run_until_number_of_tasks",
        type=int,
        help="Request new tasks until it reaches this limit."
    )

    args = parser.parse_args()

    if args.api_root:
        args.url = f"{args.api_root}/pulp/test/tasks/"

    print(f"Running with URL: {args.url}, Timeout: {args.timeout}s, Workers: {args.max_workers}")

    # Number of API, Content and task workers
    status_endpoint = f"{args.api_root/pulp/api/v3/status/}"
    status = requests.get(status_endpoint)
    status.raise_for_status()
    api_workers = len(status.get("online_api_apps"))
    content_workers = len(status.get("online_content_apps"))
    task_workers = len(status.get("online_workers"))

    print(f"Number of online API workers: {api_workers}")
    print(f"Number of online Content workers: {content_workers}")
    print(f"Number of online Task workers: {task_workers}") 

    start_time = datetime.now()
    
    tasks_processed = 0
    if args.run_until_number_of_tasks:
        while tasks_processed < args.run_until_number_of_tasks:
            tasks_processed += run_with_timeout(args.url, args.timeout, args.max_workers)
    else:
        tasks_processed = run_with_timeout(args.url, args.timeout, args.max_workers)

    elapsed_time = datetime.now() - start_time

    if tasks_processed is not None:
        print(f"Total tasks processed: {tasks_processed}. Request rate: {tasks_processed/args.timeout}/s")
    else:
        print("No tasks processed due to timeout or error.")

    print("\n")
    print("Obtaining tasks data...")

    tasks_url = f"{args.api_root}/pulp/api/v3/tasks/?pulp_created__gte={start_time.isoformat()}"
    
    tasks_request = requests.get(tasks_url)
    tasks_request.raise_for_status()
    tasks_request.json()

    tasks_count = tasks_request.get("count")
    print(f"Total tasks so far: {tasks_count}")
```
