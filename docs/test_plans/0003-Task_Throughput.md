# Task Throughput Test Plan

<!--toc:start-->
- [Task Throughput Test Plan](#task-throughput-test-plan)
  - [Current State](#current-state)
  - [Metrics to follow](#metrics-to-follow)
  - [Test Plan](#test-plan)
  - [Results](#results)
  - [An example to calculate the number of tasks ingested](#an-example-to-calculate-the-number-of-tasks-ingested)
<!--toc:end-->

Some operations on Pulp API trigger an immediate task, which should be executed in 
the same process of the API if all exclusive resources are available at the moment.
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

1. Start the example script firing a number of requests to create an empty distribution.
2. Using the logs, calculate the number of successful tasks, and the number of tasks completed per second.
3. Calculate the Task Latency using a sample of executed tasks.


## Results
Date of the test: YYYY/mm/dd

A possible template for the table could be:

| Run  | Workers | Requests | Successful Tasks | Task Latency | Error Rates | Observations |
|------|---------|----------|------------------|--------------|-------------|--------------|
| 1    | 1       | 1000     | 990              | 10s          | 1/min       | `...`        | 
| 2              | {{value}}| {{value}}        | `...`        | `...`       | `...`        |
...


**Key:**
- Use `-` for unavailable metrics.

## An example to calculate the number of tasks ingested
```python
import requests
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError

def send_request_and_process_tasks(url):
    """
    Sends a request to the specified URL and calculates the number of tasks processed
    within a 25-second timeout based on the response.
    
    Args:
        url (str): The endpoint to send the request to.
    
    Returns:
        int: Number of tasks processed, or None if the request fails or times out.
    """
    try:
        start_time = time.time()
        
        # Send the request (adjust method, headers, or data as needed)
        response = requests.get(url)
        response.raise_for_status()  # Raise an error for bad status codes
        
        tasks = response.json().get('tasks')
        
        elapsed_time = time.time() - start_time
        
        print(f"Processed {tasks} tasks in {elapsed_time:.2f} seconds.")
        return tasks
    
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        return None
    except Exception as e:
        print(f"Error processing tasks: {e}")
        return None

def run_with_timeout(url, timeout=25):
    """
    Runs the request and task processing with a timeout.
    
    Args:
        url (str): The endpoint to send the request to.
    
    Returns:
        int: Number of tasks processed, or None if timeout or error occurs.
    """
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(send_request_and_process_tasks, url)
        try:
            result = future.result()
            return result
        except TimeoutError:
            print("Operation timed out after 25 seconds.")
            return None

# Example usage:
if __name__ == "__main__":
    # Replace with your actual endpoint
    endpoint_url = "https://api.example.com/tasks"
    tasks_processed = run_with_timeout(endpoint_url)
    
    if tasks_processed is not None:
        print(f"Total tasks processed: {tasks_processed}")
    else:
        print("No tasks processed due to timeout or error.")
```
