import base64
from storages.base import BaseStorage
from pulpcore.tasking.tasks import repository_name_var, x_quay_auth_var
import oras.client
from django.core.files import File


class OCIStorage(BaseStorage):

    @property
    def username_password(self):
        header = x_quay_auth_var.get(None)
        base64_credentials = header.split(" ")[1]

        # Decode the Base64 string to get the original 'username:password' string
        credentials = base64.b64decode(base64_credentials).decode("utf-8")

        # Split the string to separate username and password
        username, password = credentials.split(":", 1)
        return {"username": username, "password": password}
    def _save(self, name, content):
        client = oras.client.OrasClient()
        client.login(**self.username_password)
        repository_name = repository_name_var.get("dkliban/testrepo")
        target = f"quay.io/{repository_name}:{content.name}"
        client.push(files=[content.file.name], target=target, disable_path_validation=True)
        return target


    def _open(self, name, mode='rb'):
        client = oras.client.OrasClient()
        client.login(**self.username_password)
        res = client.pull(target=name)
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
        res = client.pull(target=artifact_name, return_blob_url=True)
        return res.headers["Location"]
