import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "CHANGE_ME")
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "https://texagon.epichouse.online")
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",")
PAYMENT_TEST = os.environ.get("PAYMENT_TEST", "1") == "1"
LOGO_URL = os.environ.get("LOGO_URL", "https://texagon.epichouse.online/logo.png")

TEST_KEY_SECRET = os.environ.get("TEST_KEY_SECRET", "CHANGE_ME")
FLW_SECRET_KEY = os.environ.get("FLW_SECRET_KEY", "CHANGE_ME")

if os.environ.get('LOCAL') == "0":
    CSRF_TRUSTED_ORIGINS = [
        "https://texagonbackend.esm.name.ng",
        "https://texagonbackend.epichouse.online",
        "https://texagonbackend.onrender.com",
        "http://127.0.0.1:9098",
        "http://localhost"
    ]

    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True



# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
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
    "rest_framework",
    "rest_framework_api_key",
]

MIDDLEWARE = [
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
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'texagonbackend.wsgi.application'

AUTH_USER_MODEL = 'accounts.User'
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]

# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

# Postgres
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

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True

TIME_ZONE= "Africa/Lagos"

pass_mark = 45

LOW_SCORE = 30

STATIC_URL = 'static/'
MEDIA_URL = 'media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
STATIC_ROOT = os.path.join(BASE_DIR, 'static')

if os.environ.get("LOCAL") == "0":
    #MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
    #STATIC_ROOT = os.path.join(BASE_DIR, 'static')
    STATIC_URL = '/static/'        # <- needs the leading slash
    #STATIC_ROOT = '/app/staticfiles'  # <- exactly where you mounted the volume
    STATIC_ROOT = BASE_DIR / "static" 


    MEDIA_URL = '/media/'
    #MEDIA_ROOT = '/app/media'  
    MEDIA_ROOT = BASE_DIR / "media"
# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field


DOMAIN_EMAIL = os.environ.get("DOMAIN_EMAIL")
SEND_EMAIL = os.environ.get("SEND_EMAIL")
EMAIL_HOST = os.environ.get("EMAIL_HOST")
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD")
#EMAIL_HOST = 'ikpeazuchambers.com'
#EMAIL_HOST_USER = 'quebe@ikpeazuchambers.com'
#EMAIL_HOST_PASSWORD = ''
EMAIL_PORT = os.environ.get("EMAIL_PORT")
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL")

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
if os.environ.get("LOCAL") == "0":
    STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"
