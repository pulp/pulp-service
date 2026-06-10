import re

CONTENT_LOG_REGEX = re.compile(
    r"(?P<src_ip>\S+)\s+"
    r"\[(?P<timestamp>[^\]]+)\]\s+"
    r'"(?P<method>\S+)\s+(?P<path>\S+)\s+HTTP/\d\.\d"\s+'
    r"(?P<status>\d+)\s+"
    r"(?P<bytes>\S+)\s+"
    r'"(?P<referer>[^"]*)"\s+'
    r'"(?P<user_agent>[^"]*)"\s+'
    r'cache:"(?P<cache>[^"]*)"\s+'
    r'artifact_size:"(?P<artifact_size>[^"]*)"\s+'
    r'rh_org_id:"(?P<rh_org_id>[^"]*)"\s+'
    r'x_forwarded_for:"(?P<x_forwarded_for>[^"]*)"'
)

# From pulp_python/app/utils.py:80-85 (regex sourced from pip)
WHEEL_REGEX = re.compile(
    r"""^(?P<name>.+?)-(?P<version>.*?)
    ((-(?P<build>\d[^-]*?))?-(?P<pyver>.+?)-(?P<abi>.+?)-(?P<plat>.+?)
    \.whl|\.dist-info)$""",
    re.VERBOSE,
)

CONTENT_TYPE_EXTENSIONS = {
    "python": (".whl", ".whl.metadata"),
    "rpm": (".rpm",),
}

CONTENT_PATH_PREFIX = "/api/pulp-content/"


def parse_content_log_line(message):
    match = CONTENT_LOG_REGEX.search(message)
    if match is None:
        return None
    return match.groupdict()


def parse_content_path(path):
    if not path.startswith(CONTENT_PATH_PREFIX):
        return None
    remainder = path[len(CONTENT_PATH_PREFIX):]
    segments = remainder.split("/")
    if len(segments) < 2:
        return None
    domain = segments[0]
    filename = segments[-1]
    distribution = "/".join(segments[1:-1])
    return {
        "domain": domain,
        "distribution": distribution,
        "artifact_path": path,
        "filename": filename,
    }


def matches_content_type(filename, content_type):
    extensions = CONTENT_TYPE_EXTENSIONS.get(content_type, ())
    return any(filename.endswith(ext) for ext in extensions)


def parse_wheel_filename(filename):
    base_filename = filename
    if filename.endswith(".whl.metadata"):
        base_filename = filename[:-len(".metadata")]

    match = WHEEL_REGEX.match(base_filename)
    if match is None:
        return None
    groups = match.groupdict()
    return {
        "package_name": groups["name"],
        "package_version": groups["version"],
        "build_tag": groups.get("build"),
        "pyver": groups.get("pyver"),
        "abi": groups.get("abi"),
        "architecture": groups.get("plat"),
    }


# From pulp_rpm/app/depsolving.py:69-99
def _parse_nevr(name):
    if name.count("-") < 2:
        raise ValueError("failed to parse nevr '%s' not a valid nevr" % name)
    release_dash_pos = name.rfind("-")
    release = name[release_dash_pos + 1:]
    name_epoch_version = name[:release_dash_pos]
    name_dash_pos = name_epoch_version.rfind("-")
    package_name = name_epoch_version[:name_dash_pos]
    epoch_version = name_epoch_version[name_dash_pos + 1:].split(":")
    if len(epoch_version) == 1:
        epoch = 0
        version = epoch_version[0]
    elif len(epoch_version) == 2:
        epoch = int(epoch_version[0])
        version = epoch_version[1]
    else:
        raise ValueError("failed to parse nevr '%s' not a valid nevr" % name)
    return package_name, epoch, version, release


# From pulp_rpm/app/depsolving.py:50-66
def _parse_nevra(name):
    if name.count(".") < 1:
        raise ValueError("failed to parse nevra '%s' not a valid nevra" % name)
    arch_dot_pos = name.rfind(".")
    arch = name[arch_dot_pos + 1:]
    return _parse_nevr(name[:arch_dot_pos]) + (arch,)


def parse_rpm_filename(filename):
    if not filename.endswith(".rpm"):
        return None
    nevra_string = filename[:-len(".rpm")]
    try:
        package_name, epoch, version, release, arch = _parse_nevra(nevra_string)
    except ValueError:
        return None
    return {
        "package_name": package_name,
        "package_version": version,
        "architecture": arch,
        "epoch": epoch,
        "release": release,
    }
