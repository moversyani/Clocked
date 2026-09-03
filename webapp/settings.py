"""
Django settings for the Clocked web interface.

Deliberately minimal: this app has no models and no accounts, so there is no
database, no auth, and no admin. It exists to take a registration number,
hand it to the existing clocked.mot_client / normalise / detect pipeline, and
render what comes back.
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Local development only — this project is not set up for a public deployment.
SECRET_KEY = "django-insecure-clocked-local-dev-key"
DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "checker",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "webapp.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
            ],
        },
    },
]

WSGI_APPLICATION = "webapp.wsgi.application"

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
