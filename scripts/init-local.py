"""Create private development config once; never replace existing credentials."""
import os
from pathlib import Path
import secrets
import base64
root = Path(__file__).resolve().parent.parent
path = root / ".env"
if path.exists():
    print("Existing .env preserved")
else:
    values = {
        "APP_ENV": "development", "DJANGO_DEBUG": "false",
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
        "DJANGO_ALLOWED_HOSTS": "127.0.0.1,localhost,testserver",
        "POSTGRES_DB": "oblik_dev", "POSTGRES_USER": "oblik_dev",
        "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
        "POSTGRES_HOST": "127.0.0.1", "POSTGRES_PORT": "55432",
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
    print("Created private .env; secrets not printed")

# Separate from database and .env; never replace an existing encryption key.
key_dir = root / ".secrets"
key_dir.mkdir(mode=0o700, exist_ok=True)
key_path = key_dir / "mfa.key"
try:
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print("Existing MFA encryption key preserved")
else:
    with os.fdopen(fd, "w") as file:
        file.write(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    print("Created private MFA encryption key; value not printed")
