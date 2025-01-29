import argparse
import requests
import sys
import numpy as np
import matplotlib.pyplot as plt

from datetime import datetime, timedelta, UTC


DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
TASKS_ENDPOINT = "/api/pulp/admin/tasks/?fields=pulp_created&fields=started_at&fields=finished_at&fields=unblocked_at&limit=1000"


parser = argparse.ArgumentParser()
parser.add_argument(
    "-b",
    "--base_address",
    help="Pulp hostname address. For example: http://pulp-service:5001",
)
parser.add_argument(
    "-u",
    "--username",
    help="Pulp user to run the API requests. [DEFAULT: admin]",
    default="admin",
)
parser.add_argument("-p", "--password", help="Password for Pulp user.")
parser.add_argument(
    "-c", "--certificate", help="Certificate to authenticate to Pulp API."
)
parser.add_argument(
    "-k", "--key", help="Private key for the certificate authentication."
)
parser.add_argument(
    "--period",
    help="Period, in hours, to check the tasks. For example, for the last 24 hours: --period=24 [DEFAULT: 2]",
    type=int,
    default=2,
)
parser.add_argument(
    "--bucket_size",
    help="Bucket size, in seconds. For example, for a 30 seconds bucket: --bucket-size=30 [DEFAULT: 60]",
    type=int,
    default=60,
)

args = parser.parse_args()

base_addr = args.base_address
username = args.username
password = args.password
pulp_certificate = args.certificate
pulp_cert_key = args.key
period_in_hours = args.period
bucket_size_in_seconds = args.bucket_size


def generate_buckets(start_time, end_time, interval):
    buckets = {}

    # Calculate the total number of intervals
    interval_duration = timedelta(seconds=interval)
    current_start = start_time

    while current_start < end_time:
        current_end = current_start + interval_duration
        midpoint = current_start + (interval_duration / 2)
        buckets[midpoint] = 0
        current_start = current_end

    return buckets

def run():
    datetime_now = datetime.now(UTC)
    query_date_time = datetime_now - timedelta(hours=period_in_hours)

    runtime_data = []
    waittime_data = []
    data = generate_buckets(query_date_time, datetime_now, bucket_size_in_seconds)
    bucket_times = sorted(data.keys())
    interval_duration = timedelta(seconds=bucket_size_in_seconds)

    tasks = get_all_tasks(start_datetime=query_date_time)
    for task in tasks:
        if task['unblocked_at']:
            unblocked_at = datetime.fromisoformat(task['unblocked_at'])
        else:
            unblocked_at = None
        if task['started_at']:
            started_at = datetime.fromisoformat(task['started_at'])
        else:
            started_at = None
        if task['finished_at']:
            finished_at = datetime.fromisoformat(task['finished_at'])
        else:
            finished_at = None

        # Calculate runtime
        if started_at and finished_at:
            runtime = finished_at - started_at
            runtime_data.append(runtime.total_seconds())

        # Calculate wait time
        if started_at and unblocked_at:
            waittime = started_at - unblocked_at
            waittime_data.append(waittime.total_seconds())

        # Gather stats on unblocked and waiting tasks
        if waittime.total_seconds() >= 5:
            for midpoint in bucket_times:
                bucket_start = midpoint - (interval_duration / 2)
                bucket_end = midpoint + (interval_duration / 2)
                if bucket_start <= unblocked_at < bucket_end:
                    # The task was blocked during this interval
                    data[midpoint] += 1

                    if started_at < bucket_end:
                        # The task started running during this interval
                        break
                if unblocked_at < bucket_start <= started_at < bucket_end:
                    # The task got unblocked in a previous interval and started in this one
                    data[midpoint] += 1
                    break
                if unblocked_at < bucket_start and started_at > bucket_end:
                    # The task got unblocked in a previous interval and didn't start during this interval
                    data[midpoint] += 1

    # Get percentiles for run times
    print("Task Run Time Percentiles (seconds)")
    print("-----------------------------------")
    print(f"90th Percentile: { np.percentile(runtime_data, 90)} seconds")
    print(f"95th Percentile: { np.percentile(runtime_data, 95)} seconds")
    print(f"99th Percentile: { np.percentile(runtime_data, 99)} seconds")
    print(f"99.9th Percentile: {np.percentile(runtime_data, 99.9)} seconds")
    print(f"99.99th Percentile: {np.percentile(runtime_data, 99.99)} seconds")
    print(f"99.999th Percentile: {np.percentile(runtime_data, 99.999)} seconds")
    print(f"100th Percentile: {np.percentile(runtime_data, 100)} seconds")
    print("\n\n")

    # Plot runtime distribution
    plt.figure(figsize=(8, 6))
    plt.hist(runtime_data, bins='auto', density=True, color='skyblue', edgecolor='black', alpha=0.7)
    plt.xlabel('Task Run Time (seconds)')
    plt.ylabel('Probability')
    plt.title('Probability Distribution of Task Run Time')
    plt.grid(axis='y', linestyle='--', alpha=0.7)
    plt.show()


    # Get percentiles for wait times
    print("Task Wait Time Percentiles (seconds)")
    print("-----------------------------------")
    print(f"90th Percentile: { np.percentile(waittime_data, 90)} seconds")
    print(f"95th Percentile: { np.percentile(waittime_data, 95)} seconds")
    print(f"99th Percentile: { np.percentile(waittime_data, 99)} seconds")
    print(f"99.9th Percentile: {np.percentile(waittime_data, 99.9)} seconds")
    print(f"99.99th Percentile: {np.percentile(waittime_data, 99.99)} seconds")
    print(f"99.999th Percentile: {np.percentile(waittime_data, 99.999)} seconds")
    print(f"100th Percentile: {np.percentile(waittime_data, 100)} seconds")


    # Plot the number of unblocked tasks
    midpoints = list(data.keys())
    counts = list(data.values())

    # Create the plot
    plt.figure(figsize=(12, 6))
    plt.scatter(midpoints, counts, color='blue', label='Counts')

    # Format the x-axis for better readability
    plt.xticks(rotation=45)
    plt.xlabel('Date and Time')
    plt.ylabel('Number of unblocked tasks waiting for 5 or more seconds')
    plt.title('Unblocked and Waiting Tasks Over Time')

    # Show grid for better readability
    plt.grid(axis='y', linestyle='--', alpha=0.7)

    # Display the plot
    plt.tight_layout()
    plt.show()

def in_range(query_time,start_time,end_time):
    return  start_time <= query_time <= end_time

def check_response(response):
    if response.status_code // 100 != 2:
        print("ERROR:", response.status_code, response.text)
        sys.exit(1)


def get_all_tasks(params=None, headers=None, start_datetime=None):

    url = base_addr + TASKS_ENDPOINT
    if start_datetime:
        url = url + "&started_at__gte=" + start_datetime.strftime(DATETIME_FORMAT)
    while url:
        response = requests.get(url, params=params, headers=headers, cert=(pulp_certificate, pulp_cert_key))
        response.raise_for_status()  # Raise an exception for HTTP errors
        data = response.json()  # Parse the JSON response

        # Yield each item in the 'results' array
        for result in data.get("results", []):
            yield result

        # Update the URL with the 'next' attribute from the response
        url = data.get("next")
        if url:
            url = url.replace("http://internal", "https://mtls.internal")

run()
