import argparse
import json
import os
import requests
import subprocess
import sys

from collections import defaultdict
from datetime import datetime, timedelta
from types import SimpleNamespace


DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
TASKS_ENDPOINT = "/api/pulp/admin/tasks/?fields=pulp_created&fields=started_at&fields=finished_at&fields=unblocked_at"

QUERY_TYPES = SimpleNamespace(
    RUNNING="running",
    WAITING_UNBLOCKED="waiting_unblocked",
    WAITING_BLOCKED="waiting_blocked",
)

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
    help="Bucket size, in seconds. For example, for a 30 seconds bucket: --bucket-size=30 [DEFAULT: 600]",
    type=int,
    default=600,
)
parser.add_argument(
    "-o",
    "--output",
    help="Output file with the metrics. [DEFAULT: /tmp/tasks-cli/pulp_tasks.out]",
    type=str,
    default="/tmp/tasks-cli/pulp_tasks.out",
)
parser.add_argument(
    "-g",
    "--graph_file",
    help="Gnuplot output file. [DEFAULT: /tmp/tasks-cli/graph_tasks.ps]",
    type=str,
    default="/tmp/tasks-cli/graph_tasks.ps",
)

args = parser.parse_args()

base_addr = args.base_address
username = args.username
password = args.password
pulp_certificate = args.certificate
pulp_cert_key = args.key
period_in_hours = args.period
bucket_size_in_seconds = args.bucket_size
output_file = args.output
graph_file = args.graph_file


# running task, 6 situations:
# 1- started and didn't finish yet (finished_at=null)
# 2- started before the current bucket interval and didn't finish yet (finished_at=null)
# 3- started and finished in between the current bucket interval
# 4- started and not finished in between the current bucket interval
# 5- started before the current bucket interval and finished in between the current bucket interval
# 6- started before the current bucket interval and not finished in between the current bucket interval

# waiting unblocked:
# 1- unblocked_at is inside of bucket, but started_at not (meaning, the task is waiting)

def run():
    datetime_now = datetime.now() + timedelta(hours=3)
    query_date_time = datetime_now - timedelta(hours=period_in_hours)
    data = defaultdict(lambda: {QUERY_TYPES.RUNNING:0,QUERY_TYPES.WAITING_UNBLOCKED:0})

    tasks = get_all_tasks()
    for task in tasks['results']:
        unblocked_at=datetime.strptime(task['unblocked_at'],DATETIME_FORMAT)
        started_at=datetime.strptime(task['started_at'],DATETIME_FORMAT)
        finished_at=datetime.strptime(task['finished_at'],DATETIME_FORMAT)

        start_bucket_interval = query_date_time
        end_bucket_interval = query_date_time + timedelta(seconds=bucket_size_in_seconds)
        while end_bucket_interval < datetime_now:
            # 1- unblocked_at is inside of bucket, but started_at not (meaning, the task is waiting)
            if in_range(unblocked_at,start_bucket_interval,end_bucket_interval) and started_at >= end_bucket_interval:
                data[start_bucket_interval.strftime(DATETIME_FORMAT)][QUERY_TYPES.WAITING_UNBLOCKED] += 1

            # 1- started and didn't finish yet (finished_at=null)
            elif in_range(started_at,start_bucket_interval,end_bucket_interval) and finished_at == "null":
                data[start_bucket_interval.strftime(DATETIME_FORMAT)][QUERY_TYPES.RUNNING] += 1

            # 2- started before the current bucket interval and didn't finish yet (finished_at=null)
            elif started_at <= start_bucket_interval and finished_at == "null":
                data[start_bucket_interval.strftime(DATETIME_FORMAT)][QUERY_TYPES.RUNNING] += 1

            # 3- started and finished in between the current bucket interval
            elif in_range(started_at,start_bucket_interval,end_bucket_interval) and in_range(finished_at,start_bucket_interval,end_bucket_interval):
                data[start_bucket_interval.strftime(DATETIME_FORMAT)][QUERY_TYPES.RUNNING] += 1

            # 4- started and not finished in between the current bucket interval
            elif in_range(started_at,start_bucket_interval,end_bucket_interval) and finished_at >= end_bucket_interval:
                data[start_bucket_interval.strftime(DATETIME_FORMAT)][QUERY_TYPES.RUNNING] += 1

            # 5- started before the current bucket interval and finished in between the current bucket interval
            elif started_at <= start_bucket_interval and in_range(finished_at,start_bucket_interval,end_bucket_interval):
                data[start_bucket_interval.strftime(DATETIME_FORMAT)][QUERY_TYPES.RUNNING] += 1

            # 6- started before the current bucket interval and not finished in between the current bucket interval
            elif started_at <= start_bucket_interval and finished_at >= end_bucket_interval:
                data[start_bucket_interval.strftime(DATETIME_FORMAT)][QUERY_TYPES.RUNNING] += 1

            start_bucket_interval = end_bucket_interval
            end_bucket_interval +=timedelta(seconds=bucket_size_in_seconds)
    
    #print(json.dumps(data, indent=2))
    write_to_file(data)
    p = subprocess.Popen(
        "gnuplot -e \"data_file='"
        + output_file
        + "'\" -e \"graph_file='"
        + graph_file
        + "'\" -c gnuplot-script",
        shell=True,
    )
    os.waitpid(p.pid, 0)

def in_range(query_time,start_time,end_time):
    return  start_time <= query_time <= end_time

def check_response(response):
    if response.status_code // 100 != 2:
        print("ERROR:", response.status_code, response.text)
        sys.exit(1)

def get_all_tasks():
    url = base_addr + TASKS_ENDPOINT
    if pulp_certificate:
        response = requests.get(url, cert=(pulp_certificate, pulp_cert_key))
    else:
        response = requests.get(url, auth=(username, password))
    check_response(response)
    return json.loads(response.text)

def write_to_file(data):
    try:
        with open(output_file, "w") as f:
            for key in sorted(data.keys()):
                print(
                    key,
                    data[key][QUERY_TYPES.RUNNING],
                    #data[key][QUERY_TYPES.WAITING_BLOCKED],
                    data[key][QUERY_TYPES.WAITING_UNBLOCKED],
                )
                f.write(
                    key
                    + " "
                    + str(data[key][QUERY_TYPES.RUNNING])
                    + " "
                    #+ str(data[key][QUERY_TYPES.WAITING_BLOCKED])
                    #+ " "
                    + str(data[key][QUERY_TYPES.WAITING_UNBLOCKED])
                    + "\n"
                )
    except FileNotFoundError:
        dirname = os.path.dirname(os.path.abspath(output_file))
        print(dirname, "not found!")
        print(
            'Make sure',
            dirname,
            'exists or set a different path for the output (tasks-cli -o/--output <file>)',
        )
        sys.exit(2)

run()
