"""Prototype operator accounts, sessions and role-based access control.

Enforced by the backend, not the UI:

- Passwords: PBKDF2-HMAC-SHA256 (stdlib hashlib), random 16-byte salt,
  PBKDF2_ITERATIONS rounds, stored as 'pbkdf2_sha256$<iter>$<salt>$<hash>'
  (base64). Verified with hmac.compare_digest. Passwords and hashes are never
  logged or returned by any API. There are NO default credentials.
- Sessions: secrets.token_urlsafe(32) in an HttpOnly SameSite=Lax cookie;
  only the token's SHA-256 is stored (sessions table). Lifetime
  config.SESSION_HOURS. The user row is loaded fresh on every request, so a
  deactivation or role change applies immediately.
- CSRF: every non-GET/HEAD/OPTIONS /api request must carry
  `X-Antariksha-Client: console` (a custom header forces a CORS preflight,
  which only config.CORS_ORIGINS pass).
- Permissions: ROLE_PERMISSIONS below is the single source of truth (also
  exported via GET /api/auth/me). Routes declare
  `dependencies=[Depends(require_permission("..."))]`.

The optional ANTARIKSHA_API_KEY gate (backend/main.py) is unchanged and is
evaluated before the session check.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend import config
from backend import db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Roles and permissions (single source of truth)
# ---------------------------------------------------------------------------
ROLES = ("VIEWER", "OPERATOR", "ASSET_MANAGER", "ADMINISTRATOR")
_VIEWER = ("view", "translate", "view_audit")
_OPERATOR = _VIEWER + ("refresh", "demo", "decide", "review_objects")
_ASSET_MANAGER = _OPERATOR + ("manage_assets",)
# configure_system: system settings such as the screening horizon (ADMINISTRATOR only).
_ADMINISTRATOR = _ASSET_MANAGER + ("manage_users", "view_admin_audit", "configure_system")
ROLE_PERMISSIONS = {
    "VIEWER": _VIEWER,
    "OPERATOR": _OPERATOR,
    "ASSET_MANAGER": _ASSET_MANAGER,
    "ADMINISTRATOR": _ADMINISTRATOR,
}
ALL_PERMISSIONS = _ADMINISTRATOR


def permissions_for(role):
    return list(ROLE_PERMISSIONS.get(role, ()))


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
PBKDF2_ITERATIONS = 310_000
PBKDF2_MIN_ITERATIONS = 200_000
_HASH_SCHEME = "pbkdf2_sha256"


def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    if iterations < PBKDF2_MIN_ITERATIONS:
        raise ValueError("PBKDF2 iteration count below policy minimum")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join((_HASH_SCHEME, str(iterations), base64.b64encode(salt).decode("ascii"),
                     base64.b64encode(digest).decode("ascii")))


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iter_s, salt_b64, hash_b64 = stored.split("$")
        iterations = int(iter_s)
        if scheme != _HASH_SCHEME or iterations < PBKDF2_MIN_ITERATIONS:
            return False
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(hash_b64, validate=True)
    except (AttributeError, ValueError, TypeError):
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)


_DUMMY_HASH = None


def _dummy_hash():
    """A real hash of a random secret, used to spend the same verification
    time for unknown usernames (no user enumeration by timing)."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_urlsafe(24))
    return _DUMMY_HASH


# ---------------------------------------------------------------------------
# Input validation (shared by the admin API and the CLI)
# ---------------------------------------------------------------------------
_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$")


class ValidationFailed(ValueError):
    pass


def validate_username(username) -> str:
    username = (username or "").strip()
    if not _USERNAME_RE.match(username):
        raise ValidationFailed("username must be 3-32 characters: letters, digits, '.', '_' or '-' "
                               "(starting with a letter or digit).")
    return username


def validate_password(password) -> str:
    if not isinstance(password, str) or len(password) < config.PASSWORD_MIN_LENGTH:
        raise ValidationFailed(f"password must be at least {config.PASSWORD_MIN_LENGTH} characters.")
    if len(password) > 1024:
        raise ValidationFailed("password is too long (max 1024 characters).")
    return password


def validate_role(role) -> str:
    if role not in ROLES:
        raise ValidationFailed(f"role must be one of {list(ROLES)}.")
    return role


def clean_text(value, max_len, field_name, allow_empty=True):
    """Single-line printable text: rejects control characters, trims ends.
    Returns None for empty input when allow_empty."""
    if value is None:
        if allow_empty:
            return None
        raise ValidationFailed(f"{field_name} is required.")
    if not isinstance(value, str):
        raise ValidationFailed(f"{field_name} must be a string.")
    if any(unicodedata.category(ch).startswith("C") for ch in value):
        raise ValidationFailed(f"{field_name} must not contain control characters.")
    value = value.strip()
    if not value:
        if allow_empty:
            return None
        raise ValidationFailed(f"{field_name} must not be empty.")
    if len(value) > max_len:
        raise ValidationFailed(f"{field_name} must be at most {max_len} characters.")
    return value


def create_user_account(username, role, password, display_name=None, actor=None, created_by=None):
    """Validate, hash and store a new user (audited). Raises ValidationFailed
    or db.DuplicateError. Returns the public user dict (no hash)."""
    username = validate_username(username)
    role = validate_role(role)
    validate_password(password)
    display_name = clean_text(display_name, 64, "display_name")
    return db.create_user(username, display_name, role, hash_password(password),
                          created_by or (actor or {}).get("username"), actor)


# ---------------------------------------------------------------------------
# Principal + errors
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Principal:
    id: int | None
    username: str
    display_name: str | None
    role: str
    session_expires_at: str | None = None
    session_token_hash: str | None = field(default=None, repr=False)

    @property
    def permissions(self):
        return permissions_for(self.role)

    def can(self, permission):
        return permission in ROLE_PERMISSIONS.get(self.role, ())

    def as_actor(self):
        """Dict for audit columns (actor_user_id / actor_username / actor_role)."""
        return {"user_id": self.id, "username": self.username, "role": self.role}

    def public(self):
        return {"id": self.id, "username": self.username, "display_name": self.display_name, "role": self.role}


def as_principal(value):
    """`value` if it is a Principal, else None (direct handler calls in tests
    pass FastAPI's Depends default object)."""
    return value if isinstance(value, Principal) else None


def actor_of(value):
    p = as_principal(value)
    return p.as_actor() if p else None


class AuthError(Exception):
    """Rendered by main.py's exception handler as JSON `body` with `status`."""

    def __init__(self, status, body, headers=None):
        super().__init__(body.get("detail"))
        self.status = status
        self.body = body
        self.headers = headers or {}


def auth_error_response(exc: AuthError):
    return JSONResponse(status_code=exc.status, content=exc.body, headers=exc.headers)


def _not_authenticated():
    return AuthError(401, {"detail": "Authentication required.", "error": "not_authenticated"})


# Routes reachable without a session: (method, path).
# /api/internal/scheduled-refresh is authenticated by its own shared secret
# (Authorization: Bearer, backend/main.py) and can only trigger the scheduled
# refresh; it never authenticates as a user.
SCHEDULER_ENDPOINT = "/api/internal/scheduled-refresh"
PUBLIC_ROUTES = {("GET", "/api/health"), ("HEAD", "/api/health"), ("POST", "/api/auth/login"),
                 ("POST", "/api/auth/logout"), ("GET", SCHEDULER_ENDPOINT), ("POST", SCHEDULER_ENDPOINT)}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_TOUCH_INTERVAL = timedelta(seconds=60)


def _utcnow():
    return datetime.now(timezone.utc)


def _parse_ts(value):
    try:
        ts = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def principal_from_token(token, now=None):
    """Principal for a live session token, else None (unknown, expired,
    revoked or user inactive). Loads the user fresh from the DB."""
    if not token or len(token) > 256:
        return None
    th = token_hash(token)
    row = db.get_session_with_user(th)
    if row is None or row["revoked_at"] is not None or not row["active"]:
        return None
    now = now or _utcnow()
    expires = _parse_ts(row["expires_at"])
    if expires is None or expires <= now:
        return None
    last_seen = _parse_ts(row["last_seen_at"])
    if last_seen is None or now - last_seen >= _TOUCH_INTERVAL:
        db.touch_session(th)
    return Principal(id=row["user_id"], username=row["username"], display_name=row["display_name"],
                     role=row["role"], session_expires_at=row["expires_at"], session_token_hash=th)


def get_principal(request: Request):
    """App-wide dependency: authenticate every /api request (except
    PUBLIC_ROUTES) from the session cookie, then require the CSRF header on
    mutating methods. Returns the Principal (None on public/non-API routes).
    Overridden in tests via app.dependency_overrides."""
    path = request.scope.get("path", "")
    method = request.method.upper()
    if not path.startswith("/api/") or (method, path) in PUBLIC_ROUTES:
        return None
    principal = principal_from_token(request.cookies.get(config.SESSION_COOKIE_NAME))
    if principal is None:
        raise _not_authenticated()
    if method not in _SAFE_METHODS:
        if request.headers.get(config.CSRF_HEADER, "") != config.CSRF_HEADER_VALUE:
            raise AuthError(403, {"detail": f"Missing {config.CSRF_HEADER} header on a state-changing request.",
                                  "error": "csrf"})
    return principal


def require_permission(permission: str):
    """Route dependency: 403 unless the authenticated principal's role grants
    `permission`. Tagged so a test can verify every mutating route has one."""
    if permission not in ALL_PERMISSIONS:
        raise ValueError(f"Unknown permission {permission!r}")

    def _check(principal=Depends(get_principal)):
        if principal is None:
            raise _not_authenticated()
        if not principal.can(permission):
            raise AuthError(403, {"detail": f"Your role ({principal.role}) does not permit this action.",
                                  "error": "forbidden", "required_permission": permission})
        return principal

    _check.antariksha_permission = permission
    _check.__name__ = f"require_permission_{permission}"
    return _check


# ---------------------------------------------------------------------------
# /api/auth/*
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str = ""
    password: str = ""


def _check_origin(request: Request):
    origin = request.headers.get("origin")
    if origin is not None and origin not in config.CORS_ORIGINS:
        raise AuthError(403, {"detail": "Request origin is not allowed.", "error": "origin_not_allowed"})


def _session_payload(user, expires_at):
    return {
        "user": {"id": user["id"], "username": user["username"], "display_name": user.get("display_name"),
                 "role": user["role"]},
        "permissions": permissions_for(user["role"]),
        "expires_at": expires_at,
    }


def _audit_username(value):
    cleaned = "".join(ch for ch in (value or "") if not unicodedata.category(ch).startswith("C")).strip()
    return cleaned[:64] or None


def _cookie_kwargs():
    return {"path": "/", "httponly": True, "samesite": "lax", "secure": bool(config.COOKIE_SECURE)}


@router.post("/login")
def login(body: LoginBody, request: Request):
    _check_origin(request)
    username = (body.username or "").strip()
    record = db.get_user_auth_record(username) if username and len(username) <= 64 else None
    if record is None:
        verify_password(body.password or "", _dummy_hash())
        ok, reason = False, "unknown_user"
    else:
        ok = verify_password(body.password or "", record["password_hash"])
        reason = None if ok else "bad_password"
        if ok and not record["active"]:
            ok, reason = False, "inactive_user"
    if not ok:
        db.insert_governance_audit(
            {"user_id": record["id"] if record else None, "username": _audit_username(username),
             "role": record["role"] if record else None},
            "login_failure", "user", record["id"] if record else None, {"reason": reason})
        return JSONResponse(status_code=401, content={"detail": "Invalid username or password.",
                                                      "error": "invalid_credentials"})
    token = secrets.token_urlsafe(32)
    lifetime = timedelta(hours=float(config.SESSION_HOURS))
    expires_at = (_utcnow() + lifetime).isoformat()
    db.create_session(token_hash(token), record["id"], expires_at)
    db.touch_last_login(record["id"])
    db.insert_governance_audit({"user_id": record["id"], "username": record["username"], "role": record["role"]},
                               "login_success", "user", record["id"], None)
    response = JSONResponse(content=_session_payload(record, expires_at))
    response.set_cookie(config.SESSION_COOKIE_NAME, token, max_age=int(lifetime.total_seconds()),
                        **_cookie_kwargs())
    return response


@router.post("/logout")
def logout(request: Request):
    _check_origin(request)
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    if token and len(token) <= 256:
        th = token_hash(token)
        row = db.get_session_with_user(th)
        if db.revoke_session(th) is not None and row is not None:
            db.insert_governance_audit({"user_id": row["user_id"], "username": row["username"],
                                        "role": row["role"]}, "logout", "user", row["user_id"], None)
    response = JSONResponse(content={"ok": True})
    response.delete_cookie(config.SESSION_COOKIE_NAME, **_cookie_kwargs())
    return response


@router.get("/me")
def me(principal=Depends(get_principal)):
    principal = as_principal(principal)
    if principal is None:
        raise _not_authenticated()
    return {"user": principal.public(), "permissions": principal.permissions,
            "expires_at": principal.session_expires_at}


# ---------------------------------------------------------------------------
# /api/admin/* (ADMINISTRATOR)
# ---------------------------------------------------------------------------
admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


def _error(status, detail, error, **extra):
    return JSONResponse(status_code=status, content={"detail": detail, "error": error, **extra})


class CreateUserBody(BaseModel):
    username: str
    role: str
    password: str
    display_name: str | None = None


class PatchUserBody(BaseModel):
    role: str | None = None
    active: bool | None = None
    display_name: str | None = None
    password: str | None = None


@admin_router.get("/users", dependencies=[Depends(require_permission("manage_users"))])
def admin_list_users():
    return {"users": db.list_users(), "roles": list(ROLES), "role_permissions":
            {r: list(p) for r, p in ROLE_PERMISSIONS.items()}}


@admin_router.post("/users", status_code=201)
def admin_create_user(body: CreateUserBody, principal=Depends(require_permission("manage_users"))):
    try:
        return create_user_account(body.username, body.role, body.password, body.display_name,
                                   actor=actor_of(principal))
    except ValidationFailed as exc:
        return _error(422, str(exc), "invalid_input")
    except db.DuplicateError as exc:
        return _error(409, str(exc), "duplicate")


@admin_router.patch("/users/{user_id}")
def admin_update_user(user_id: int, body: PatchUserBody, principal=Depends(require_permission("manage_users"))):
    fields = body.model_fields_set
    try:
        role = validate_role(body.role) if body.role is not None else None
        if "active" in fields and body.active is None:
            raise ValidationFailed("active must be true or false.")
        password_hash = hash_password(validate_password(body.password)) if body.password is not None else None
        display_name = clean_text(body.display_name, 64, "display_name") if "display_name" in fields else None
    except ValidationFailed as exc:
        return _error(422, str(exc), "invalid_input")
    if not fields:
        return _error(422, "No changes supplied.", "invalid_input")
    try:
        return db.update_user(user_id, actor_of(principal), role=role, active=body.active,
                              display_name=display_name, set_display_name="display_name" in fields,
                              password_hash=password_hash)
    except db.NotFoundError:
        return _error(404, "User not found.", "not_found")
    except db.LastAdministratorError as exc:
        return _error(409, str(exc), "last_admin")


@admin_router.get("/audit", dependencies=[Depends(require_permission("view_admin_audit"))])
def admin_audit(limit: int = 200):
    return {"entries": db.list_governance_audit(limit=limit)}


# ---------------------------------------------------------------------------
# Startup bootstrap
# ---------------------------------------------------------------------------
def bootstrap_users():
    """No users: log how to create the first ADMINISTRATOR, or create it
    once from ANTARIKSHA_BOOTSTRAP_ADMIN_USERNAME/_PASSWORD if both are set.
    Never logs a password."""
    if db.count_users():
        return None
    username, password = config.BOOTSTRAP_ADMIN_USERNAME, config.BOOTSTRAP_ADMIN_PASSWORD
    if username and password:
        try:
            user = create_user_account(username, "ADMINISTRATOR", password, actor={
                "user_id": None, "username": "bootstrap", "role": None}, created_by="bootstrap")
        except (ValidationFailed, db.DuplicateError) as exc:
            logger.error("Bootstrap administrator NOT created: %s", exc)
            return None
        logger.warning("Bootstrap ADMINISTRATOR '%s' created from ANTARIKSHA_BOOTSTRAP_ADMIN_* environment "
                       "variables. Remove them from the environment now.", user["username"])
        return user
    logger.warning("No operator accounts exist: every /api endpoint except /api/health and /api/auth/login "
                   "will return 401. Create the first administrator with:  "
                   "python -m backend.users create --username <name> --role ADMINISTRATOR")
    return None
