"""Test helper: establish protected-asset (Group A) membership the way the
real lifecycle does -- a successful ingest run whose named-entry resolution
maps a registry entry to a catalog object. Object type alone never makes an
object protected."""

from backend.db import insert_ingest_run


def record_successful_ingest(resolution, unresolved=None, used_cache=True):
    """`resolution` maps "<group>:<name_query>" -> norad_id, as ingest records it.
    A locally seeded fixture catalog is not a live network ingest, so used_cache
    defaults to True (the pipeline then labels runs "cached", as before)."""
    return insert_ingest_run("CelesTrak public GP/TLE data (test fixture)", used_cache=used_cache,
                             object_count=len(resolution), counts={}, resolution=resolution,
                             unresolved=unresolved, resolved_count=len(resolution))


def protect(norad_id, name_query="TEST-ASSET-SAT"):
    """Mark one catalog object as the resolution of an active Group A entry."""
    return record_successful_ingest({f"A:{name_query}": str(norad_id)})
