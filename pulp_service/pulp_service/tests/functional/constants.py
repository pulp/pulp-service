# CONTENT GUARD CONSTANTS
CONTENT_GUARD_HEADER_NAME="x-rh-identity"
CONTENT_GUARD_HEADER_VALUE='eyJpZGVudGl0eSI6IHsib3JnX2lkIjogIjE4OTM5NzY0In19' 
CONTENT_GUARD_FEATURES=[ "OPENSHIFT-OCP-x86_64", "RHEL-HA-x86_64" ]
CONTENT_GUARD_FEATURES_NOT_SUBSCRIBED=[ "rhods" ]
CONTENT_GUARD_FILTER='.identity.org_id'

# VULNERABILITY REPORT CONSTANTS
# NPM
NPM_REMOTE_REGISTRY = "https://registry.npmjs.org/"
NPM_SAMPLE_PACKAGE = "cookie/0.5.0"
NPM_VULNERABILITY_PACKAGE = "cookie-0.5.0"
NPM_VULNERABILITY_IDS = ["GHSA-pxg6-pf52-xh8x"]

# PYTHON
PYTHON_REMOTE_REPO = "https://pypi.org/"
PYTHON_REMOTE_INCLUDE = ["django>=5.1"]
PYTHON_VULNERABILITY_PACKAGE = "Django-5.1"
PYTHON_VULNERABILITY_IDS = [
    "GHSA-5hgc-2vfp-mqvc",
    "GHSA-8498-2h75-472j",
    "GHSA-m9g8-fxxm-xg86",
    "GHSA-p3fp-8748-vqfq",
    "GHSA-qcgg-j2x8-h9g8",
    "GHSA-rrqc-c2jx-6jgv",
    "GHSA-wqfg-m96j-85vm",
    "PYSEC-2024-102",
    "PYSEC-2024-156",
    "PYSEC-2024-157",
    "PYSEC-2025-1",
]

# GEM
GEM_REMOTE_REGISTRY = "https://index.rubygems.org/"
GEM_REMOTE_INCLUDE = {"rails": "= 7.0.1"}
GEM_VULNERABILITY_PACKAGE = "rails-7.0.1"
GEM_VULNERABILITY_IDS = ["GHSA-9822-6m93-xqf4"]

# RPM
RPM_SAMPLE_PACKAGE_URL = (
    "https://vault.centos.org/7.0.1406/os/x86_64/Packages/kernel-3.10.0-123.el7.x86_64.rpm"
)
RPM_SAMPLE_RH_CPE = '["cpe:/o:redhat:enterprise_linux:7::workstation"]'
RPM_VULNERABILITY_PACKAGE = "kernel-3.10.0"
RPM_VULNERABILITY_IDS = [
    "RHSA-2014:0678",
    "RHSA-2014:0786",
    "RHSA-2014:0923",
    "RHSA-2014:1023",
    "RHSA-2014:1281",
    "RHSA-2014:1724",
    "RHSA-2014:1971",
    "RHSA-2014:2010",
]

# Sample npm package-json.lock file
NPM_PACKAGE_LOCK_FILE = {
    "name": "sample_file",
    "version": "0.0.1",
    "lockfileVersion": 3,
    "requires": True,
    "packages": {
        "": {"name": "sample_file", "version": "0.0.1", "dependencies": {"cookie": "^0.5.0"}},
        "node_modules/cookie": {
            "version": "0.5.0",
            "resolved": "https://registry.npmjs.org/cookie/-/cookie-0.5.0.tgz",
            "integrity": "sha512-YZ3GUyn/o8gfKJlnlX7g7xq4gyO6OSuhGPKaaGssGB2qgDUS0gPgtTvoyZLTt9Ab6dC4hfc9dV5arkvc/OCmrw==",
            "license": "MIT",
            "engines": {"node": ">= 0.6"},
        },
    },
}

# https://github.com/ossf/osv-schema/blob/e4c58d9a4a9ea2207e4a0dce31c7e1754aa7f1fa/validation/schema.json
OSV_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://raw.githubusercontent.com/ossf/osv-schema/main/validation/schema.json",
    "title": "Open Source Vulnerability",
    "description": "A schema for describing a vulnerability in an open source package. See also https://ossf.github.io/osv-schema/",
    "type": "object",
    "properties": {
        "schema_version": {"type": "string"},
        "id": {"$ref": "#/$defs/prefix"},
        "modified": {"$ref": "#/$defs/timestamp"},
        "published": {"$ref": "#/$defs/timestamp"},
        "withdrawn": {"$ref": "#/$defs/timestamp"},
        "aliases": {"type": ["array", "null"], "items": {"type": "string"}},
        "related": {"type": "array", "items": {"type": "string"}},
        "upstream": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "details": {"type": "string"},
        "severity": {"$ref": "#/$defs/severity"},
        "affected": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "package": {
                        "type": "object",
                        "properties": {
                            "ecosystem": {"$ref": "#/$defs/ecosystemWithSuffix"},
                            "name": {"type": "string"},
                            "purl": {"type": "string"},
                        },
                        "required": ["ecosystem", "name"],
                    },
                    "severity": {"$ref": "#/$defs/severity"},
                    "ranges": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": ["GIT", "SEMVER", "ECOSYSTEM"]},
                                "repo": {"type": "string"},
                                "events": {
                                    "title": "events must contain an introduced object and may contain fixed, last_affected or limit objects",
                                    "type": "array",
                                    "contains": {"required": ["introduced"]},
                                    "items": {
                                        "type": "object",
                                        "oneOf": [
                                            {
                                                "type": "object",
                                                "properties": {"introduced": {"type": "string"}},
                                                "required": ["introduced"],
                                            },
                                            {
                                                "type": "object",
                                                "properties": {"fixed": {"type": "string"}},
                                                "required": ["fixed"],
                                            },
                                            {
                                                "type": "object",
                                                "properties": {"last_affected": {"type": "string"}},
                                                "required": ["last_affected"],
                                            },
                                            {
                                                "type": "object",
                                                "properties": {"limit": {"type": "string"}},
                                                "required": ["limit"],
                                            },
                                        ],
                                    },
                                    "minItems": 1,
                                },
                                "database_specific": {"type": "object"},
                            },
                            "allOf": [
                                {
                                    "title": "GIT ranges require a repo",
                                    "if": {"properties": {"type": {"const": "GIT"}}},
                                    "then": {"required": ["repo"]},
                                },
                                {
                                    "title": "last_affected and fixed events are mutually exclusive",
                                    "if": {
                                        "properties": {
                                            "events": {"contains": {"required": ["last_affected"]}}
                                        }
                                    },
                                    "then": {
                                        "not": {
                                            "properties": {
                                                "events": {"contains": {"required": ["fixed"]}}
                                            }
                                        }
                                    },
                                },
                            ],
                            "required": ["type", "events"],
                        },
                    },
                    "versions": {"type": "array", "items": {"type": "string"}},
                    "ecosystem_specific": {"type": "object"},
                    "database_specific": {"type": "object"},
                },
            },
        },
        "references": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
                            "ADVISORY",
                            "ARTICLE",
                            "DETECTION",
                            "DISCUSSION",
                            "REPORT",
                            "FIX",
                            "INTRODUCED",
                            "GIT",
                            "PACKAGE",
                            "EVIDENCE",
                            "WEB",
                        ],
                    },
                    "url": {"type": "string", "format": "uri"},
                },
                "required": ["type", "url"],
            },
        },
        "credits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "contact": {"type": "array", "items": {"type": "string"}},
                    "type": {
                        "type": "string",
                        "enum": [
                            "FINDER",
                            "REPORTER",
                            "ANALYST",
                            "COORDINATOR",
                            "REMEDIATION_DEVELOPER",
                            "REMEDIATION_REVIEWER",
                            "REMEDIATION_VERIFIER",
                            "TOOL",
                            "SPONSOR",
                            "OTHER",
                        ],
                    },
                },
                "required": ["name"],
            },
        },
        "database_specific": {"type": "object"},
    },
    "required": ["id", "modified"],
    "allOf": [
        {
            "if": {"required": ["severity"]},
            "then": {
                "properties": {
                    "affected": {"items": {"properties": {"severity": {"type": "null"}}}}
                }
            },
        }
    ],
    "$defs": {
        "ecosystemName": {
            "type": "string",
            "title": "Currently supported ecosystems",
            "description": "These ecosystems are also documented at https://ossf.github.io/osv-schema/#affectedpackage-field",
            "enum": [
                "AlmaLinux",
                "Alpine",
                "Android",
                "Bioconductor",
                "Bitnami",
                "Chainguard",
                "ConanCenter",
                "CRAN",
                "crates.io",
                "Debian",
                "GHC",
                "GitHub Actions",
                "Go",
                "Hackage",
                "Hex",
                "Kubernetes",
                "Linux",
                "Mageia",
                "Maven",
                "npm",
                "NuGet",
                "openSUSE",
                "OSS-Fuzz",
                "Packagist",
                "Photon OS",
                "Pub",
                "PyPI",
                "Red Hat",
                "Rocky Linux",
                "RubyGems",
                "SUSE",
                "SwiftURL",
                "Ubuntu",
                "Wolfi",
            ],
        },
        "ecosystemSuffix": {"type": "string", "pattern": ":.+"},
        "ecosystemWithSuffix": {
            "type": "string",
            "title": "Currently supported ecosystems",
            "description": "These ecosystems are also documented at https://ossf.github.io/osv-schema/#affectedpackage-field",
            "pattern": "^(AlmaLinux|Alpine|Android|Bioconductor|Bitnami|Chainguard|ConanCenter|CRAN|crates\\.io|Debian|GHC|GitHub Actions|Go|Hackage|Hex|Kubernetes|Linux|Mageia|Maven|npm|NuGet|openSUSE|OSS-Fuzz|Packagist|Photon OS|Pub|PyPI|Red Hat|Rocky Linux|RubyGems|SUSE|SwiftURL|Ubuntu|Wolfi|GIT)(:.+)?$",
        },
        "prefix": {
            "type": "string",
            "title": "Currently supported home database identifier prefixes",
            "description": "These home databases are also documented at https://ossf.github.io/osv-schema/#id-modified-fields",
            "pattern": "^(ASB-A|PUB-A|ALSA|ALBA|ALEA|BIT|CGA|CURL|CVE|DSA|DLA|ELA|DTSA|GHSA|GO|GSD|HSEC|KUBE|LBSEC|LSN|MAL|MGASA|OSV|openSUSE-SU|PHSA|PSF|PYSEC|RHBA|RHEA|RHSA|RLSA|RXSA|RSEC|RUSTSEC|SUSE-[SRFO]U|UBUNTU|USN|V8)-",
        },
        "severity": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["CVSS_V2", "CVSS_V3", "CVSS_V4", "Ubuntu"]},
                    "score": {"type": "string"},
                },
                "allOf": [
                    {
                        "if": {"properties": {"type": {"const": "CVSS_V2"}}},
                        "then": {
                            "properties": {
                                "score": {
                                    "pattern": "^((AV:[NAL]|AC:[LMH]|Au:[MSN]|[CIA]:[NPC]|E:(U|POC|F|H|ND)|RL:(OF|TF|W|U|ND)|RC:(UC|UR|C|ND)|CDP:(N|L|LM|MH|H|ND)|TD:(N|L|M|H|ND)|[CIA]R:(L|M|H|ND))/)*(AV:[NAL]|AC:[LMH]|Au:[MSN]|[CIA]:[NPC]|E:(U|POC|F|H|ND)|RL:(OF|TF|W|U|ND)|RC:(UC|UR|C|ND)|CDP:(N|L|LM|MH|H|ND)|TD:(N|L|M|H|ND)|[CIA]R:(L|M|H|ND))$"
                                }
                            }
                        },
                    },
                    {
                        "if": {"properties": {"type": {"const": "CVSS_V3"}}},
                        "then": {
                            "properties": {
                                "score": {
                                    "pattern": "^CVSS:3[.][01]/((AV:[NALP]|AC:[LH]|PR:[NLH]|UI:[NR]|S:[UC]|[CIA]:[NLH]|E:[XUPFH]|RL:[XOTWU]|RC:[XURC]|[CIA]R:[XLMH]|MAV:[XNALP]|MAC:[XLH]|MPR:[XNLH]|MUI:[XNR]|MS:[XUC]|M[CIA]:[XNLH])/)*(AV:[NALP]|AC:[LH]|PR:[NLH]|UI:[NR]|S:[UC]|[CIA]:[NLH]|E:[XUPFH]|RL:[XOTWU]|RC:[XURC]|[CIA]R:[XLMH]|MAV:[XNALP]|MAC:[XLH]|MPR:[XNLH]|MUI:[XNR]|MS:[XUC]|M[CIA]:[XNLH])$"
                                }
                            }
                        },
                    },
                    {
                        "if": {"properties": {"type": {"const": "CVSS_V4"}}},
                        "then": {
                            "properties": {
                                "score": {
                                    "pattern": "^CVSS:4[.]0/AV:[NALP]/AC:[LH]/AT:[NP]/PR:[NLH]/UI:[NPA]/VC:[HLN]/VI:[HLN]/VA:[HLN]/SC:[HLN]/SI:[HLN]/SA:[HLN](/E:[XAPU])?(/CR:[XHML])?(/IR:[XHML])?(/AR:[XHML])?(/MAV:[XNALP])?(/MAC:[XLH])?(/MAT:[XNP])?(/MPR:[XNLH])?(/MUI:[XNPA])?(/MVC:[XNLH])?(/MVI:[XNLH])?(/MVA:[XNLH])?(/MSC:[XNLH])?(/MSI:[XNLHS])?(/MSA:[XNLHS])?(/S:[XNP])?(/AU:[XNY])?(/R:[XAUI])?(/V:[XDC])?(/RE:[XLMH])?(/U:(X|Clear|Green|Amber|Red))?$"
                                }
                            }
                        },
                    },
                    {
                        "if": {"properties": {"type": {"const": "Ubuntu"}}},
                        "then": {
                            "properties": {
                                "score": {
                                    "enum": ["negligible", "low", "medium", "high", "critical"]
                                }
                            }
                        },
                    },
                ],
                "required": ["type", "score"],
            },
        },
        "timestamp": {
            "type": "string",
            "format": "date-time",
            "pattern": "[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]+)?Z",
        },
    },
    "additionalProperties": False,
}
