from aiohttp.web_exceptions import HTTPFailedDependency
from storages.base import BaseStorage
from storages.utils import setting
import oras.client
import oras.oci
import oras.defaults
from django.core.files import File
import tempfile
import os
import requests
import base64


class OCIStorage(BaseStorage):

    def get_default_settings(self):
        return {
            "username": setting("username"),
            "password": setting("password"),
            "repository": setting("repository"),
        }
    
    @property
    def registry(self):
        """Registry is always quay.io"""
        return "quay.io"

    def _get_token_with_push_scope(self, client, registry, repository, username, password):
        """
        Manually request a token with push scope.
        
        This is necessary because the automatic auth flow only gets 'pull' scope,
        but we need 'push' scope to upload blobs.
        """
        token_url = f"https://{registry}/v2/auth"
        token_params = {
            "service": registry,
            "scope": f"repository:{repository}:pull,push"
        }
        basic_auth = base64.b64encode(f"{username}:{password}".encode()).decode()
        token_response = requests.get(
            token_url,
            params=token_params,
            headers={"Authorization": f"Basic {basic_auth}"},
            timeout=30
        )
        
        if token_response.status_code == 200:
            token_data = token_response.json()
            token = token_data.get('token') or token_data.get('access_token')
            client.auth.set_token_auth(token)
            return True
        return False

    def _save(self, name, content):
        """
        Save content as a blob and return the blob reference (registry/repo@digest).
        
        :param name: The name/path for the content (not used for blob storage)
        :param content: Django File object with content to upload
        :return: Blob reference in format: registry/repository@sha256:digest
        """
        # Create ORAS client
        client = oras.client.OrasClient()
        
        # Login to save credentials
        client.login(
            username=self.username,
            password=self.password,
            hostname=self.registry,
            config_path="/tmp/.docker/config.json"
        )
        
        # Get token with push scope
        self._get_token_with_push_scope(
            client, 
            self.registry, 
            self.repository, 
            self.username, 
            self.password
        )
        
        # Get the file path from the content object
        # The content parameter is a Django File object with a 'name' attribute
        file_path = content.file.name
        
        # Create layer metadata (calculates digest, size)
        layer = oras.oci.NewLayer(
            file_path,
            media_type=oras.defaults.default_blob_media_type,
            is_dir=False
        )
        
        # Get container reference
        container = client.get_container(f"{self.registry}/{self.repository}")
        
        # Load auth configs
        client.auth.load_configs(container, configs=["/tmp/.docker/config.json"])
        
        # Upload the blob
        response = client.upload_blob(
            blob=file_path,
            container=container,
            layer=layer
        )
        
        if response.status_code not in [200, 201, 202]:
            raise ValueError(f"Blob upload failed: {response.status_code} - {response.text}")
        
        # Return blob reference: registry/repository@digest
        blob_ref = f"{self.registry}/{self.repository}@{layer['digest']}"
        return blob_ref

    def _open(self, name, mode='rb'):
        """
        Download a blob and return it as a Django File object.
        
        :param name: Blob reference in format: registry/repository@sha256:digest
        :param mode: File open mode (default 'rb')
        :return: Django File object
        """
        # Parse the blob reference to extract digest
        # Format: registry/repository@sha256:digest
        if '@' not in name:
            raise ValueError(f"Invalid blob reference format: {name}")
        
        digest = name.split('@')[1]  # Extract sha256:digest
        
        # Create ORAS client
        client = oras.client.OrasClient()
        
        # Login
        client.login(
            username=self.username,
            password=self.password,
            hostname=self.registry,
            config_path="/tmp/.docker/config.json"
        )
        
        # Get container reference
        container = client.get_container(f"{self.registry}/{self.repository}")
        
        # Load auth configs
        client.auth.load_configs(container, configs=["/tmp/.docker/config.json"])
        
        # Create temporary file path for download
        tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.blob')
        tmp_path = tmp_file.name
        tmp_file.close()
        
        # Download the blob
        client.download_blob(
            container=container,
            digest=digest,
            outfile=tmp_path,
            return_blob_url=False
        )
        
        # Return as Django File object
        return File(open(tmp_path, mode))

    def exists(self, name):
        """
        Check if a blob exists in the registry.
        
        :param name: Blob reference in format: registry/repository@sha256:digest
        :return: True if blob exists, False otherwise
        """
        try:
            # Parse the digest from the blob reference
            if '@' not in name:
                return False
            
            digest = name.split('@')[1]
            
            # Create ORAS client
            client = oras.client.OrasClient()
            
            # Login
            client.login(
                username=self.username,
                password=self.password,
                hostname=self.registry,
                config_path="/tmp/.docker/config.json"
            )
            
            # Get container reference
            container = client.get_container(f"{self.registry}/{self.repository}")
            
            # Load auth configs
            client.auth.load_configs(container, configs=["/tmp/.docker/config.json"])
            
            # Create a minimal layer dict with just the digest
            layer = {"digest": digest}
            
            # Check if blob exists
            return client.blob_exists(layer, container)
        except Exception:
            return False

    def size(self, name):
        """
        Get the size of a blob.
        
        :param name: Blob reference in format: registry/repository@sha256:digest
        :return: Size in bytes
        """
        # Parse the digest from the blob reference
        if '@' not in name:
            raise ValueError(f"Invalid blob reference format: {name}")
        
        digest = name.split('@')[1]
        
        # Create ORAS client
        client = oras.client.OrasClient()
        
        # Login
        client.login(
            username=self.username,
            password=self.password,
            hostname=self.registry,
            config_path="/tmp/.docker/config.json"
        )
        
        # Get container reference
        container = client.get_container(f"{self.registry}/{self.repository}")
        
        # Load auth configs
        client.auth.load_configs(container, configs=["/tmp/.docker/config.json"])
        
        # Use HEAD request to get Content-Length
        response = client.get_blob(container=container, digest=digest, head=True)
        
        if response.status_code == 200:
            return int(response.headers.get('Content-Length', 0))
        
        raise ValueError(f"Failed to get blob size: {response.status_code}")

    def url(self, artifact_name, parameters={}, **kwargs):
        """
        Get the direct URL to a blob.
        
        :param artifact_name: Blob reference in format: registry/repository@sha256:digest
        :return: Direct URL to the blob
        """
        if parameters is None:
            parameters = {}
        # Parse the digest from the blob reference
        if '@' not in artifact_name:
            raise ValueError(f"Invalid blob reference format: {artifact_name}")
        
        digest = artifact_name.split('@')[1]
        
        # Create ORAS client
        client = oras.client.OrasClient()
        
        # Login
        client.login(
            username=self.username,
            password=self.password,
            hostname=self.registry,
            config_path="/tmp/.docker/config.json"
        )
        
        # Get container reference
        container = client.get_container(f"{self.registry}/{self.repository}")
        
        # Load auth configs
        client.auth.load_configs(container, configs=["/tmp/.docker/config.json"])
        
        # Use download_blob with return_blob_url=True to get the URL
        blob_url = client.download_blob(
            container=container,
            digest=digest,
            outfile="",  # Not used when return_blob_url=True
            return_blob_url=True
        )
        
        return blob_url

