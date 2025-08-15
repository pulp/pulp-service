# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0] - 2025-08-05

### Added

- **Certificate Authentication:** Implemented support for client-side certificate authentication (`--cert` and `--key`) across both synchronous and asynchronous clients. This allows for more secure authentication methods in environments where it is required.

### Changed

- **CLI (`pulp_benchmark/cli.py`):**
  - Added `--cert` and `--key` options to the main `click` command group.
  - Updated the context object (`ctx.obj`) to pass certificate paths to plugins.
  - Modified the initial status check (`get_system_status_*`) to use the new authentication options.

- **Synchronous Client (`pulp_benchmark/client_sync.py`):**
  - Updated `run_concurrent_requests_sync` and `get_system_status_sync` to accept `cert` and `key` arguments and configure the `requests.Session` accordingly.

- **Asynchronous Client (`pulp_benchmark/client_async.py`):**
  - Updated `run_concurrent_requests` and `get_system_status` to accept `cert` and `key` arguments.
  - Implemented `ssl.SSLContext` creation to handle certificate loading for `aiohttp` sessions.

- **Plugins (`pulp_benchmark/plugins/`):**
  - Modified `test_task_insertion.py` to retrieve certificate info from the context and pass it to the client functions.
  - Modified `task_analysis.py` to support certificate authentication for its API requests.

- **Documentation (`README.md`):**
  - Updated the "Global Options" table to include documentation for the new `--cert` and `--key` flags.

### Fixed

- **CLI (`pulp_benchmark/cli.py`):** The `--password` option is no longer required when using certificate authentication (`--cert` and `--key`).
- **`task_analysis.py`:** Corrected a variable typo from `tasks_durl` to `tasks_url` in the `run_analysis_sync` function.
