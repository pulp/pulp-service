from pulpcore.app.authentication import JSONHeaderRemoteAuthentication

from django.conf import settings


class RHServiceAccountCertAuthentication(JSONHeaderRemoteAuthentication):

    header = settings.RH_IDENTITY_HEADER
    jq_filter = ".identity.x509.subject_dn"

    def authenticate_header(self, request):
        return "Bearer"
