package skills

import (
	"github.com/tmc/langchaingo/llms"
)

func MalwareAndVulnSkill() llms.MessageContent {
	malwareMessage := `
# Python Package Archive Security Scanner (.tar.gz / .whl) — Single‑File Mode

Skill to analyze **a single file extracted from a Python .tar.gz (sdist) or .whl (wheel/bdist) archive** for **malware‑like behavior** and **common security vulnerabilities**. Files are sent one at a time to keep context usage low.

## Input

The skill expects:

- A **single file** from a Python package archive, provided as text in the prompt.
- Metadata headers indicating:
  - **Archive**: the source archive filename (e.g., mypackage-1.0.0.tar.gz or mypackage-1.0.0-py3-none-any.whl).
  - **File**: the path of the file inside the archive (e.g., mypackage-1.0.0/setup.py or mypackage/__init__.py).

The archive type can be inferred from the **Archive** header:
- **.tar.gz / .tgz** → sdist (source distribution). Contains setup.py, raw source, PKG‑INFO, etc.
- **.whl** → wheel (built distribution). Contains pre‑built .py files, a .dist‑info directory with METADATA, RECORD, WHEEL, entry_points.txt, etc. No setup.py — code runs at import time, not install time.

From this input, the skill should:
- Identify the file's role in the package based on its path and archive type.
- Apply the appropriate scanning rules for that file type.
- Return a concise security report for **this file only**.

---

## Step 1: Identify file role

Determine the file's role from its path and the archive type:

### For sdist (.tar.gz) archives:
- **Installation entry point** (setup.py, setup.cfg, pyproject.toml) → apply Step 2 (CRITICAL scrutiny).
- **Package init** (__init__.py) → apply Step 2 + Step 3.
- **Regular source file** (.py) → apply Step 3 + Step 4.
- **Metadata file** (PKG‑INFO, README, LICENSE) → apply Step 4 only (check for hardcoded secrets).
- **Config/data file** (.cfg, .ini, .toml, .yaml, .json) → apply Step 4 only.

### For wheel (.whl) archives:
- **dist‑info metadata** (METADATA, WHEEL, RECORD, top_level.txt) → apply Step 2‑whl (metadata validation).
- **Entry points** (entry_points.txt or entry_points in METADATA) → apply Step 2‑whl (CRITICAL — defines console_scripts that run as commands).
- **Package init** (__init__.py) → apply Step 3 + Step 4 (in wheels, __init__.py is the main import‑time attack surface).
- **Regular source file** (.py) → apply Step 3 + Step 4.
- **Data/config files** (.json, .yaml, .cfg, etc.) → apply Step 4 only.
- **Compiled extensions** (.so, .pyd, .dll) → flag as **native_extension** for awareness (cannot be scanned as text).

If the file is a binary placeholder (content starts with "[binary file"), flag as **unexpected_binary** (for sdist) or **native_extension** (for whl) and skip further scanning.

---

## Step 2: Scan installation entry points (CRITICAL — sdist)

The **setup.py** (and pyproject.toml build scripts) are the highest‑risk files in an sdist because they execute during **pip install**. Scan them with extra scrutiny.

### 2.1 Malicious install hooks

Flag if setup.py or build scripts contain:

- Custom **cmdclass** overrides (e.g., subclassing install, develop, egg_info, build_ext) that execute arbitrary code during installation.
- Code that runs **before or outside** the setup() call — any top‑level statements in setup.py beyond imports and the setup() invocation are suspicious.
- Use of **atexit**, **signal handlers**, or **threading/multiprocessing** to run code as a side effect of importing or installing.
- **Pre/post‑install scripts** defined in setup.cfg or pyproject.toml that execute shell commands.

### 2.2 Network activity during install

Flag setup.py or build scripts that:

- Import or use **urllib, requests, http.client, socket, ftplib, smtplib, xmlrpc** at the top level or inside cmdclass overrides.
- Download additional code or data during installation (a major malware red flag).
- Exfiltrate environment information (os.environ, platform details, SSH keys, credentials) to remote servers.

### 2.3 Dangerous constructs in setup.py

Flag if setup.py contains:

- **eval(**, **exec(**, **compile(** with dynamic or obfuscated input.
- **os.system(**, **os.popen(**, **subprocess.call(**, **subprocess.Popen(**, **subprocess.run(** — shell command execution during install.
- **__import__(** used to dynamically load modules at install time.
- **ctypes** or **cffi** calls to load native code.

For each match, report:
- File path within the archive.
- Construct type (e.g., "malicious_install_hook", "network_during_install").
- Line context (snippet and line number).
- Risk label: **CRITICAL** for install‑time execution, **HIGH** for dangerous constructs.

---

## Step 2‑whl: Scan wheel metadata and entry points (CRITICAL — .whl)

Wheels do not have a setup.py, so the attack surface differs from sdist. The main risks are **malicious entry points** (console_scripts / gui_scripts that run as CLI commands) and **import‑time code** in __init__.py.

### 2‑whl.1 Entry points validation

If the file is **entry_points.txt** (inside .dist‑info):
- Parse the [console_scripts] and [gui_scripts] sections.
- Flag entry points that reference unusual module paths or functions (e.g., pointing to obfuscated module names).
- Flag entry points that map to modules outside the declared top‑level packages (cross‑reference with top_level.txt if available).
- Risk label: **HIGH** if entry points look suspicious.

### 2‑whl.2 METADATA validation

If the file is **METADATA** (inside .dist‑info):
- Check the **Name** field for typosquatting against popular packages.
- Check **Requires‑Dist** for suspicious or internal‑looking dependency names (dependency confusion).
- Flag if the **Summary** or **Description** is empty or generic while the package declares many dependencies — a common pattern in malicious placeholder wheels.
- Risk label: **MEDIUM** for metadata anomalies.

### 2‑whl.3 RECORD validation

If the file is **RECORD** (inside .dist‑info):
- List all files declared in the wheel.
- Flag any compiled extensions (.so, .pyd, .dll) for awareness.
- Flag files listed in RECORD that have unusual or deeply nested paths.
- Risk label: **LOW** (informational).

### 2‑whl.4 WHEEL file validation

If the file is **WHEEL** (inside .dist‑info):
- Verify the **Wheel‑Version** is a known valid version (e.g., 1.0).
- Check that **Root‑Is‑Purelib** is consistent with the archive contents (a purelib wheel should not contain .so/.pyd files).
- Risk label: **LOW** (informational).

---

## Step 3: Scan package source files

Scan all .py files in the package for malware and vulnerability patterns.

### 3.1 Dangerous code constructs

Flag if the code contains:

- Direct use of eval(, exec(, or compile( with user‑controlled or remote‑tainted input.
- os.system(, os.popen(, subprocess.call(, subprocess.Popen(, subprocess.run() with strings built from untrusted data.
- __import__('code').interact() or similar interactive backdoor patterns.
- Network‑oriented code that:
  - Opens outbound sockets (socket.socket, urllib, requests, http.client) to hard‑coded or obfuscated IPs/domains.
  - Sends local data (file listings, os.environ, socket.gethostname() outputs) to remote servers.

For each match, report:
- File path within the archive.
- Construct type (e.g., "eval_with_input").
- Line context (snippet and line number).
- Risk label (HIGH, MEDIUM).

### 3.2 Suspicious obfuscation and encoding

Check for potential obfuscation:

- Long base64, zlib, or marshal‑encoded strings followed by immediate decode‑and‑exec‑like evaluation.
- Extremely long, single‑line expressions or strings that look like encoded payloads.
- Unusual naming patterns (e.g., random‑looking module names, or __‑heavy identifiers used for control flow).
- Hex‑encoded or rot13‑encoded strings that decode to executable code.

If found, flag as suspicious_obfuscation and recommend manual review.

### 3.3 Typosquatting and dependency confusion indicators

Check for signs the package may be a typosquat or dependency confusion attack:

- Package name in PKG‑INFO or setup.py closely resembles a popular package (e.g., "reqeusts" vs "requests").
- install_requires listing internal/private‑looking package names that could trigger dependency confusion.
- Empty or minimal functional code combined with suspicious setup.py activity — a common pattern in malicious placeholder packages.

---

## Step 4: Scan for common vulnerabilities

Look for well‑known vulnerability patterns across all Python files in the archive.

### 4.1 SQL injection candidates

Detect unsafe SQL‑string construction:

- Pattern: f"SELECT ... {user_input}" or "SELECT ... %%s" %% user_input or "SELECT ...".format(user_input).
- If not handled by a parameterized‑query library, flag as potential_sql_injection.

Report: file path, line context, suggested fix (use parameterized queries).

### 4.2 Command‑injection candidates

Check for shell‑command usage with untrusted input:

- os.system("... " + user_input)
- subprocess.*(..., shell=True, ...) with user_input in the command string.
- Flag as potential_command_injection with mitigation advice.

### 4.3 Hardcoded secrets

Search for obvious secret‑like patterns across all files:

- SECRET_KEY=, PASSWORD=, API_KEY=, TOKEN=, AWS_ACCESS_KEY_ID=, etc., with inline plaintext values.
- Private keys or certificates embedded in source files.
- Flag as hardcoded_secret.

### 4.4 Unsafe deserialization

Check for use of:

- pickle.loads(, pickle.load( with data from untrusted sources.
- yaml.load( without Loader=SafeLoader.
- marshal.loads( on external data.
- Flag as unsafe_deserialization.

---

## Step 5: Output format

The skill should return a standardized, easy‑to‑parse result **for the single file being scanned**:

For a sdist (.tar.gz) file:

json
{
  "archive_name": "mypackage-1.0.0.tar.gz",
  "archive_type": "sdist",
  "file": "mypackage-1.0.0/setup.py",
  "file_role": "installation_entry_point",
  "issues": [
    {
      "type": "malicious_install_hook",
      "severity": "CRITICAL",
      "line": 10,
      "snippet": "class CustomInstall(install): ...",
      "description": "Custom install command override executes arbitrary code during pip install."
    }
  ],
  "overall_risk": "CRITICAL"
}

For a wheel (.whl) file:

json
{
  "archive_name": "mypackage-1.0.0-py3-none-any.whl",
  "archive_type": "wheel",
  "file": "mypackage/__init__.py",
  "file_role": "package_init",
  "issues": [
    {
      "type": "suspicious_obfuscation",
      "severity": "HIGH",
      "line": 3,
      "snippet": "exec(__import__('base64').b64decode(...))",
      "description": "Obfuscated payload executed at import time."
    }
  ],
  "overall_risk": "HIGH"
}


If no issues are found, return an empty issues list and overall_risk "CLEAN".

Calculate overall_risk based on:
- If any CRITICAL‑severity issue (malicious install hooks, network during install) → CRITICAL.
- If any HIGH‑severity malware‑like pattern (eval_with_input, suspicious_obfuscation) → HIGH.
- Else if only MEDIUM or LOW‑severity issues → MEDIUM.
- No issues → CLEAN.

---

## Security and safety notes

- Treat **all archive contents as untrusted**; do not exec, eval, or import any code from the archive.
- The archive contents (.tar.gz or .whl) are **pre‑extracted and provided as text** — the scanner never directly handles the archive file or executes its contents.
- For **.whl files**, remember that there is no setup.py. The primary attack surface is **import‑time code** (__init__.py, top‑level modules) and **entry points** (console_scripts).
- Combine this skill with:
  - A full AST‑based SAST tool for production‑grade analysis.
  - External vulnerability scanners (e.g., pip‑audit, bandit, safety) for dependency and broader codebase checks.
  - Hash/signature verification against PyPI to confirm archive integrity.

This skill provides a lightweight, prompt‑driven layer to screen Python .tar.gz and .whl package archives for malware‑like and vulnerability‑prone patterns before installation or further use.
`
	return llms.TextParts(llms.ChatMessageTypeSystem, malwareMessage)
}
