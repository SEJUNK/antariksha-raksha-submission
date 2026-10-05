"""Read-only catalog views for the operator: the protected-asset registry
and the new-object review list.

Two different things are kept apart on purpose:

- OBJECT CATALOG: every object ingested from public CelesTrak data (the
  working set), whatever its type.
- PROTECTED ASSET REGISTRY: the operator-managed Group A entries in the DB
  protected_assets table (seeded once from data/working_set.json group_a,
  then changed only by an ASSET_MANAGER/ADMINISTRATOR via the audited
  /api/protected-assets endpoints). Protected status comes only from that
  configuration (status 'active') -- never inferred from orbital data,
  ownership or nationality, and a newly ingested object never becomes
  protected automatically.

"New object" means new to this LOCAL catalog (first_seen later than the
catalog baseline). It does not mean newly launched, and an object missing
from a later ingest is not treated as retired.
"""

import json
from collections import Counter

from backend.config import WORKING_SET_PATH
from backend.db import (
    ensure_protected_assets_seeded,
    first_seen_baseline,
    latest_ingest_run,
    list_new_objects,
    list_objects,
    list_protected_assets,
)
from backend.propagate import MAX_TLE_AGE_DAYS, tle_age_days_for

REGISTRY_SOURCE = ("Operator configuration: protected-asset registry (database; seeded once from "
                   "data/working_set.json Group A)")
CATALOG_SOURCE = "CelesTrak public GP data (working set)"
GROUP_LABELS = {"satellite": "A", "foreign_sat": "C", "debris": "B"}


def _load_group_a():
    """Every registry entry (all statuses), seeding the registry once from
    the working set if that has not happened yet."""
    ensure_protected_assets_seeded(WORKING_SET_PATH)
    return list_protected_assets()


def _age(obj):
    try:
        return round(tle_age_days_for(obj), 1)
    except Exception:  # noqa: BLE001 -- unreadable elements: no age, no crash
        return None


def _resolution_and_unresolved():
    run = latest_ingest_run() or {}
    try:
        resolution = json.loads(run.get("resolution_json") or "{}") or {}
    except (TypeError, ValueError):
        resolution = {}
    try:
        unresolved = json.loads(run.get("unresolved_json") or "[]") or []
    except (TypeError, ValueError):
        unresolved = []
    return resolution, {f"{u.get('group')}:{u.get('name_query')}": u for u in unresolved if isinstance(u, dict)}


def protected_asset_registry():
    objects = {o["norad_id"]: o for o in list_objects(include_inactive=True)}
    active = [o for o in objects.values() if o.get("active", 1)]
    resolution, unresolved = _resolution_and_unresolved()
    assets = []
    for entry in _load_group_a():
        key = f"A:{entry['name_query']}"
        norad_id = resolution.get(key) or (unresolved.get(key) or {}).get("kept_norad_id")
        obj = objects.get(norad_id) if norad_id else None
        age = _age(obj) if obj else None
        if obj is None:
            data_status = "unresolved"
        elif not obj.get("active", 1):
            data_status = "inactive"
        elif age is not None and age > MAX_TLE_AGE_DAYS:
            data_status = "stale"
        else:
            data_status = "current"
        is_active = entry.get("status", "active") == "active"
        assets.append({
            "asset_id": entry.get("id"),
            "status": entry.get("status", "active"),
            "name_query": entry["name_query"],
            "exact_match": entry.get("exact_match"),
            "updated_at": entry.get("updated_at"),
            "updated_by": entry.get("updated_by"),
            "configured_name": entry["name_query"],
            "name": obj["name"] if obj else None,
            "norad_id": norad_id,
            "criticality": entry.get("criticality"),
            "owner": obj.get("owner_country") if obj else None,
            "note": entry.get("note"),
            # Protected only while the registry entry is active.
            "protected": is_active,
            "protected_basis": "operator_configuration" if is_active else None,
            "data_status": data_status,
            "unresolved_reason": (unresolved.get(key) or {}).get("reason"),
            "source_format": obj.get("source_format") if obj else None,
            "tle_age_days": age,
            "first_seen": obj.get("first_seen") if obj else None,
        })
    return {
        "registry_source": REGISTRY_SOURCE,
        "assets": assets,
        "catalog": {
            "source": CATALOG_SOURCE,
            "active_objects": len(active),
            "inactive_objects": len(objects) - len(active),
            "by_type": dict(Counter(o["object_type"] for o in active)),
            "by_source_format": dict(Counter(o.get("source_format") or "tle" for o in active)),
        },
        "max_tle_age_days": MAX_TLE_AGE_DAYS,
    }


def new_object_review():
    rows = list_new_objects()
    out = []
    for o in rows:
        review = o.get("review")
        out.append({
            "norad_id": o["norad_id"],
            "name": o["name"],
            "object_type": o["object_type"],
            "group": GROUP_LABELS.get(o["object_type"]),
            # Only Group A configuration entries are ingested as type
            # 'satellite', so protected status still comes from configuration.
            "protected": o["object_type"] == "satellite",
            "first_seen": o.get("first_seen"),
            "source": CATALOG_SOURCE,
            "source_format": o.get("source_format") or "tle",
            "orbital_data": "available" if (o.get("omm_json") or (o.get("tle_line1") and o.get("tle_line2"))) else "missing",
            "active": bool(o.get("active", 1)),
            "tle_age_days": _age(o),
            "review_status": "reviewed" if review else "not_reviewed",
            "review": {"reviewed_at": review["reviewed_at"], "note": review.get("note")} if review else None,
        })
    return {
        "baseline": first_seen_baseline(),
        "definition": "new_to_local_catalog",
        "objects": out,
        "counts": {
            "total": len(out),
            "not_reviewed": sum(1 for o in out if o["review_status"] == "not_reviewed"),
        },
    }
