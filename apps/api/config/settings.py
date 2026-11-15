import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "django-insecure-forge-platform-key-dev-only-change-in-production-1234567890",
)

DEBUG = os.getenv("DJANGO_DEBUG", "True").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    # Forge applications
    "apps.authentication.apps.AuthenticationConfig",
    "apps.schemas.apps.SchemasConfig",
    "apps.records.apps.RecordsConfig",
    "apps.workflows.apps.WorkflowsConfig",
    "apps.outbox.apps.OutboxConfig",
    "apps.webhooks.apps.WebhooksConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Forge Core Middlewares
    "core.middleware.RequestIDMiddleware",
    "core.middleware.TenantContextMiddleware",
    "core.middleware.StructuredLoggingMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Relational Database Configuration (Postgres / SQLite fallback)
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL:
    import dj_database_url

    DATABASES = {"default": dj_database_url.parse(DATABASE_URL)}
else:
    # Use PostgreSQL if explicitly specified via individual env vars, otherwise sqlite for tests/local
    PG_HOST = os.getenv("POSTGRES_HOST")
    if PG_HOST:
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": os.getenv("POSTGRES_DB", "forge_db"),
                "USER": os.getenv("POSTGRES_USER", "forge_user"),
                "PASSWORD": os.getenv("POSTGRES_PASSWORD", "forge_password"),
                "HOST": PG_HOST,
                "PORT": os.getenv("POSTGRES_PORT", "5432"),
            }
        }
    else:
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": BASE_DIR / "db.sqlite3",
            }
        }

AUTH_USER_MODEL = "authentication.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# REST Framework Configuration
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.authentication.authentication.ForgeJWTAuthentication",
        "apps.authentication.authentication.ApiKeyAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "core.throttling.TenantRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "tenant": os.getenv("THROTTLE_RATE_TENANT", "300/min"),
    },
    "UNAUTHENTICATED_USER": None,
}

# MongoDB Persistence Configuration
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/?replicaSet=rs0")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "forge_tenant_data")

# Core Security Secrets
FORGE_SERVER_SECRET = os.getenv(
    "FORGE_SERVER_SECRET",
    "c8f0e5b7a1d34f9a8e2b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f",
)
JWT_SECRET_KEY = os.getenv(
    "JWT_SECRET_KEY",
    "jwt-signing-secret-key-32-chars-long-minimum-secure-forge-key",
)
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_LIFETIME_SECONDS = int(os.getenv("JWT_LIFETIME_SECONDS", "900"))  # 15 min
