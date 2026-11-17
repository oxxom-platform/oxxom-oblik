import os
from pathlib import Path
from .environment import load_local_env, configuration

BASE_DIR = Path(__file__).resolve().parent.parent
load_local_env(BASE_DIR.parent / ".env")
CONFIG = configuration(os.environ)
SECRET_KEY = CONFIG["secret"]
DEBUG = CONFIG["debug"]
ALLOWED_HOSTS = CONFIG["hosts"]
CSRF_TRUSTED_ORIGINS = CONFIG["origins"]
DATABASES = {"default": CONFIG["database"]}
INSTALLED_APPS = ["django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions", "accounts", "core", "catalog", "documents", "inventory", "posting", "operations", "payments", "reports", "printing", "catalog_imports"]
MIDDLEWARE = ["core.seo.SearchIndexPolicyMiddleware", "core.errors.ErrorPageMiddleware", "django.middleware.security.SecurityMiddleware", "django.contrib.sessions.middleware.SessionMiddleware",
              "core.locale_middleware.InterfaceLanguageMiddleware", "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware",
              "django.contrib.auth.middleware.AuthenticationMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware"]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
AUTH_USER_MODEL = "accounts.User"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True, "OPTIONS": {"context_processors": ["django.template.context_processors.csrf", "core.workspace.context", "core.i18n.context"]}}]
OXXOM_MFA_KEY = os.environ.get("OXXOM_MFA_KEY", "")
# Exact TCP peer only; HTTPS remains Gunicorn's separately validated policy.
from accounts.client_address import canonical_address
try:
    OXXOM_AUTH_PROXY_IPS = tuple(canonical_address(value) for value in
                               os.environ.get('OXXOM_PROXY_IP', '').split(',') if value.strip())
except ValueError:
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured('OXXOM_PROXY_IP requires exact IP addresses') from None
if not OXXOM_MFA_KEY and CONFIG["mode"] == "development":
    key_path = BASE_DIR.parent / ".secrets" / "mfa.key"
    if key_path.exists():
        OXXOM_MFA_KEY = key_path.read_text().strip()
if OXXOM_MFA_KEY:
    from cryptography.fernet import Fernet
    from django.core.exceptions import ImproperlyConfigured
    try:
        Fernet(OXXOM_MFA_KEY.encode())
    except (ValueError, TypeError):
        raise ImproperlyConfigured("Invalid OXXOM_MFA_KEY format") from None
elif CONFIG["mode"] == "production":
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured("OXXOM_MFA_KEY is required in production")
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "uk"
OXXOM_INDEX_PUBLIC = os.environ.get('OXXOM_INDEX_PUBLIC', 'true' if CONFIG['mode'] == 'production' else 'false') == 'true'
TIME_ZONE = "Europe/Kyiv"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 7 * 24 * 60 * 60
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_FAILURE_VIEW = 'core.errors.csrf_failure'
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
DATA_UPLOAD_MAX_MEMORY_SIZE = 131072
SESSION_COOKIE_SECURE = CONFIG["mode"] == "production"
CSRF_COOKIE_SECURE = CONFIG["mode"] == "production"
SECURE_SSL_REDIRECT = CONFIG["mode"] == "production"
# Enable forwarded HTTPS only behind a trusted proxy that replaces this header.
if os.environ.get("TRUST_PROXY", "false") == "true":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 3600 if CONFIG["mode"] == "production" else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
