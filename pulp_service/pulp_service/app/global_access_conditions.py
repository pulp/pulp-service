from pulpcore.plugin.util import get_domain_pk

from pulp_service.app.authorization import DomainBasedPermission


def has_domain_org_access(request, _view, _action):
    user = request.user
    if not user.is_authenticated:
        return False

    perm = DomainBasedPermission()
    domain_pk = get_domain_pk()
    decoded_header = perm.get_decoded_identity_header(request)
    org_id = perm.get_org_id(decoded_header)
    return perm._has_domain_access(domain_pk, org_id, user)
