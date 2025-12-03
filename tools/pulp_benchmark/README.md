# Pulp Benchmark & Analysis Tool

A high-performance, asynchronous command-line tool for load testing and analyzing the tasking system of a Pulp API instance. Built with Python, `aiohttp`, and `click`.

This tool is designed to be highly extensible through a self-registering plugin architecture, allowing new commands and functionality to be added with minimal effort.

## Features

-   **Asynchronous Core:** Uses `aiohttp` and `asyncio` to handle a high volume of concurrent network requests with minimal resource usage, perfect for load testing.
-   **Extensible Plugin Architecture:** Automatically discovers and registers new commands. Simply drop a new Python file in the `plugins/` directory to add new functionality.
-   **Detailed Task Analysis:** Provides statistical analysis of task wait times, including mean, median, percentiles, and a wait-time distribution table using `numpy` and `pandas`.
-   **Secure & User-Friendly CLI:** Built with `click`, featuring a clean interface, auto-generated help messages, and a secure password prompt.
-   **Environment Variable Support:** Configure global options via environment variables for easier use in CI/CD pipelines.

## Prerequisites

-   Python 3.8+

## Installation

1.  **Set up the project directory:** Clone the repository or create the files and directories for the `pulp_benchmark` project.

2.  **Create and activate a virtual environment** (recommended):
    ```bash
    # For Unix/macOS
    python3 -m venv venv
    source venv/bin/activate

    # For Windows
    python -m venv venv
    .\venv\Scripts\activate
    ```

3.  **Install the required dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

## Usage

The application is run through the `main.py` entry point. It follows the structure:
`python -m pulp_benchmark.main [GLOBAL OPTIONS] COMMAND [COMMAND OPTIONS]`

### Global Options

These options are available for all commands and must be provided before the command name.

| Option                       | Environment Variable | Default | Description                                                                                                  |
|------------------------------|----------------------|---------|--------------------------------------------------------------------------------------------------------------|
| `--api-root`                 | `PULP_API_ROOT`      | *None*  | **Required.** The root URL of the Pulp API instance (e.g., `https://pulp.example.com`).                     |
| `--user`                     | `PULP_USER`          | `admin` | Username for API authentication.                                                                             |
| `--password`                 | `PULP_PASSWORD`      | *None*  | **Required** (or use `--cert`/`--key`). Password for API authentication.                                     |
| `--cert`                     | *None*               | *None*  | Path to the client certificate file for mTLS authentication.                                                 |
| `--key`                      | *None*               | *None*  | Path to the client private key file for mTLS authentication.                                                 |
| `--verify-ssl`/`--no-verify-ssl` | *None*           | `True`  | Verify SSL certificates. Use `--no-verify-ssl` for self-signed certificates.                                |
| `--debug-requests`/`--no-debug-requests` | *None*   | `False` | Enable detailed logging of HTTP requests and responses (headers, body, status).                              |
| `--client`                   | *None*               | `async` | HTTP client to use: `async` (aiohttp) or `sync` (requests).                                                 |

**Authentication**: Either `--password` or both `--cert` and `--key` must be provided.

**Examples:**

```bash
# Basic authentication
python -m pulp_benchmark.main --api-root https://pulp.example.com --user admin --password secret command

# Client certificate authentication
python -m pulp_benchmark.main --api-root https://pulp.example.com --cert /path/to/cert.pem --key /path/to/key.pem command

# With self-signed certificates
python -m pulp_benchmark.main --api-root https://pulp.example.com --user admin --password secret --no-verify-ssl command

# Enable debug logging
python -m pulp_benchmark.main --api-root https://pulp.example.com --user admin --password secret --debug-requests command
```

### Available Commands

#### `test_task_insertion`

Runs a load test by concurrently creating many simple tasks on the server.

**Options:**

| Option            | Type  | Default | Description                                              |
|-------------------|-------|---------|----------------------------------------------------------|
| `--timeout`       | `int` | `30`      | Timeout in seconds for each individual task request.     |
| `--max-workers`   | `int` | `50`      | Number of concurrent async requests to run in parallel.  |
| `--run-until`     | `int` | `None`  | Keep creating tasks until this total number is reached.  |

**Example:**
```bash
python -m pulp_benchmark.main --api-root [https://pulp.example.com](https://pulp.example.com) test_task_insertion --max-workers 100 --run-until 5000
```

### **Section 7: Usage - `task_analysis` Command**

```markdown
#### `task_analysis`

Fetches and performs a detailed wait-time analysis on tasks completed in the system.

**Options:**

| Option        | Type       | Default       | Description                                                              |
|---------------|------------|---------------|--------------------------------------------------------------------------|
| `--since`     | `DateTime` | *Current UTC* | Analyze tasks created since this timestamp. Format: `YYYY-MM-DDTHH:MM:SS`. |
| `--until`     | `DateTime` | *None*        | Analyze tasks created until this timestamp. Format: `YYYY-MM-DDTHH:MM:SS`. |
| `--task-name` | `String`   | *None*        | Filter tasks by name (e.g., `pulp_ansible.app.tasks.collections.sync`).  |

**Examples:**
```bash
# Analyze all tasks created since June 10, 2025 at 6:00 PM UTC
python -m pulp_benchmark.main --api-root https://pulp.example.com task_analysis --since "2025-06-10T18:00:00"

# Analyze tasks within a specific time range
python -m pulp_benchmark.main --api-root https://pulp.example.com task_analysis \
  --since "2025-12-01T00:00:00" \
  --until "2025-12-03T23:59:59"

# Analyze only specific task type
python -m pulp_benchmark.main --api-root https://pulp.example.com task_analysis \
  --since "2025-12-01T00:00:00" \
  --task-name "pulp_ansible.app.tasks.collections.sync"

# Use with mTLS authentication
python -m pulp_benchmark.main \
  --api-root https://mtls.internal.console.stage.redhat.com/api \
  --cert /path/to/cert.pem \
  --key /path/to/key.pem \
  task_analysis --task-name "pulp_container.app.tasks.sync"
```

### **Section 8: Usage - `rds_connection_tests` Command**

```markdown
#### `rds_connection_tests`

Triggers and monitors RDS Proxy connection timeout tests remotely. This plugin tests database connection behavior over extended periods to identify RDS Proxy timeout issues.

**Related Ticket**: [PULP-955](https://issues.redhat.com/browse/PULP-955)

**Options:**

| Option              | Short | Type      | Default | Description                                                              |
|---------------------|-------|-----------|---------|--------------------------------------------------------------------------|
| `--test`            | `-t`  | `String`  | *All*   | Test name to run (can be specified multiple times). If not provided, runs all tests. |
| `--duration`        | `-d`  | `Integer` | `50`    | Duration of each test in minutes (1-300).                                |
| `--monitor`         |       | `Flag`    | `True`  | Monitor task progress and display results when complete.                 |
| `--no-monitor`      |       | `Flag`    | `False` | Disable monitoring (dispatch tasks and exit immediately).                |
| `--poll-interval`   | `-p`  | `Integer` | `60`    | How often to check task status in seconds (when monitoring).             |
| `--run-sequentially`|       | `Flag`    | `False` | Run tests sequentially instead of in parallel.                           |

**Available Tests:**
- `test_1_idle_connection` - Tests connection that remains idle
- `test_2_active_heartbeat` - Tests connection with periodic heartbeat queries
- `test_3_transaction_open` - Tests open transaction behavior
- `test_4_advisory_lock` - Tests PostgreSQL advisory locks
- `test_5_cursor_hold` - Tests cursor with hold behavior
- `test_6_listen_notify` - Tests LISTEN/NOTIFY channels
- `test_7_pinned_connection` - Tests pinned database connections

**Examples:**
```bash
# Run all tests with default 50-minute duration
python -m pulp_benchmark.main \
  --api-root https://your-pulp-instance.com \
  rds_connection_tests

# Run specific test with custom duration
python -m pulp_benchmark.main \
  --api-root https://your-pulp-instance.com \
  rds_connection_tests \
    --test test_1_idle_connection \
    --duration 90

# Run multiple tests sequentially
python -m pulp_benchmark.main \
  --api-root https://your-pulp-instance.com \
  rds_connection_tests \
    -t test_1_idle_connection \
    -t test_2_active_heartbeat \
    --run-sequentially

# Dispatch tests without monitoring
python -m pulp_benchmark.main \
  --api-root https://your-pulp-instance.com \
  rds_connection_tests \
    --no-monitor

# Debug mode with detailed HTTP logging
python -m pulp_benchmark.main \
  --api-root https://your-pulp-instance.com \
  --debug-requests \
  rds_connection_tests \
    -t test_1_idle_connection
```

**For detailed documentation**, see [RDS_TESTS_REMOTE_TRIGGER_GUIDE.md](RDS_TESTS_REMOTE_TRIGGER_GUIDE.md)
```

### **Section 9: Extending the Tool**

```markdown
---
## Extending the Tool: Adding New Commands

This tool features a plugin system that automatically discovers new commands. To add your own command:

1. Create a new Python file inside the `pulp_benchmark/plugins/` directory (e.g., `pulp_benchmark/plugins/my_new_command.py`).

2. In that file, use `click` to define a self-contained command. The file must contain an object that is an instance of `click.Command`.

**Example Plugin (`pulp_benchmark/plugins/hello.py`):**
```python
import click

@click.command()
@click.option('--name', default='World', help='The name to greet.')
@click.pass_context
def hello(ctx, name):
    """A simple example of a plugin command."""
    api_root = ctx.obj.get('api_root', 'NOT SET')
    click.echo(f"Hello, {name}!")
    click.echo(f"This command is running against API root: {api_root}")
```

### **Section 9: License**

```markdown
## License

This project is open source and available under the [GPLv2 License](LICENSE).
