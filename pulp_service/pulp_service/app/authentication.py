from pulpcore.app.authentication import JSONHeaderRemoteAuthentication


class RHServiceAccountCertAuthentication(JSONHeaderRemoteAuthentication):

    header = "HTTP_X_RH_IDENTITY"
    jq_filter = ".identity.x509.subject_dn"

    def authenticate_header(self, request):
        return "Bearer"
