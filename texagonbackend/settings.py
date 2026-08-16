import os
from pathlib import Path
from dotenv import load_dotenv
from decimal import Decimal
from corsheaders.defaults import default_headers
from .brand import get_brand_config, get_active_brand, brand_config, APP_BRAND

load_dotenv()
# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "CHANGE_ME")
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", brand_config["frontend_origin"])
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()] or ["*"]
PAYMENT_TEST = os.environ.get("PAYMENT_TEST", "1") == "1"
LOGO_URL = os.environ.get("LOGO_URL", brand_config["logo_url"])
SITE_NAME = brand_config["full_name"]

TEST_KEY_SECRET = os.environ.get("TEST_KEY_SECRET", "CHANGE_ME")
FLW_SECRET_KEY = os.environ.get("FLW_SECRET_KEY", "CHANGE_ME")

KONNECT_LOGOUT_URL = os.environ.get("KONNECT_LOGOUT_URL", f"{FRONTEND_ORIGIN}/k-dashboard/")
KONNECT_TOKEN = os.environ.get("KONNECT_TOKEN", "H9LBxlRkBWs2hb1mnZ0v2wzfhOqwfjCFaK73Jx99")
KONNECT_MAX_ROOM = 25
TAX_RATE = Decimal("0.08")
FLAT_SHIPPING = Decimal("1000.99")

ACCOUNT_SIGNUP_EMAIL = os.environ.get("ACCOUNT_SIGNUP_EMAIL", "1") == "1"

ACCOUNT_EMAIL_NOTIFICATION = os.environ.get("ACCOUNT_EMAIL_NOTIFICATION", "1") == "1"

CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    'http://127.0.0.1:3000',
    "http://localhost:3001",
    'http://127.0.0.1:3001',
    "https://texagonbackend.onrender.com",
    "https://texagon.onrender.com",
    "https://learn.techxagonacademy.com",
    "https://techxagonacademy.com",
    "https://learn.nimet.gov.ng",
    "https://nimet.gov.ng",
    "https://nimet-web.onrender.com",
]

CORS_ALLOW_HEADERS = list(default_headers) + [
    "x-session-token",
]

#CORS_ALLOW_METHODS = list(default_methods)  # optional but fine

CORS_ALLOW_CREDENTIALS = True

if os.environ.get('LOCAL') == "0":
    CSRF_TRUSTED_ORIGINS = [
        "https://texagonbackend.esm.name.ng",
        "https://texagonbackend.epichouse.online",
        "https://texagonbackend.onrender.com",
        "http://127.0.0.1:9098",
        "http://localhost",
        "http://127.0.0.1:3000",
        "https://learn.techxagonacademy.com",
        "https://techxagonacademy.com",
        "https://learn.nimet.gov.ng",
        "https://nimet.gov.ng",
        "https://nimet-web.onrender.com",
    ]

    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    # Allow overriding SSL redirect via env var (useful for local S3 testing with LOCAL=0)
    SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "1") == "1"
    SESSION_COOKIE_SECURE = os.environ.get("SECURE_SSL_REDIRECT", "1") == "1"
    CSRF_COOKIE_SECURE = os.environ.get("SECURE_SSL_REDIRECT", "1") == "1"


SESSION_COOKIE_SAMESITE = "None"
SESSION_COOKIE_SECURE = True  # requires https
CSRF_COOKIE_SAMESITE = "None"
CSRF_COOKIE_SECURE = True

# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    "storages",
    "cloudinary",
    "cloudinary_storage",
    "core",
    "codeide",
    "accounts",
    "orgs",
    "academics",
    "learning",
    "assessments",
    "attendance",
    "gamification",
    "live",
    "store",
    "billing",
    "notifications",
    "api",
    "konnect",
    'corefrontend',
    'blog',
    'projects',
    'app_updates',
    "rest_framework",
    "rest_framework_api_key",
    'django.contrib.sites',
    'django.contrib.sitemaps',
    'django.contrib.humanize',
    "offline_work",
]

SITE_ID = 1

# Prevent Django from trying to redirect POST requests to trailing-slash URLs.
# All API URL patterns already include trailing slashes explicitly.
APPEND_SLASH = False

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# Enable WhiteNoise ONLY in production
if os.environ.get("LOCAL") == "0":
    MIDDLEWARE.insert(
        1,  # must come right after SecurityMiddleware
        'whitenoise.middleware.WhiteNoiseMiddleware'
    )


ROOT_URLCONF = 'texagonbackend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / "templates"],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'store.context_processors.cart_item_count',
                'store.context_processors.unread_notifications_count',
            ],
        },
    },
]

WSGI_APPLICATION = 'texagonbackend.wsgi.application'

AUTH_USER_MODEL = 'accounts.User'
AUTHENTICATION_BACKENDS = [
    "accounts.backends.IdentifierBackend",
    "django.contrib.auth.backends.ModelBackend"
]

# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

# Postgres
if os.environ.get("LOCAL_DB", "0") == "0":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "appdb"),
            "USER": os.environ.get("POSTGRES_USER", "appuser"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "HOST": os.environ.get("POSTGRES_HOST", "db"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

#TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True

TIME_ZONE= "Africa/Lagos"

pass_mark = 45

LOW_SCORE = 30
FLW_SECRET_HASH = os.environ.get("FLW_SECRET_HASH", "")

# -----------------------------
# Static + Media (Django 5.2+)
# -----------------------------
STATIC_URL = "/static/"

# Where Django looks for static files during development
STATICFILES_DIRS = [
    BASE_DIR / "static",
]

# Where collectstatic will gather files for production
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# ── Environment-based storage switching ──
IS_LOCAL = os.environ.get("LOCAL", "1") == "1"
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "cloudinary").lower()

# Force local filesystem storage when running locally, regardless of STORAGE_BACKEND
if IS_LOCAL:
    IS_S3 = False
else:
    IS_S3 = STORAGE_BACKEND == "s3"

# Always define STORAGES (Django 5.2+)
# - "default" will be S3 when IS_S3 is True
# - Cloudinary storages are kept as named backends so you can pick them per-field in models
STORAGES = {
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },

    # Default fallback (overridden to S3 when STORAGE_BACKEND == "s3")
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },

    # Named Cloudinary backends (usable in models when IS_S3 is False, or anytime you want Cloudinary)
    "cloudinary": {
        "BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage",
    },
    "cloudinary_raw": {
        "BACKEND": "cloudinary_storage.storage.RawMediaCloudinaryStorage",
    },
}

# WhiteNoise static files in production
if os.environ.get("LOCAL") == "0":
    STORAGES["staticfiles"] = {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    }

# -----------------------------
# S3 Storage (when enabled)
# -----------------------------
if IS_S3:
    AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = os.getenv("AWS_STORAGE_BUCKET_NAME")
    AWS_S3_REGION_NAME = os.getenv("AWS_S3_REGION_NAME", "eu-north-1")

    # IMPORTANT: Force regional endpoint (prevents SignatureDoesNotMatch)
    AWS_S3_ENDPOINT_URL = os.getenv("AWS_S3_ENDPOINT_URL") or f"https://s3.{AWS_S3_REGION_NAME}.amazonaws.com"
    AWS_S3_CUSTOM_DOMAIN = os.getenv("AWS_S3_CUSTOM_DOMAIN") or None

    AWS_S3_FILE_OVERWRITE = False
    AWS_DEFAULT_ACL = None

    # Private bucket (Block Public Access ON) => signed URLs
    AWS_QUERYSTRING_AUTH = True
    AWS_S3_SIGNATURE_VERSION = "s3v4"
    AWS_S3_ADDRESSING_STYLE = "virtual"
    AWS_S3_OBJECT_PARAMETERS = {"CacheControl": "max-age=86400"}

    # When S3 is enabled, default storage becomes S3
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
    }

    # MEDIA_URL for display (URLs generated by .url will be signed when AWS_QUERYSTRING_AUTH=True)
    if AWS_S3_CUSTOM_DOMAIN:
        MEDIA_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/"
    else:
        MEDIA_URL = f"https://{AWS_STORAGE_BUCKET_NAME}.s3.{AWS_S3_REGION_NAME}.amazonaws.com/"

# -----------------------------
# Cloudinary settings (when enabled)
# -----------------------------
elif STORAGE_BACKEND == "cloudinary":
    CLOUDINARY_STORAGE = {
        "CLOUD_NAME": os.getenv("CLOUDINARY_CLOUD_NAME"),
        "API_KEY": os.getenv("CLOUDINARY_API_KEY"),
        "API_SECRET": os.getenv("CLOUDINARY_API_SECRET"),
    }

    # If Cloudinary is your main backend, make it the default storage
    STORAGES["default"] = {
        "BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage",
    }

    MEDIA_URL = "/media/"

# if os.environ.get("LOCAL") == "0":
#     STATIC_URL = "/static/"
#     STATIC_ROOT = BASE_DIR / "static"

#     # Cloudinary credentials
#     CLOUDINARY_STORAGE = {
#         "CLOUD_NAME": os.environ.get("CLOUDINARY_CLOUD_NAME"),
#         "API_KEY": os.environ.get("CLOUDINARY_API_KEY"),
#         "API_SECRET": os.environ.get("CLOUDINARY_API_SECRET"),
#     }





DOMAIN_EMAIL = os.environ.get("DOMAIN_EMAIL", brand_config["domain_email"])
SEND_EMAIL = os.environ.get("SEND_EMAIL", brand_config["domain_email"])
EMAIL_HOST = os.environ.get("EMAIL_HOST")
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD")
EMAIL_PORT = os.environ.get("EMAIL_PORT")
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", brand_config["default_from_email"])

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

EMAIL_CHANGE_VERIFY = os.environ.get("EMAIL_CHANGE_VERIFY", "1") == "1"         # set to False to apply email immediately (not recommended)
EMAIL_CHANGE_CODE_LIFETIME_MINUTES = os.environ.get("EMAIL_CHANGE_CODE_LIFETIME_MINUTES", 15)

DATA_UPLOAD_MAX_MEMORY_SIZE = 600 * 1024 * 1024      # 100MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 600 * 1024 * 1024      # 100MB


USE_CELERY = os.environ.get("USE_CELERY", "0") == "1"
REDIS_URL = os.environ.get("REDIS_URL", "")

if USE_CELERY:
    CELERY_BROKER_URL = REDIS_URL
    CELERY_RESULT_BACKEND = REDIS_URL
    CELERY_ACCEPT_CONTENT = ["json"]
    CELERY_TASK_SERIALIZER = "json"
    CELERY_RESULT_SERIALIZER = "json"
    CELERY_TIMEZONE = TIME_ZONE

    INSTALLED_APPS += ["django_celery_results"]


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {
            "format": "%(asctime)s [%(levelname)s] %(name)s %(message)s"
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
            "stream": "ext://sys.stdout",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",  # INFO so Render's basic logs show useful app messages
    },
    "loggers": {
        # Keep django's default logs going to console too
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        # Your app modules will inherit root logger, or configure here explicitly:
        # "yourapp.views": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}