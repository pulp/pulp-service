from types import SimpleNamespace

OSV_QUERY_URL = "https://api.osv.dev/v1/query"
OSV_QUERY_BATCH_URL = "https://api.osv.dev/v1/querybatch"
RH_REPO_TO_CPE_URL = "https://www.redhat.com/security/data/metrics/repository-to-cpe.json"
PKG_ECOSYSTEM = SimpleNamespace(
    rpm="Red Hat", npm="npm", maven="Maven", python="PyPI", gem="RubyGems"
)
