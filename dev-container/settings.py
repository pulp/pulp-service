CONTENT_ORIGIN = "http://localhost:24816"
CONTENT_PATH_PREFIX = "/api/pulp-content/"
PYPI_API_PATH_PREFIX = "/api/pypi/"
DOMAIN_ENABLED = True
SECRET_KEY = "dev-secret-key-not-for-production"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "pulp",
        "USER": "pulp",
        "PASSWORD": "",
        "HOST": "localhost",
        "PORT": "5432",
    }
}

REDIS_URL = "redis://localhost:6379/0"
CACHE_ENABLED = True
WORKER_TYPE = "redis"

MEDIA_ROOT = "/var/lib/pulp/media/"
DEFAULT_FILE_STORAGE = "pulpcore.app.models.storage.FileSystem"
WORKING_DIRECTORY = "/var/lib/pulp/tmp/"

ALLOWED_CONTENT_CHECKSUMS = ["sha224", "sha256", "sha384", "sha512"]

TOKEN_AUTH_DISABLED = True

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.RemoteUserBackend",
    "django.contrib.auth.backends.ModelBackend",
    "pulp_service.app.authentication.RHSamlAuthentication",
]

REST_FRAMEWORK__DEFAULT_AUTHENTICATION_CLASSES = (
    "rest_framework.authentication.BasicAuthentication",
    "rest_framework.authentication.SessionAuthentication",
    "pulp_service.app.authentication.RHServiceAccountCertAuthentication",
    "pulp_service.app.authentication.RHTermsBasedRegistryAuthentication",
)

REST_FRAMEWORK__DEFAULT_PERMISSION_CLASSES = (
    "pulp_service.app.authorization.DomainBasedPermission",
)

AUTHENTICATION_JSON_HEADER = "HTTP_X_RH_IDENTITY"
AUTHENTICATION_JSON_HEADER_JQ_FILTER = ".identity.user.username"
