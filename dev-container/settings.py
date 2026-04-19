CONTENT_ORIGIN = "http://localhost:24816"
CONTENT_PATH_PREFIX = "/pulp/content/"
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

MEDIA_ROOT = "/var/lib/pulp/media/"
DEFAULT_FILE_STORAGE = "pulpcore.app.models.storage.FileSystem"
WORKING_DIRECTORY = "/var/lib/pulp/tmp/"

ALLOWED_CONTENT_CHECKSUMS = ["sha224", "sha256", "sha384", "sha512"]

TOKEN_AUTH_DISABLED = True
