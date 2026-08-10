import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class TelegramCredentialError(ValueError):
    pass


def _fernet() -> Fernet:
    settings = get_settings()
    configured = settings.telegram_credentials_encryption_key.strip()
    if not configured and settings.app_env == "production":
        raise TelegramCredentialError(
            "TELEGRAM_CREDENTIALS_ENCRYPTION_KEY must be configured independently in production"
        )
    key = (
        configured.encode("ascii")
        if configured
        else base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode("utf-8")).digest())
    )
    try:
        return Fernet(key)
    except (ValueError, TypeError) as exc:
        raise TelegramCredentialError(
            "TELEGRAM_CREDENTIALS_ENCRYPTION_KEY must be a valid Fernet key"
        ) from exc


def encrypt_telegram_secret(value: str) -> str:
    if not value:
        raise TelegramCredentialError("Telegram secret must not be empty")
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_telegram_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        raise TelegramCredentialError("Stored Telegram credential cannot be decrypted") from exc
