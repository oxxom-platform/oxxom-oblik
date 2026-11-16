"""Strict configuration parsing; independent of Django for early validation."""
import os
from pathlib import Path


def load_local_env(path):
    # Production uses process environment, never implicit local files.
    if os.environ.get("APP_ENV", "development") != "development" or not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key.replace("_", "").isalnum():
            raise ValueError("Invalid environment file entry")
        os.environ.setdefault(key, value)


def required(env, name):
    value = env.get(name, "").strip()
    if not value or value.startswith("CHANGE_ME"):
        raise ValueError(f"Set {name} before starting OXXOM")
    return value


def configuration(env):
    mode = env.get("APP_ENV", "development")
    if mode not in {"development", "test", "production"}:
        raise ValueError("APP_ENV must be development, test or production")
    secret = required(env, "DJANGO_SECRET_KEY")
    if len(secret) < 50:
        raise ValueError("DJANGO_SECRET_KEY must contain at least 50 characters")
    debug = env.get("DJANGO_DEBUG", "false").lower()
    if debug not in {"true", "false"}:
        raise ValueError("DJANGO_DEBUG must be true or false")
    hosts = [x.strip() for x in env.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if x.strip()]
    if not hosts or "*" in hosts:
        raise ValueError("Explicit DJANGO_ALLOWED_HOSTS required")
    origins = [x.strip() for x in env.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if x.strip()]
    if mode == "production":
        if debug == "true":
            raise ValueError("DEBUG forbidden in production")
        if "DJANGO_ALLOWED_HOSTS" not in env or not origins or any(not x.startswith("https://") for x in origins):
            raise ValueError("Production requires explicit hosts and HTTPS CSRF origins")
    port = int(env.get("POSTGRES_PORT", "55432"))
    if not 1 <= port <= 65535:
        raise ValueError("Invalid POSTGRES_PORT")
    options = {"connect_timeout": 5}
    # Local macOS connection was verified with GSS negotiation disabled.
    # Do not change SSL or the production connection policy.
    if mode in {"development", "test"} and env.get("POSTGRES_HOST", "127.0.0.1") in {"127.0.0.1", "localhost", "::1"}:
        options["gssencmode"] = "disable"
    return dict(mode=mode, secret=secret, debug=debug == "true", hosts=hosts, origins=origins,
        database={"ENGINE": "django.db.backends.postgresql", "NAME": required(env,"POSTGRES_DB"),
                  "USER": required(env,"POSTGRES_USER"), "PASSWORD": required(env,"POSTGRES_PASSWORD"),
                  "HOST": env.get("POSTGRES_HOST", "127.0.0.1"), "PORT": port,
                  "CONN_MAX_AGE": 0, "OPTIONS": options})
