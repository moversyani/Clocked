"""
Credential loading.

Reads the five DVSA values from a .env file at the project root, falling back
to real environment variables so the same code works on a laptop and on a
deployed server. Nothing here ever prints a secret.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

REQUIRED = (
    "DVSA_CLIENT_ID",
    "DVSA_CLIENT_SECRET",
    "DVSA_API_KEY",
    "DVSA_TOKEN_URL",
    "DVSA_SCOPE_URL",
)

load_dotenv(ENV_PATH)


class MissingCredentials(RuntimeError):
    """Raised when one or more required values are absent."""


def get(name: str) -> str:
    value = os.environ.get(name, "").strip()

    if not value:
        raise MissingCredentials(
            f"{name} is not set. Copy .env.example to .env and fill it in."
        )

    return value


def missing() -> list[str]:
    """Names of any required values that are absent or blank."""
    return [name for name in REQUIRED if not os.environ.get(name, "").strip()]


def mask(value: str, keep: int = 4) -> str:
    """Show just enough of a secret to confirm it is the right one."""
    if len(value) <= keep:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * (len(value) - keep)}"
