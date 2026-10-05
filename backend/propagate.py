"""SGP4 propagation via skyfield (blueprint Section 6.2)."""

import logging
from datetime import datetime, timedelta, timezone

import numpy as np
from skyfield.api import EarthSatellite, load

from backend.config import PROPAGATION_STEP_SECONDS, PROPAGATION_WINDOW_HOURS
from backend.db import list_objects
from backend.orbital_formats import satrec_for, source_format_of

logger = logging.getLogger(__name__)

# builtin=True avoids a network fetch for leap-second/delta-T data, keeping
# propagation free of network access once the package is installed.
_TS = load.timescale(builtin=True)

MAX_TLE_AGE_DAYS = 30


def _make_satellite(tle_line1, tle_line2, name=""):
    return EarthSatellite(tle_line1, tle_line2, name, _TS)


def satellite_for(obj):
    """Source-independent EarthSatellite factory for one object (normalized
    record or db.objects row). TLE objects (source_format 'tle' or absent)
    use exactly the same EarthSatellite(line1, line2, name, ts) construction
    as before OMM support, so TLE results are bit-identical; OMM objects are
    initialised natively from their stored OMM elements (sgp4.omm) -- no TLE
    lines are generated, so catalog numbers > 99999 work."""
    name = obj.get("name") or ""
    if source_format_of(obj) == "tle":
        return _make_satellite(obj["tle_line1"], obj["tle_line2"], name)
    sat = EarthSatellite.from_satrec(satrec_for(obj), _TS)
    sat.name = name
    return sat


def _tle_epoch_age_days(sat):
    now = _TS.now()
    return now.tt - sat.epoch.tt


def tle_age_days_for(obj_or_line1, tle_line2=None):
    """Public wrapper: element-set epoch age in days, for callers (e.g.
    provenance) that don't already have an EarthSatellite instance. Accepts
    an object (TLE or OMM; see satellite_for) or, as before, raw TLE lines."""
    if tle_line2 is None and isinstance(obj_or_line1, dict):
        return _tle_epoch_age_days(satellite_for(obj_or_line1))
    return _tle_epoch_age_days(_make_satellite(obj_or_line1, tle_line2))


def propagate_object(tle_line1, tle_line2, times):
    """Return ECI (GCRS) positions in km, shape (len(times), 3)."""
    sat = _make_satellite(tle_line1, tle_line2)
    sky_times = _TS.from_datetimes(times)
    geocentric = sat.at(sky_times)
    return geocentric.position.km.T  # (N, 3)


def velocity_at(tle_line1, tle_line2, when):
    sat = _make_satellite(tle_line1, tle_line2)
    sky_time = _TS.from_datetime(when)
    geocentric = sat.at(sky_time)
    return geocentric.velocity.km_per_s  # (3,)


def _time_grid(window_hours=PROPAGATION_WINDOW_HOURS, step_seconds=PROPAGATION_STEP_SECONDS, start=None):
    """Inclusive screening grid: start, start+step, ..., start+window. Both
    endpoints are sampled, so a 72 h window at 60 s gives 72*60 + 1 = 4321
    samples ending exactly at start+72:00:00 (no sample beyond the horizon).
    A window that isn't a whole number of steps stops at the last step that
    still lies inside it."""
    if start is None:
        start = datetime.now(timezone.utc)
    n_intervals = int(window_hours * 3600 // step_seconds)
    return [start + timedelta(seconds=i * step_seconds) for i in range(n_intervals + 1)]


def make_state_functions(objects, ids=None):
    """Per-object SGP4 state functions state(datetime) -> (pos_km (3,),
    vel_km_s (3,)), in GCRS -- the same frame as propagate_all's positions
    grid -- so sub-grid TCA refinement (backend/conjunction.py) evaluates the
    actual propagator rather than an interpolation of grid samples.
    `ids` optionally restricts which objects get a function."""
    wanted = set(ids) if ids is not None else None
    fns = {}
    for obj in objects:
        if wanted is not None and obj["norad_id"] not in wanted:
            continue
        try:
            sat = satellite_for(obj)
        except Exception as exc:
            logger.warning("No state function for %s (%s): %s", obj["name"], obj["norad_id"], exc)
            continue

        def state(when, _sat=sat):
            geocentric = _sat.at(_TS.from_datetime(when))
            return np.asarray(geocentric.position.km, dtype=float), np.asarray(geocentric.velocity.km_per_s, dtype=float)

        fns[obj["norad_id"]] = state
    return fns


def propagate_all(window_hours=PROPAGATION_WINDOW_HOURS, step_seconds=PROPAGATION_STEP_SECONDS, objects=None,
                  start=None):
    """Propagate every stored object across a shared time grid.

    Returns (times, positions) where positions is dict[norad_id] -> (N,3) km array.
    Objects with stale (>30 day) TLE epochs or that raise during propagation are
    skipped and logged rather than crashing the whole pipeline. `objects`
    defaults to the real catalog (list_objects()); the demo pipeline passes
    the override-applied catalog instead. The grid is inclusive of both ends
    (see _time_grid); `start` defaults to now (UTC).
    """
    times = _time_grid(window_hours, step_seconds, start=start)
    sky_times = _TS.from_datetimes(times)
    positions = {}
    for obj in (objects if objects is not None else list_objects()):
        try:
            sat = satellite_for(obj)
            age_days = _tle_epoch_age_days(sat)
            if age_days > MAX_TLE_AGE_DAYS:
                logger.warning("Skipping %s (%s): TLE epoch %.1f days old", obj["name"], obj["norad_id"], age_days)
                continue
            geocentric = sat.at(sky_times)
            positions[obj["norad_id"]] = geocentric.position.km.T
        except Exception as exc:
            logger.warning("Skipping %s (%s): propagation failed: %s", obj["name"], obj["norad_id"], exc)
    return times, positions


def current_positions(objects=None):
    """Position now for every object, as lat/lon/alt for the globe."""
    now = datetime.now(timezone.utc)
    sky_now = _TS.from_datetime(now)
    results = []
    for obj in (objects if objects is not None else list_objects()):
        try:
            sat = satellite_for(obj)
            age_days = _tle_epoch_age_days(sat)
            if age_days > MAX_TLE_AGE_DAYS:
                logger.warning("Skipping %s (%s): TLE epoch %.1f days old", obj["name"], obj["norad_id"], age_days)
                continue
            geocentric = sat.at(sky_now)
            subpoint = geocentric.subpoint()
            results.append({
                "norad_id": obj["norad_id"],
                "name": obj["name"],
                "object_type": obj["object_type"],
                "criticality": obj["criticality"],
                "lat": subpoint.latitude.degrees,
                "lon": subpoint.longitude.degrees,
                "alt_km": subpoint.elevation.km,
                "tle_age_days": round(age_days, 1),
                "demo_adjusted": bool(obj.get("demo_adjusted", False)),
            })
        except Exception as exc:
            logger.warning("Skipping %s (%s): propagation failed: %s", obj["name"], obj["norad_id"], exc)
    return results


def position_tracks(window_minutes=120, step_seconds=60, objects=None):
    """Lat/lon/alt sample series per object over the next window_minutes,
    for smooth client-side time-lapse animation on the globe (the frontend
    feeds these into a Cesium SampledPositionProperty and runs the scene
    clock at a multiplier, so orbits are visible in seconds)."""
    start = datetime.now(timezone.utc)
    n_steps = int(window_minutes * 60 / step_seconds) + 1
    times = [start + timedelta(seconds=i * step_seconds) for i in range(n_steps)]
    sky_times = _TS.from_datetimes(times)
    results = []
    for obj in (objects if objects is not None else list_objects()):
        try:
            sat = satellite_for(obj)
            if _tle_epoch_age_days(sat) > MAX_TLE_AGE_DAYS:
                continue
            geocentric = sat.at(sky_times)
            subpoint = geocentric.subpoint()
            results.append({
                "norad_id": obj["norad_id"],
                "name": obj["name"],
                "object_type": obj["object_type"],
                "criticality": obj["criticality"],
                "start": times[0].isoformat(),
                "step_seconds": step_seconds,
                "lat": np.round(subpoint.latitude.degrees, 4).tolist(),
                "lon": np.round(subpoint.longitude.degrees, 4).tolist(),
                "alt_km": np.round(subpoint.elevation.km, 2).tolist(),
                "demo_adjusted": bool(obj.get("demo_adjusted", False)),
            })
        except Exception as exc:
            logger.warning("Skipping %s (%s) in tracks: %s", obj["name"], obj["norad_id"], exc)
    return results


def separation_profile(norad_a, norad_b, tca_iso, window_minutes=90, step_seconds=60, objects=None):
    """Separation distance (km) between two objects over tca +/- window/2 --
    the classic conjunction-analysis chart: distance collapsing to the miss
    distance at TCA, then opening back up."""
    objects = {o["norad_id"]: o for o in (objects if objects is not None else list_objects())}
    obj_a, obj_b = objects.get(norad_a), objects.get(norad_b)
    if not obj_a or not obj_b:
        return None

    tca = datetime.fromisoformat(tca_iso)
    if tca.tzinfo is None:
        tca = tca.replace(tzinfo=timezone.utc)
    half = timedelta(minutes=window_minutes / 2)
    n_steps = int(window_minutes * 60 / step_seconds) + 1
    times = [tca - half + timedelta(seconds=i * step_seconds) for i in range(n_steps)]
    sky_times = _TS.from_datetimes(times)

    sat_a = satellite_for(obj_a)
    sat_b = satellite_for(obj_b)
    pos_a = sat_a.at(sky_times).position.km
    pos_b = sat_b.at(sky_times).position.km
    sep_km = np.linalg.norm(pos_a - pos_b, axis=0)

    return {
        "start": times[0].isoformat(),
        "step_seconds": step_seconds,
        "tca": tca.isoformat(),
        "sep_km": np.round(sep_km, 3).tolist(),
    }


if __name__ == "__main__":
    objs = list_objects()
    leo_sat = next((o for o in objs if o["object_type"] == "satellite" and "GSAT" not in o["name"] and "IRNSS" not in o["name"]), objs[0] if objs else None)
    if not leo_sat:
        print("No objects in DB -- run `python -m backend.ingest` first.")
    else:
        times = [datetime.now(timezone.utc) + timedelta(minutes=m) for m in (0, 30, 60)]
        sat = satellite_for(leo_sat)
        sky_times = _TS.from_datetimes(times)
        print(f"Object: {leo_sat['name']} ({leo_sat['norad_id']})")
        for t, sky_t in zip(times, sky_times):
            geocentric = sat.at(sky_t)
            subpoint = geocentric.subpoint()
            pos = geocentric.position.km
            print(f"  {t.isoformat()}  ECI={pos.round(1).tolist()}  alt={subpoint.elevation.km:.1f} km")
