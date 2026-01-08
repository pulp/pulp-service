import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from pulpcore.app.authentication import JSONHeaderRemoteAuthentication


_logger = logging.getLogger(__name__)


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
    """
    Authenticate users via SAML email from RH Identity header.
    Used for pulp-mgmt admin interface with session support.
    """

    header = "HTTP_X_RH_IDENTITY"
    jq_filter = ".identity.associate.email"

    def get_user(self, user_id):
        """
        Required method for Django authentication backends.
        Returns a user instance given a user_id (primary key).
        """
        User = get_user_model()
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            _logger.warning(f"User with id {user_id} not found in get_user()")
            return None


class PublicDomainReadAuthentication(JSONHeaderRemoteAuthentication):
    """
    Authentication class that allows anonymous GET requests to domains starting with 'public-'.
    
    For public domains (domains whose name starts with 'public-'):
    - GET requests are allowed without authentication (returns AnonymousUser)
    - All other request methods fall through to the next authentication class
    
    For non-public domains:
    - All requests fall through to the next authentication class
    
    This should be placed first in the authentication chain to allow
    unauthenticated read access to public content.
    """

    def authenticate(self, request):
        """
        Check if request is a GET to a public domain. If so, allow anonymous access.
        Otherwise, return None to pass to the next authentication class.
        """
        # Only handle GET requests
        if request.method != 'GET':
            return None
        
        # Get the domain from the request
        domain = getattr(request, 'pulp_domain', None)
        
        if not domain:
            # No domain in request, pass to next authenticator
            return None
        
        # Check if domain name starts with 'public-'
        if domain.name.startswith('public-'):
            _logger.debug(f"Allowing anonymous GET access to public domain: {domain.name}")
            # Return AnonymousUser to allow unauthenticated access
            return (AnonymousUser(), None)
        
        # Not a public domain, pass to next authenticator
        return None
