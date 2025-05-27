"""WARNING: The setting defaults are for development only. Change them for production via .env file."""

import logging
from pathlib import Path

from environs import Env

env = Env()
env.read_env()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.0/howto/deployment/checklist/

# change in production
SECRET_KEY = env("SECRET_KEY", default="django-insecure-dummy-key")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env.bool("DEBUG", default=True)
LOCAL = env.bool("LOCAL", default=True)

# change in production: example: [".example.com"]
ALLOWED_HOSTS = ["*"]

# Admin definition
DJANGO_ADMINS = env.list("DJANGO_ADMINS", default=["Dummy Name:dummy@example.com"])
# DJANGO_ADMINS=Blake:blake@cyb.org,Alice:alice@cyb.org
ADMINS = [tuple(x.split(":")) for x in DJANGO_ADMINS]


# Application definition

INSTALLED_APPS = [
    "servestatic.runserver_nostatic",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    # allauth
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "allauth.socialaccount.providers.github",
    # corsheaders
    "corsheaders",
    # local
    "accounts",
    "api",
    # file cleanup
    "django_cleanup.apps.CleanupConfig",
]

# Change in production
SECURE_SSL_REDIRECT = env.bool(
    "DJANGO_SECURE_SSL_REDIRECT", default=False
)  # prod: True
SECURE_HSTS_SECONDS = env.int(
    "DJANGO_SECURE_HSTS_SECONDS", default=0
)  # prod: 2592000 (30 days)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(
    "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False
)  # prod: True
SECURE_HSTS_PRELOAD = env.bool(
    "DJANGO_SECURE_HSTS_PRELOAD", default=False
)  # prod: True
SESSION_COOKIE_SECURE = env.bool(
    "DJANGO_SESSION_COOKIE_SECURE", default=False
)  # prod: True
CSRF_COOKIE_SECURE = env.bool("DJANGO_CSRF_COOKIE_SECURE", default=False)  # prod: True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")  # same for prod


MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "servestatic.middleware.ServeStaticMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "api.middleware.add_slash",  # custom middleware
    "corsheaders.middleware.CorsMiddleware",  # corsheaders
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # middleware for allauth
    "allauth.account.middleware.AccountMiddleware",
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
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

ASGI_APPLICATION = "config.asgi.application"

# Database
# https://docs.djangoproject.com/en/5.0/ref/settings/#databases

DATABASES = {
    "default": env.dj_db_url("DATABASE_URL", default="postgres://postgres@db/postgres")
}


# Password validation
# https://docs.djangoproject.com/en/5.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.0/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.0/howto/static-files/


STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"


# Default primary key field type
# https://docs.djangoproject.com/en/5.0/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Email settings

EMAIL_CONSOLE = env.bool("EMAIL_CONSOLE", default=True)  # prod: False

if EMAIL_CONSOLE:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
else:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = env("EMAIL_HOST", default="smtp.gmail.com")  # prod: SMTP server
    EMAIL_PORT = 587
    EMAIL_HOST_USER = env(
        "EMAIL_HOST_USER", default="dummy user"
    )  # prod: SMTP username
    EMAIL_HOST_PASSWORD = env(
        "EMAIL_HOST_PASSWORD", default="dummy password"
    )  # prod: SMTP password
    EMAIL_USE_TLS = True


DEFAULT_FROM_EMAIL = env(
    "DEFAULT_FROM_EMAIL", default="dummy-email@example.com"
)  # prod: SMTP email


# allauth settings
AUTH_USER_MODEL = "accounts.CustomUser"
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "home"

ACCOUNT_AUTHENTICATION_METHOD = "email"
ACCOUNT_CHANGE_EMAIL = True
ACCOUNT_CONFIRM_EMAIL_ON_GET = True
ACCOUNT_EMAIL_NOTIFICATIONS = True
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_EMAIL_VERIFICATION = "optional"
ACCOUNT_EMAIL_SUBJECT_PREFIX = ""

if DEBUG and LOCAL:
    ACCOUNT_DEFAULT_HTTP_PROTOCOL = "http"
else:
    ACCOUNT_DEFAULT_HTTP_PROTOCOL = "https"

ACCOUNT_LOGIN_BY_CODE_ENABLED = True
ACCOUNT_LOGIN_BY_CODE_TIMEOUT = 900
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
ACCOUNT_LOGIN_ON_PASSWORD_RESET = True
ACCOUNT_LOGOUT_ON_GET = True
ACCOUNT_PASSWORD_INPUT_RENDER_VALUE = True
ACCOUNT_PRESERVE_USERNAME_CASING = False
ACCOUNT_SESSION_REMEMBER = True
ACCOUNT_SIGNUP_FORM_HONEYPOT_FIELD = "address"
ACCOUNT_SIGNUP_PASSWORD_ENTER_TWICE = False
ACCOUNT_SIGNUP_REDIRECT_URL = "home"  # "post_signup"
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_USERNAME_REQUIRED = False

SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True
SOCIALACCOUNT_LOGIN_ON_GET = True


# EMEDDING Service
EMBEDDINGS_URL = env("EMBEDDINGS_URL")
# Queries need to be fast, so we use a separate service for embeddings.
ALWAYS_ON_EMBEDDINGS_URL = env("ALWAYS_ON_EMBEDDINGS_URL", default=EMBEDDINGS_URL)
EMBEDDINGS_URL_TOKEN = env("EMBEDDINGS_URL_TOKEN")

# Gotenberg
GOTENBERG_URL = env("GOTENBERG_URL", default="http://gotenberg:3000")

# cors
CORS_ALLOW_ALL_ORIGINS = True

# logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}

logger = logging.getLogger(__name__)

# SENTRY
SENTRY_DSN = env("SENTRY_DSN", default=None)

if SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        traces_sample_rate=0.1,
    )

    logger.info("Sentry is enabled.")

DATA_UPLOAD_MAX_MEMORY_SIZE = 52428800  # 50MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 52428800  # 50MB

# S3
AWS_S3_ACCESS_KEY_ID = env("AWS_S3_ACCESS_KEY_ID", default="dummy_key")
AWS_S3_SECRET_ACCESS_KEY = env("AWS_S3_SECRET_ACCESS_KEY", default="dummy_key")
AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME", default="dummy_bucket")
AWS_S3_REGION_NAME = env("AWS_S3_REGION_NAME", default="us-east-1")

# Svix
SVIX_TOKEN = env("SVIX_TOKEN", default="")

# SENTRY
SENTRY_DSN = env("SENTRY_DSN", default=None)

if SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        traces_sample_rate=0.1,
    )

    logger.info("Sentry is enabled.")

# PROXY
PROXY_URL = env("PROXY_URL", default=None)

STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": AWS_STORAGE_BUCKET_NAME,
            "region_name": AWS_S3_REGION_NAME # "us-east-1",  # e.g., 'us-west-1'
            "default_acl": None,  # Makes files private by default
            "querystring_auth": True,  # Requires signed URLs for access
            "querystring_expire": 3600,  # Expiry time for signed URLs (1 hour)
        },
    },
    "staticfiles": {
        "BACKEND": "servestatic.storage.CompressedManifestStaticFilesStorage",
    },
}
