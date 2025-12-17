"""

WARNING:
Pulp authentication using a console.redhat.com service account is no longer supported!
Use Red Hat IT Service Account instead.

OAuth2 Client Credentials Authentication Library for Pulp API

This library provides OAuth2 client credentials authentication for the Red Hat Console Pulp API,
replicating the same authentication flow used by pulp-cli.

Usage:
    from pulp_oauth2_auth import PulpOAuth2Session
    
    # Create authenticated session
    session = PulpOAuth2Session(
        client_id="your_client_id",
        client_secret="your_client_secret",
        base_url="https://console.redhat.com",
        scopes=["api.console"]
    )
    
    # Make API calls
    response = session.get("/api/pulp/api/v3/status/")
    print(response.json())
"""

import typing as t
from datetime import datetime, timedelta
import requests


class OAuth2ClientCredentialsAuth(requests.auth.AuthBase):
    """
    OAuth2 Client Credentials Grant authentication flow implementation.
    Based on pulp-cli's authentication mechanism.
    
    This handles automatic token retrieval, refresh, and 401 retry logic.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_url: str,
        scopes: t.Optional[t.List[str]] = None,
        verify: t.Optional[t.Union[str, bool]] = None,
    ):
        """
        Initialize OAuth2 authentication.
        
        Args:
            client_id: OAuth2 client ID
            client_secret: OAuth2 client secret
            token_url: URL for token endpoint (e.g., "https://console.redhat.com/token")
            scopes: List of OAuth2 scopes to request
            verify: SSL certificate verification (True/False or path to CA bundle)
        """
        self._token_server_auth = requests.auth.HTTPBasicAuth(client_id, client_secret)
        self._token_url = token_url
        self._scopes = scopes or []
        self._verify = verify

        self._access_token: t.Optional[str] = None
        self._expire_at: t.Optional[datetime] = None

    def __call__(self, request: requests.PreparedRequest) -> requests.PreparedRequest:
        """Apply OAuth2 authentication to the request."""
        # Check if we need to fetch/refresh token
        if self._expire_at is None or self._expire_at < datetime.now():
            self._retrieve_token()

        assert self._access_token is not None
        request.headers["Authorization"] = f"Bearer {self._access_token}"

        # Register 401 handler for automatic token refresh
        request.register_hook("response", self._handle401)
        return request

    def _handle401(
        self,
        response: requests.Response,
        **kwargs: t.Any,
    ) -> requests.Response:
        """Handle 401 responses by refreshing token and retrying once."""
        if response.status_code != 401:
            return response

        # Token probably expired, get a new one
        self._retrieve_token()
        assert self._access_token is not None

        # Consume content and release the original connection
        response.content
        response.close()
        
        # Prepare new request with fresh token
        prepared_new_request = response.request.copy()
        prepared_new_request.headers["Authorization"] = f"Bearer {self._access_token}"

        # Avoid infinite loop by removing the 401 handler
        prepared_new_request.deregister_hook("response", self._handle401)

        # Send the new request
        new_response: requests.Response = response.connection.send(prepared_new_request, **kwargs)
        new_response.history.append(response)
        new_response.request = prepared_new_request

        return new_response

    def _retrieve_token(self) -> None:
        """Fetch a new OAuth2 access token."""
        data = {"grant_type": "client_credentials"}
        
        if self._scopes:
            data["scope"] = " ".join(self._scopes)

        response: requests.Response = requests.post(
            self._token_url,
            data=data,
            auth=self._token_server_auth,
            verify=self._verify,
        )

        response.raise_for_status()

        token = response.json()
        self._expire_at = datetime.now() + timedelta(seconds=token["expires_in"])
        self._access_token = token["access_token"]

    @property
    def access_token(self) -> t.Optional[str]:
        """Get the current access token (for debugging/inspection)."""
        return self._access_token

    @property
    def expires_at(self) -> t.Optional[datetime]:
        """Get the token expiration time (for debugging/inspection)."""
        return self._expire_at


class PulpOAuth2Session(requests.Session):
    """
    Requests session with built-in OAuth2 authentication for Pulp API.
    
    This provides a drop-in replacement for requests.Session that automatically
    handles OAuth2 authentication for Red Hat Console's Pulp API.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        base_url: str = "https://console.redhat.com",
        scopes: t.Optional[t.List[str]] = None,
        verify: t.Optional[t.Union[str, bool]] = None,
        **kwargs
    ):
        """
        Initialize authenticated Pulp API session.
        
        Args:
            client_id: OAuth2 client ID
            client_secret: OAuth2 client secret
            base_url: Base URL for the Pulp API (default: Red Hat Console)
            scopes: OAuth2 scopes (default: ["api.console"])
            verify: SSL verification (default: True)
            **kwargs: Additional arguments passed to requests.Session
        """
        super().__init__(**kwargs)
        
        self.base_url = base_url.rstrip('/')
        self.scopes = scopes or ["api.console"]
        
        # Set up OAuth2 authentication with correct Red Hat SSO token URL
        token_url = "https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token"
        self.auth = OAuth2ClientCredentialsAuth(
            client_id=client_id,
            client_secret=client_secret,
            token_url=token_url,
            scopes=self.scopes,
            verify=verify
        )
        
        # Set verify for the session
        if verify is not None:
            self.verify = verify

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make a request, automatically prepending base_url if needed."""
        # If URL is relative, prepend base_url
        if not url.startswith(('http://', 'https://')):
            url = f"{self.base_url}{url}"
        
        return super().request(method, url, **kwargs)

    def get_token_info(self) -> t.Dict[str, t.Any]:
        """Get current token information for debugging."""
        oauth_auth = self.auth
        if isinstance(oauth_auth, OAuth2ClientCredentialsAuth):
            return {
                "access_token": oauth_auth.access_token,
                "expires_at": oauth_auth.expires_at.isoformat() if oauth_auth.expires_at else None,
                "is_expired": oauth_auth.expires_at < datetime.now() if oauth_auth.expires_at else True,
            }
        return {}


# Convenience function for quick API calls
def create_pulp_session(
    client_id: str,
    client_secret: str,
    base_url: str = "https://console.redhat.com",
    scopes: t.Optional[t.List[str]] = None,
    **kwargs
) -> PulpOAuth2Session:
    """
    Create an authenticated Pulp API session.
    
    Args:
        client_id: OAuth2 client ID
        client_secret: OAuth2 client secret  
        base_url: Base URL for the Pulp API
        scopes: OAuth2 scopes
        **kwargs: Additional session arguments
    
    Returns:
        Authenticated session ready for API calls
    """
    return PulpOAuth2Session(
        client_id=client_id,
        client_secret=client_secret,
        base_url=base_url,
        scopes=scopes,
        **kwargs
    )
