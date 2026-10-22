"""
Django settings for survey_management.

Reads configuration from environment variables (.env locally, host
dashboard in production) via django-environ. Works out of the box with
SQLite + local media for local development, and switches to Postgres +
S3/R2-compatible storage automatically when the relevant env vars are set.
"""

import sys
import warnings
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    USE_S3=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="dev-insecure-secret-key-change-me")
DEBUG = env("DEBUG")
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["127.0.0.1", "localhost"])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",

    "django_filters",

    "accounts.apps.AccountsConfig",
    "clients",
    "jobs",
    "locations",
    "survey_points",
    "documents",
    "reports",
    "audit",
    "dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "accounts.middleware.ApprovedAccountMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "audit.middleware.AuditLogMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "dashboard.context_processors.notifications",
                "accounts.context_processors.public_assets",
                "accounts.context_processors.super_admin_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- Database -----------------------------------------------------------
# Production: Postgres on Supabase. Use the Supabase *Session pooler* connection
# string (IPv4, works from Render). Set DB_POOLER_TRANSACTION_MODE=True only if you
# deliberately use the transaction pooler (port 6543).
# Falls back to SQLite for local dev if DATABASE_URL is not set.
DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
}

if DATABASES["default"]["ENGINE"].endswith("postgresql"):
    DATABASES["default"].setdefault("OPTIONS", {})["sslmode"] = env("DB_SSLMODE", default="require")
    DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
    DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
    if env.bool("DB_POOLER_TRANSACTION_MODE", default=False):
        DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
        DATABASES["default"]["CONN_MAX_AGE"] = 0

# --- Auth -----------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTHENTICATION_BACKENDS = ["accounts.backends.EmailOrUsernameBackend"]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard:home"
LOGOUT_REDIRECT_URL = "login"

# --- I18N -------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True

# --- Static files (WhiteNoise) -----------------------------------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

if "test" in sys.argv:
    STORAGES["staticfiles"]["BACKEND"] = "django.contrib.staticfiles.storage.StaticFilesStorage"

# --- Media files (local by default, R2/S3 in production) ---------------
USE_S3 = env("USE_S3")

if USE_S3:
    STORAGES["default"] = {"BACKEND": "storages.backends.s3.S3Storage"}
    AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID").strip()
    AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY").strip()
    AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME").strip()
    AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL").strip().rstrip("/")
    endpoint = urlsplit(AWS_S3_ENDPOINT_URL)
    if endpoint.path == f"/{AWS_STORAGE_BUCKET_NAME}":
        AWS_S3_ENDPOINT_URL = urlunsplit((endpoint.scheme, endpoint.netloc, "", "", ""))
        warnings.warn(
            "AWS_S3_ENDPOINT_URL included the bucket path; using the account-level R2 endpoint instead.",
            RuntimeWarning,
        )
    elif endpoint.path:
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured("AWS_S3_ENDPOINT_URL must be the account-level endpoint without a bucket path.")
    AWS_S3_REGION_NAME = env("AWS_S3_REGION_NAME", default="auto")
    AWS_S3_SIGNATURE_VERSION = "s3v4"
    AWS_S3_ADDRESSING_STYLE = "path"
    AWS_S3_FILE_OVERWRITE = False
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = True  # never hand out permanent public URLs
else:
    STORAGES["default"] = {"BACKEND": "django.core.files.storage.FileSystemStorage"}
    MEDIA_URL = "media/"
    MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Security (relaxed in DEBUG, tightened when DEBUG=False) -----------
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 7
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True

# --- File upload limits (survey docs/photos) ----------------------------
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024  # 20MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024

# --- Email (password reset) ---------------------------------------------
# Dev default prints emails to the console. For real delivery set
# EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend plus the EMAIL_* values.
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="SwiftVertex Geomatics <no-reply@localhost>")
ACCOUNT_STATUS_EMAILS = env.bool("ACCOUNT_STATUS_EMAILS", default=False)

# --- Public pages -----------------------------------------------------------
# Only shown as links on the sign-up form when set.
TERMS_URL = env("TERMS_URL", default="")
PRIVACY_URL = env("PRIVACY_URL", default="")

# --- Survey file archive (documents) --------------------------------------
# With USE_S3=True files go to the PRIVATE Cloudflare R2 bucket named above
# (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_STORAGE_BUCKET_NAME / AWS_S3_ENDPOINT_URL).
# Otherwise they go to a private folder on disk (development only).
OBJECT_STORE_ROOT = env("OBJECT_STORE_ROOT", default=str(BASE_DIR / "private_files"))
MAX_UPLOAD_MB = env.int("MAX_UPLOAD_MB", default=250)
VERIFY_READBACK_MAX_MB = env.int("VERIFY_READBACK_MAX_MB", default=100)  # re-read + re-hash right after upload up to this size
OVERWRITE_RETENTION_DAYS = env.int("OVERWRITE_RETENTION_DAYS", default=30)
DOWNLOAD_URL_SECONDS = env.int("DOWNLOAD_URL_SECONDS", default=300)
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"

LOGGING = {
    "version": 1,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"django.db.backends": {"handlers": ["console"], "level": "DEBUG"}},
}