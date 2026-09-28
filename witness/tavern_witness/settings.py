import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
DEBUG = os.environ.get("WITNESS_DEBUG") == "1"
SECRET_KEY = os.environ.get("WITNESS_SECRET_KEY", "local-witness-development-only")
if not DEBUG and SECRET_KEY == "local-witness-development-only":
    raise RuntimeError("WITNESS_SECRET_KEY is required outside local development")

ALLOWED_HOSTS = [host.strip() for host in os.environ.get("WITNESS_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if host.strip()]
ROOT_URLCONF = "tavern_witness.urls"
WSGI_APPLICATION = "tavern_witness.wsgi.application"
INSTALLED_APPS = ["witness_core"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "django.middleware.common.CommonMiddleware"]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("WITNESS_DB_PATH", str(BASE_DIR / "witness.sqlite3")),
        "OPTIONS": {"timeout": 20, "transaction_mode": "IMMEDIATE"},
        "TEST": {
            "NAME": os.environ.get(
                "WITNESS_TEST_DB_PATH",
                str(Path(os.environ.get("TEMP", "/tmp")) / f"neon-witness-test-{os.getpid()}.sqlite3"),
            ),
        },
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
WITNESS_INITIAL_NODE = os.environ.get("WITNESS_INITIAL_NODE", "154")
WITNESS_LEASE_SECONDS = int(os.environ.get("WITNESS_LEASE_SECONDS", "60"))
WITNESS_CLOCK_SKEW_SECONDS = int(os.environ.get("WITNESS_CLOCK_SKEW_SECONDS", "2"))
WITNESS_PUBLIC_STATUS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("WITNESS_PUBLIC_STATUS_ORIGINS", "").split(",")
    if origin.strip()
]
WITNESS_NODE_PUBLIC_KEYS = os.environ.get("WITNESS_NODE_PUBLIC_KEYS", "{}")
WITNESS_SIGNING_PRIVATE_KEY = os.environ.get("WITNESS_SIGNING_PRIVATE_KEY", "")
if not DEBUG and not WITNESS_SIGNING_PRIVATE_KEY:
    raise RuntimeError("WITNESS_SIGNING_PRIVATE_KEY is required outside local development")
