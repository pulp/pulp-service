from pulpcore.app.authentication import JSONHeaderRemoteAuthentication


class RHServiceAccountCertAuthentication(JSONHeaderRemoteAuthentication):

    header = "HTTP_X_RH_IDENTITY"
    jq_filter = ".identity.x509.subject_dn"

    def authenticate_header(self, request):
        return "Bearer"


class RHEntitlementCertAuthentication(JSONHeaderRemoteAuthentication):

    header = "HTTP_X_RH_IDENTITY"
    jq_filter = ".identity.org_id"

    def authenticate_header(self, request):
        return "Bearer"


class RHSamlAuthentication(JSONHeaderRemoteAuthentication):

    header = "HTTP_X_RH_IDENTITY"
    jq_filter = ".identity.associate.email"
