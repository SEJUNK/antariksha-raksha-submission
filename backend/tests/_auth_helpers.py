"""Helpers for tests that exercise the real login/session/CSRF/RBAC path."""

from backend.tests._asgi import request

PASSWORD = "correct-horse-battery"
CSRF = {"X-Antariksha-Client": "console"}


def make_user(username, role, password=PASSWORD, display_name=None):
    from backend.auth import create_user_account
    return create_user_account(username, role, password, display_name,
                               actor={"user_id": None, "username": "test-setup", "role": None})


def cookie_from(headers):
    raw = headers.get("set-cookie", "")
    first = raw.split(";", 1)[0]
    name, _, value = first.partition("=")
    return name.strip(), value.strip().strip('"')


def login(username, password=PASSWORD, headers=None):
    from backend.main import app
    status, hdrs, body = request(app, "POST", "/api/auth/login", headers=headers,
                                 json_body={"username": username, "password": password})
    token = cookie_from(hdrs)[1] if status == 200 else None
    return status, hdrs, body, token


def call(method, path, token=None, csrf=True, json_body=None, headers=None):
    """Request with the session cookie (if token) and the CSRF header on
    mutating methods (if csrf)."""
    from backend.main import app
    hdrs = dict(headers or {})
    if token:
        hdrs["Cookie"] = f"antariksha_session={token}"
    if csrf and method.upper() not in ("GET", "HEAD", "OPTIONS"):
        hdrs.update(CSRF)
    return request(app, method, path, headers=hdrs, json_body=json_body)
