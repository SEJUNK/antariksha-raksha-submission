"""Admin-configurable system settings (app_settings table).

Currently one setting: the screening horizon -- how far ahead propagated
trajectories are screened for candidate close approaches. Allowed values are
config.SCREENING_HORIZON_ALLOWED_HOURS; while no row exists the default
(config.PROPAGATION_WINDOW_HOURS = 72 h) applies, so existing installs keep
72 h until an ADMINISTRATOR changes it. The propagation step is fixed
(config.PROPAGATION_STEP_SECONDS) and not configurable.

A change applies to the NEXT screening run (live or demo); each run records
the horizon it actually used in screening_runs.window_hours, so historical
runs/events keep their own values. Every change is written to
governance_audit (action 'config_change', target 'setting').
"""

from backend import db
from backend.config import (
    PROPAGATION_STEP_SECONDS,
    PROPAGATION_WINDOW_HOURS,
    SCREENING_HORIZON_ALLOWED_HOURS,
)

SCREENING_HORIZON_KEY = "screening_horizon_hours"
SCREENING_HORIZON_DEFAULT = PROPAGATION_WINDOW_HOURS
SCREENING_HORIZON_DESCRIPTION = ("The prototype screens propagated trajectories for candidate close "
                                 "approaches over this future window.")


class InvalidSettingValue(ValueError):
    pass


def validate_screening_horizon(value):
    """Return the value as int if it is exactly one of the allowed integers;
    booleans, floats (even 72.0), strings and anything else are rejected."""
    if type(value) is not int or value not in SCREENING_HORIZON_ALLOWED_HOURS:
        raise InvalidSettingValue(
            f"hours must be one of {list(SCREENING_HORIZON_ALLOWED_HOURS)} (integer).")
    return value


def _stored_horizon(row):
    if row is None:
        return None
    try:
        value = int(row["value"])
    except (TypeError, ValueError):
        return None
    return value if value in SCREENING_HORIZON_ALLOWED_HOURS else None


def get_screening_horizon_hours():
    """The active screening horizon in hours (default when unset/invalid)."""
    value = _stored_horizon(db.get_setting_row(SCREENING_HORIZON_KEY))
    return SCREENING_HORIZON_DEFAULT if value is None else value


def screening_horizon_payload():
    row = db.get_setting_row(SCREENING_HORIZON_KEY)
    value = _stored_horizon(row)
    return {
        "hours": SCREENING_HORIZON_DEFAULT if value is None else value,
        "allowed": list(SCREENING_HORIZON_ALLOWED_HOURS),
        "default": SCREENING_HORIZON_DEFAULT,
        "step_seconds": PROPAGATION_STEP_SECONDS,
        "updated_at": row["updated_at"] if row and value is not None else None,
        "updated_by": row["updated_by"] if row and value is not None else None,
        "description": SCREENING_HORIZON_DESCRIPTION,
    }


def set_screening_horizon_hours(value, actor):
    """Validate and store a new horizon. Returns (payload, changed). A no-op
    change (same value as active) writes nothing and no audit row."""
    hours = validate_screening_horizon(value)
    old = get_screening_horizon_hours()
    if hours == old:
        return screening_horizon_payload(), False
    db.set_setting_audited(SCREENING_HORIZON_KEY, hours, actor,
                           {"name": SCREENING_HORIZON_KEY, "old": old, "new": hours})
    return screening_horizon_payload(), True
