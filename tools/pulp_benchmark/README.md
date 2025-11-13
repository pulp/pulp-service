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

| Option    | Type       | Default      | Description                                                              |
|-----------|------------|--------------|--------------------------------------------------------------------------|
| `--since` | `DateTime` | *Current UTC* | Analyze tasks created since this timestamp. Format: `YYYY-MM-DDTHH:MM:SS`. |

**Example:**
```bash
# Analyze tasks created since June 10, 2025 at 6:00 PM UTC
python -m pulp_benchmark.main --api-root [https://pulp.example.com](https://pulp.example.com) task_analysis --since "2025-06-10T18:00:00"
```

### **Section 8: Extending the Tool**

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
