STATIC_URL = "/static/"

import os
from pathlib import Path
import sys

if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys._MEIPASS)
    RUNTIME_DIR = Path(os.path.dirname(sys.executable))

    # 🔥 handle both layouts
    if (RUNTIME_DIR / "static").exists():
        STATIC_DIR = RUNTIME_DIR / "static"
    else:
        STATIC_DIR = RUNTIME_DIR / "_internal" / "static"
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
    RUNTIME_DIR = BASE_DIR
    STATIC_DIR = BASE_DIR / "static"

# ❌ REMOVE ALL OF THIS (was breaking things)
# STATIC_ROOT
# STATICFILES_DIRS
# STATICFILES_STORAGE

# Frontend (template still comes from backend/templates)
FRONTEND_DIR = BASE_DIR / "dist"

DEBUG = True   # keep True so django.contrib.staticfiles serves /static/ — set False only with WhiteNoise
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    "api",
    "rest_framework.authtoken",
    "rest_framework",
    "corsheaders",
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',  # keep this first
    'django.middleware.security.SecurityMiddleware',

    # ✅ YOUR DEBUG MIDDLEWARE (OPTIONAL — remove if not using)
    'api.debug_middleware.DebugMiddleware',

    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'chess_project.urls'

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

WSGI_APPLICATION = 'chess_project.wsgi.application'

# All user data lives in LOCALAPPDATA/chess-ai/ so it works whether the app
# is installed in Program Files (read-only) or run from anywhere else.
_APP_DATA = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'chess-ai'
_APP_DATA.mkdir(parents=True, exist_ok=True)

# Generate a persistent secret key on first run and store it in app data.
# A stable key means sessions and CSRF tokens survive server restarts.
import secrets as _secrets
_secret_path = _APP_DATA / 'django_secret.txt'
if _secret_path.exists():
    try:
        SECRET_KEY = _secret_path.read_text().strip()
    except Exception:
        SECRET_KEY = 'django-insecure-fallback'
else:
    SECRET_KEY = 'django-' + _secrets.token_urlsafe(50)
    try:
        _secret_path.write_text(SECRET_KEY)
    except Exception:
        pass

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': str(_APP_DATA / 'db.sqlite3'),
    }
}

# ✅ CORS
CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True

# ✅ CSRF
CSRF_TRUSTED_ORIGINS = [
    "http://127.0.0.1:1430",
    "http://localhost:1430",
    "http://127.0.0.1:1420",
    "http://localhost:1420",
]

CSRF_COOKIE_HTTPONLY = False
CSRF_USE_SESSIONS = False
CSRF_COOKIE_SAMESITE = "Lax"

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
}