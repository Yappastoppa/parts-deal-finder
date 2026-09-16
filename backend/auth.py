"""Admin session helpers. Cross-origin (Pages <-> Railway), so sessions are a bearer token, not a cookie."""
import os
import secrets


def admin_password_configured():
    return bool(os.environ.get('APF_ADMIN_PASSWORD'))


def check_admin_password(candidate):
    expected = os.environ.get('APF_ADMIN_PASSWORD', '')
    return bool(expected) and isinstance(candidate, str) and secrets.compare_digest(candidate, expected)


def read_bearer_token(environ):
    header = environ.get('HTTP_AUTHORIZATION', '')
    if not header.startswith('Bearer '):
        return None
    token = header[len('Bearer '):].strip()
    return token or None
