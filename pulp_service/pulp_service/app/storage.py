import base64
from storages.base import BaseStorage
from storages.utils import setting
import oras.client
from django.core.files import File


class OCIStorage(BaseStorage):

    def get_default_settings(self):
        return {"username": setting("username"),
                "password": setting("password"),
                "repository": setting("repository"),
        }

    @property
    def username_password(self):
        return {"username": self.username, "password": self.password, "config_path": "/tmp/.docker/config.json"}

    def _save(self, name, content):
        client = oras.client.OrasClient()
        client.login(**self.username_password)
        target = f"quay.io/{self.repository}:{content.hashers['sha256'].hexdigest()}"
        client.push(files=[content.file.name], target=target, disable_path_validation=True, config_path="/tmp/.docker/config.json")
        return target


    def _open(self, name, mode='rb'):
        client = oras.client.OrasClient()
        client.login(**self.username_password)
        res = client.pull(target=name, config_path="/tmp/.docker/config.json")
        return File(open(res[0], mode))

    def exists(self, name):
        # TODO: implement a check
        return False

    def size(self, name):
        return 1

    def url(self, artifact_name, parameters={}, **kwargs):
        client = oras.client.OrasClient()
        client.login(**self.username_password)

        # Pull
        res = client.pull(target=artifact_name, return_blob_url=True, config_path="/tmp/.docker/config.json")
        return res.headers["Location"]
