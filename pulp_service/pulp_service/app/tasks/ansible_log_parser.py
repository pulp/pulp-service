import re
import copy
import logging
import aiohttp
from asgiref.sync import sync_to_async
from pulp_service.app.models import AnsibleLogReport
from pulpcore.app.models import Domain
from pulpcore.app.models import CreatedResource
from pulpcore.plugin.tasking import dispatch
from pulpcore.app.util import get_domain

_logger = logging.getLogger(__name__)

# Define regular expressions for log parsing (keep these as they are)
SYSTEM_ROLE_LOG_RE = re.compile(
    r"/SYSTEM-ROLE-(?P<role>[a-z0-9_]+)_(?P<test_name>tests_[a-z0-9_]+"
    r"[.]yml)-.*-ANSIBLE-(?P<ansible_ver>[0-9.]+).*[.]log$"
)
SYSTEM_ROLE_TF_LOG_RE = re.compile(
    r"/data/(?P<role>[a-z0-9_]+)-(?P<test_name>tests_[a-z0-9_]+)"
    r"-ANSIBLE-(?P<ansible_ver>[0-9.]+)-(?P<tf_job_name>[0-9a-z_]+)"
    r"-(?P<test_status>SUCCESS|FAIL)[.]log$"
)


async def get_file_data(log_url, timeout=30):
    """Fetch file content from URL asynchronously."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(log_url, timeout=timeout) as response:
                response.raise_for_status()
                text = await response.text()
                return text.splitlines()
    except aiohttp.ClientError as e:
        _logger.error("Failed to fetch log file: %s", str(e))
        raise ValueError(f"Error fetching log file: {str(e)}")


async def analyze_ansible_log(log_url, role_filter):
    """
    Async task to analyze an Ansible log file for errors.
    
    Args:
        log_url (str): URL of the Ansible log file
        role_filter (list): List of roles to filter by
    """
    errors = await get_errors_from_ansible_log(role_filter, log_url)
    
    # Prepare report data
    report_data = {
        "log_url": log_url,
        "errors": errors,
        "error_count": len(errors),
        "role_filter": role_filter,
        "pulp_domain": get_domain()
    }
    
    # Store results in database
    report = await sync_to_async(AnsibleLogReport.objects.create)(**report_data)
    
    # Register the created resource
    await sync_to_async(CreatedResource.objects.create)(content_object=report)


async def get_errors_from_ansible_log(role_filter, log_url):
    """
    Parse Ansible log file for errors asynchronously.

    Args:
        role_filter (list): List of roles to filter by, or ["ALL"] for all roles
        log_url (str): URL of the Ansible log file

    Returns:
        list: List of error dictionaries
    """
    errors = []
    current_task = None
    total_failed = 0
    task_lines = []
    task_has_fatal = False
    task_path = None
    role = "unknown"
    ansible_version = "unknown"

    _logger.debug("Getting errors from ansible log [%s]", log_url)

    # Extract role and ansible version from log URL
    # Extracts the system role name
    if "logs/tf_" in log_url:
        extracted_part = log_url.split("logs/")[1]
        start_index = extracted_part.find("tf_") + 3  # +3 to skip "tf_"
        if start_index > 2:
            end_index = extracted_part.index("-", start_index)
            role = extracted_part[start_index:end_index]
            pattern = r"ANSIBLE-(\d+\.\d+)"
            ansible_version_matches = re.findall(pattern, log_url)
            ansible_version = (
                ansible_version_matches[0] if ansible_version_matches else "UNKNOWN"
            )
    else:
        # https://....//SYSTEM-ROLE-$ROLENAME_$TEST_NAME.yml-legacy-ANSIBLE-2.log
        match = SYSTEM_ROLE_LOG_RE.search(log_url)
        if match:
            role = match.group("role")
            if role_filter != ["ALL"] and role not in role_filter:
                _logger.info(
                    "Skipping log - role [%s] not in role_filter [%s]: [%s]",
                    role,
                    str(role_filter),
                    log_url,
                )
                return []
            ansible_version = match.group("ansible_ver")
        else:
            # testing farm - https://...../data/$ROLENAME-$TESTNAME-ANSIBLE-$VER-$TFTESTNAME-$STATUS.log
            match = SYSTEM_ROLE_TF_LOG_RE.search(log_url)
            if match:
                role = match.group("role")
                if role_filter != ["ALL"] and role not in role_filter:
                    _logger.info(
                        "Skipping log - role [%s] not in role_filter [%s]: [%s]",
                        role,
                        str(role_filter),
                        log_url,
                    )
                    return []
                ansible_version = match.group("ansible_ver")

    # Use the async function to get file data
    lines = await get_file_data(log_url)

    for line in lines:
        if (
            line.startswith("TASK ")
            or line.startswith("PLAY ")
            or line.startswith("META ")
        ):
            # end of current task and possibly start of new task
            if task_lines and task_has_fatal:
                # Extract task name from the first task line
                task_match = re.search(r"TASK\s\[(.*?)\]", task_lines[0])
                if task_match:
                    current_task = task_match.group(1)
                # end task
                error = {
                    "Url": log_url,
                    "Role": role,
                    "Ansible Version": ansible_version,
                    "Task": current_task,
                    "Detail": copy.deepcopy(task_lines[3:]),
                    "Task Path": task_path,
                }
                errors.append(error)
            if line.startswith("TASK "):
                task_lines = [line.strip()]
            else:
                task_lines = []
            task_has_fatal = False
            task_path = None
        elif task_lines:
            task_lines.append(line.strip())
            if line.startswith("fatal:"):
                task_has_fatal = True
            elif line.startswith("failed:"):
                task_has_fatal = True
            elif line.startswith("task path:"):
                task_path_match = re.search(r"task path: (.*)", line)
                if task_path_match:
                    task_path = task_path_match.group(1)
            elif line.startswith("...ignoring"):
                task_has_fatal = False
        else:
            match = re.search(r"\sfailed=(\d+)\s", line)
            if match:
                total_failed += int(match.group(1))

    _logger.debug(
        "Found [%d] errors and Ansible reported [%d] failures",
        len(errors),
        total_failed,
    )
    if total_failed == 0:
        errors = []
    for error in errors:
        error["Fails expected"] = total_failed

    return errors


# Function to be used for dispatching the task
def dispatch_ansible_log_analysis(log_url, role_filter):
    """
    Dispatch an async task to analyze an Ansible log file.
    
    Args:
        log_url (str): URL of the Ansible log file
        role_filter (list): List of roles to filter by

    Returns:
        Task: The dispatched task object
    """
    # Create resources list for domain isolation
    exclusive_resources = []
    
    try:
        domain = get_domain()
        # Add as a exclusive_resources
        exclusive_resources.append(domain)
    except Domain.DoesNotExist:
        pass
    
    return dispatch(
        analyze_ansible_log,
        kwargs={"log_url": log_url, "role_filter": role_filter},
        exclusive_resources=exclusive_resources
    )