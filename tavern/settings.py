import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.environ.get("TAVERN_DEBUG") == "1"
INSECURE_HTTP = os.environ.get("TAVERN_INSECURE_HTTP") == "1"
TAVERN_APP_VERSION = os.environ.get("TAVERN_APP_VERSION", "20260925.1")
TAVERN_FEDERATION_SITE_NAME = os.environ.get("TAVERN_FEDERATION_SITE_NAME", "霓虹酒馆分站")
TAVERN_FEDERATION_SITE_URL = os.environ.get("TAVERN_FEDERATION_SITE_URL", "").rstrip("/")
TAVERN_FEDERATION_SITE_KEY = os.environ.get("TAVERN_FEDERATION_SITE_KEY", "").strip().casefold()
TAVERN_FEDERATION_REQUIRED = True
TAVERN_FEDERATION_ROLE = "satellite"
TAVERN_DEPLOYMENT_SERVER_IP = os.environ.get("TAVERN_DEPLOYMENT_SERVER_IP", "")
TAVERN_DR_ENABLED = os.environ.get("TAVERN_DR_ENABLED") == "1"
TAVERN_NODE_ID = os.environ.get("TAVERN_NODE_ID", "local")
TAVERN_DR_READ_ONLY = os.environ.get("TAVERN_DR_READ_ONLY") == "1"
TAVERN_DR_LEASE_PATH = os.environ.get("TAVERN_DR_LEASE_PATH", "")
TAVERN_WITNESS_PUBLIC_KEY = os.environ.get("TAVERN_WITNESS_PUBLIC_KEY", "")
TAVERN_WITNESS_URL = os.environ.get("TAVERN_WITNESS_URL", "")
TAVERN_WITNESS_CA_BUNDLE = os.environ.get("TAVERN_WITNESS_CA_BUNDLE", "")
TAVERN_DR_NODE_PRIVATE_KEY = os.environ.get("TAVERN_DR_NODE_PRIVATE_KEY", "")
TAVERN_DR_TRUSTED_NODE_PUBLIC_KEYS = os.environ.get("TAVERN_DR_TRUSTED_NODE_PUBLIC_KEYS", "{}")
TAVERN_DR_ENCRYPTION_KEY = os.environ.get("TAVERN_DR_ENCRYPTION_KEY", "")
TAVERN_DR_ENCRYPTION_KEY_VERSION = os.environ.get("TAVERN_DR_ENCRYPTION_KEY_VERSION", "v1")
TAVERN_DR_REPLICA_ROOT = Path(os.environ.get("TAVERN_DR_REPLICA_ROOT", str(BASE_DIR / "dr-replica")))
TAVERN_DR_USE_REPLICA_ACTIVE = os.environ.get("TAVERN_DR_USE_REPLICA_ACTIVE") == "1"
if TAVERN_DR_ENABLED and not TAVERN_DR_USE_REPLICA_ACTIVE:
    raise RuntimeError("启用双站容灾必须使用隔离的活动副本目录")
if TAVERN_DR_ENABLED and not (TAVERN_DR_REPLICA_ROOT / "active" / "database.sqlite3").is_file():
    raise RuntimeError("灾备活动数据库尚未初始化，拒绝启动")
TAVERN_DR_SNAPSHOT_DIR = Path(os.environ.get("TAVERN_DR_SNAPSHOT_DIR", str(BASE_DIR / "dr-snapshots")))
TAVERN_DR_SNAPSHOT_LOCK_PATH = os.environ.get("TAVERN_DR_SNAPSHOT_LOCK_PATH", str(BASE_DIR / "dr-snapshot.lock"))
TAVERN_DR_STATUS_PATH = Path(os.environ.get("TAVERN_DR_STATUS_PATH", str(BASE_DIR / "dr-replication-status.json")))
TAVERN_DR_ROLE_PATH = Path(os.environ.get("TAVERN_DR_ROLE_PATH", str(BASE_DIR / "dr-role.json")))
TAVERN_DR_PEER_URL = os.environ.get("TAVERN_DR_PEER_URL", "")
TAVERN_DR_ALLOW_HTTP_PEER = os.environ.get("TAVERN_DR_ALLOW_HTTP_PEER") == "1"
TAVERN_DR_HTTP_PEER_HOSTS = [host.strip().lower() for host in os.environ.get("TAVERN_DR_HTTP_PEER_HOSTS", "").split(",") if host.strip()]
TAVERN_DR_MAX_SNAPSHOT_BYTES = int(os.environ.get("TAVERN_DR_MAX_SNAPSHOT_BYTES", str(2 * 1024 * 1024 * 1024)))
TAVERN_DR_STANDBY_NODE_ID = os.environ.get("TAVERN_DR_STANDBY_NODE_ID", "")
TAVERN_DR_STANDBY_ORIGIN = os.environ.get("TAVERN_DR_STANDBY_ORIGIN", "").rstrip("/")
TAVERN_DR_STATUS_ALLOWED_ORIGINS = [origin.strip() for origin in os.environ.get("TAVERN_DR_STATUS_ALLOWED_ORIGINS", "").split(",") if origin.strip() and origin.strip() != "*"]
SECRET_KEY = os.environ.get("TAVERN_SECRET_KEY", "unsafe-local-development-key")
if not DEBUG and SECRET_KEY == "unsafe-local-development-key":
    raise RuntimeError("TAVERN_SECRET_KEY is required in production")
if not DEBUG and not os.environ.get("TAVERN_ENCRYPTION_KEY"):
    raise RuntimeError("TAVERN_ENCRYPTION_KEY is required in production")

ALLOWED_HOSTS = os.environ.get("TAVERN_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("TAVERN_CSRF_ORIGINS", "").split(",")
    if origin.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "core.disaster_recovery_middleware.DisasterRecoveryWriteMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.PolicyConsentMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
if not DEBUG:
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "tavern.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "frontend_dist"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]
WSGI_APPLICATION = "tavern.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "core.db.backends.sqlite3",
        "NAME": str(TAVERN_DR_REPLICA_ROOT / "active" / "database.sqlite3") if TAVERN_DR_USE_REPLICA_ACTIVE else os.environ.get("TAVERN_DB_PATH", str(BASE_DIR / "db.sqlite3")),
        "OPTIONS": {"timeout": 20},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = Path(os.environ.get("TAVERN_STATIC_ROOT", str(BASE_DIR / "collected_static")))
STATICFILES_DIRS = [("assets", BASE_DIR / "frontend_dist" / "assets")] if (BASE_DIR / "frontend_dist" / "assets").exists() else []
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}
MEDIA_ROOT = (TAVERN_DR_REPLICA_ROOT / "active" / "media") if TAVERN_DR_USE_REPLICA_ACTIVE else Path(os.environ.get("TAVERN_MEDIA_ROOT", str(BASE_DIR / "media")))
MEDIA_URL = "/api/avatars/"
DATA_UPLOAD_MAX_MEMORY_SIZE = 21 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 3 * 1024 * 1024

SESSION_COOKIE_NAME = "neon_tavern_session"
CSRF_COOKIE_NAME = "neon_tavern_csrf"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG and not INSECURE_HTTP
CSRF_COOKIE_SECURE = not DEBUG and not INSECURE_HTTP
SESSION_COOKIE_AGE = 14 * 24 * 60 * 60
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = not DEBUG and not INSECURE_HTTP
SECURE_HSTS_SECONDS = 3600 if not DEBUG and not INSECURE_HTTP else 0
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
