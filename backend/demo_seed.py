"""Demo-reliability seeding core (blueprint Section 10), importable by both
scripts/seed_demo_event.py (CLI) and the /api/demo/seed endpoint (live demo).

Collision demo: builds a TLE for one real debris object (its NORAD ID and
catalog identity) on a genuine *crossing* orbit -- a plane tilted by a small
disclosed angle about the protected asset's predicted position at a chosen
encounter time (~6 h ahead), so the two objects meet there with a real
relative velocity and a well-defined time of closest approach.

Proximity demo: aligns one foreign satellite's orbital plane and phase with
the asset's, offset by a small disclosed along-track distance, producing a
sustained co-orbital dwell.

ALWAYS logged as a simulation adjustment; never the default path. The
seeded TLE is stored in the demo_overrides table, never in `objects`: the
real CelesTrak row stays untouched, only the demo screening run applies the
override, and the next normal screening clears it. Pc and risk tier are
whatever the unchanged screening/risk pipeline computes for that geometry.
"""

import logging
import math
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
from sgp4.api import Satrec, WGS72, jday
from sgp4.exporter import export_tle

from backend.conjunction import refine_tca
from backend.db import init_db, list_events, list_objects, resolved_protected_norad_ids, set_demo_override
from backend.orbital_formats import satrec_for, tle_representable
from backend.pipeline import PIPELINE_LOCK, run_screening_and_briefs
from backend.propagate import MAX_TLE_AGE_DAYS

logger = logging.getLogger(__name__)

GM_KM3_S2 = 398600.4418
_SGP4_EPOCH_JD = 2433281.5  # sgp4init epoch origin: 1949-12-31 00:00 UTC

# Collision-demo encounter design (disclosed in the result dict and log).
COLLISION_ENCOUNTER_LEAD_HOURS = 6.0
# Plane tilt about the encounter position. Relative speed ~ 2 v sin(tilt/2)
# ~ 0.2 km/s for LEO -- a genuine crossing, while staying well below
# ~0.33 km/s so the closest 60 s grid sample is always inside the unchanged
# 10 km screening threshold (worst case v_rel * step / 2).
COLLISION_PLANE_OFFSET_DEG = 1.5
# Seeded mean semi-major axis is 15 km above the asset's (mean motion held
# fixed at that value; perigee placed at the encounter point). The period
# difference makes successive revolutions drift ~3*pi*15 ~ 140 km apart
# along-track, so the encounter is unique in the screening window instead of
# recurring every orbit as two equal-period crossing orbits would. (With a
# smaller offset, neighbouring passes at a few km were observed on live data.)
COLLISION_SMA_OFFSET_KM = 15.0
# Designed miss distance at TCA, perpendicular to the relative velocity.
COLLISION_TARGET_MISS_KM = 0.02

# Controlled two-step DEMO history (collision mode only). Same object pair and
# same designed encounter epoch, two disclosed designed miss distances, so the
# unchanged correlation rule links the two demo observations into one track.
# Pc/tier are NOT set here -- they are whatever the unchanged pipeline computes.
# Measured on the real catalog (~6-7 h lead, sigma ~0.113 km, HBR 20 m):
# 0.52 km -> Pc ~4e-5 (High, >=3x from both tier boundaries across 5-7 h
# leads); 0.02 km -> Pc ~7.7e-3 (Critical).
COLLISION_HISTORY_MISS_KM = {1: 0.52, 2: 0.02}
HISTORY_STEPS = 2
# Step 2 reuses step 1's encounter epoch only while it is still at least this
# far ahead (otherwise the history is stale: start again with step 1).
HISTORY_MIN_REMAINING_LEAD_HOURS = 1.0


class DemoScenarioError(RuntimeError):
    """Raised for an *expected* demo scenario failure: a required catalog
    object is missing, a TLE cannot be parsed/propagated, the encounter
    geometry cannot be constructed, or the screening pass does not produce
    the intended event. Carries an operator-facing message only (no
    traceback) -- the caller (API route) surfaces this as a clean "demo
    failed" response instead of a silent empty feed or a raw exception.
    Anything else raised from seed_event is a genuine internal error.

    reason: short machine-readable code ("missing_object", "invalid_tle",
    "propagation_failed", "geometry_failed", "no_event").
    status_code: HTTP status the API returns -- 409 when the catalog lacks
    a required object (a state precondition), 502 otherwise (the scenario
    ran but could not produce its event)."""

    def __init__(self, message, reason="no_event", status_code=502):
        super().__init__(message)
        self.reason = reason
        self.status_code = status_code


def _orbital_radius_km(satrec):
    n_rad_per_s = satrec.no_kozai / 60.0
    return (GM_KM3_S2 / (n_rad_per_s ** 2)) ** (1.0 / 3.0)


def _pick(objects, object_type, name_hint=None):
    # The disclosed demo override is written as classic TLE lines, so only
    # objects whose catalog number fits the 5-digit TLE field are eligible
    # (OMM-ingested objects up to 99999 qualify; 6-digit ones do not).
    candidates = [o for o in objects if o["object_type"] == object_type and tle_representable(o)]
    if name_hint:
        matches = [o for o in candidates if name_hint.upper() in o["name"].upper()]
        if matches:
            return matches[0]
    if not candidates:
        raise DemoScenarioError(
            f"No objects of type '{object_type}' in the catalog -- run a catalog refresh "
            "(`/api/refresh` or `python -m backend.ingest`) first.",
            reason="missing_object", status_code=409,
        )
    return candidates[0]


def _pick_protected_asset(objects, name_hint=None):
    """The demo's protected asset: chosen only among catalog objects resolved
    to active protected-asset registry entries by the latest successful
    refresh -- the same set the screening pipeline uses as Group A (never by
    object type). `name_hint` still prefers a matching name within that set."""
    protected = resolved_protected_norad_ids()
    pool = [o for o in objects if str(o["norad_id"]) in protected and tle_representable(o)]
    if not pool:
        raise DemoScenarioError(
            "No protected asset in the current catalog -- configure an active entry in the protected-asset "
            "registry and run a successful catalog refresh (`/api/refresh`) first.",
            reason="missing_object", status_code=409,
        )
    if name_hint:
        matches = [o for o in pool if name_hint.upper() in o["name"].upper()]
        if matches:
            return matches[0]
    return pool[0]


def _parse_tle(obj):
    """Parse a catalog element set (TLE lines, or stored OMM elements via
    orbital_formats.satrec_for), turning a malformed record into an operator
    message naming the object instead of an opaque sgp4 ValueError."""
    try:
        sat = satrec_for(obj)
    except (ValueError, IndexError, TypeError, KeyError) as exc:
        raise DemoScenarioError(
            f"The catalog TLE for {obj['name']} ({obj['norad_id']}) could not be parsed ({exc}). "
            "Re-run a catalog refresh, then retry the scenario.",
            reason="invalid_tle",
        ) from exc
    if getattr(sat, "error", 0):
        raise DemoScenarioError(
            f"The catalog TLE for {obj['name']} ({obj['norad_id']}) is invalid (SGP4 error {sat.error}). "
            "Re-run a catalog refresh, then retry the scenario.",
            reason="invalid_tle",
        )
    return sat


def _find_seeded_event(asset_norad_id, target_norad_id, expected_class, near_tca=None):
    """Locate the specific event produced for this scenario's object pair,
    so the caller can hand the UI an exact event_id to open -- rather than
    the presenter having to guess which row in a possibly multi-event feed
    is the demo's, or assuming it sorted to the top. A pair can have more
    than one encounter in the window; with near_tca (the designed encounter
    time) the encounter closest to it is returned."""
    matches = [
        evt for evt in list_events()
        if evt["event_class"] == expected_class
        and {evt["object_a_id"], evt["object_b_id"]} == {asset_norad_id, target_norad_id}
    ]
    if not matches:
        return None
    if near_tca is None:
        return matches[0]
    return min(matches, key=lambda e: abs((datetime.fromisoformat(e["tca_timestamp"]) - near_tca).total_seconds()))


def _jd(when):
    return jday(when.year, when.month, when.day, when.hour, when.minute,
                when.second + when.microsecond / 1e6)


def _teme_state(sat, when):
    jd, fr = _jd(when)
    err, r, v = sat.sgp4(jd, fr)
    if err:
        raise DemoScenarioError(f"SGP4 propagation error {err} while designing the demo encounter.",
                                reason="propagation_failed")
    return np.array(r), np.array(v)


def _rotate(vec, axis, angle_rad):
    """Rodrigues rotation of vec about unit axis."""
    return (vec * math.cos(angle_rad) + np.cross(axis, vec) * math.sin(angle_rad)
            + axis * np.dot(axis, vec) * (1 - math.cos(angle_rad)))


def _unit(v):
    return v / np.linalg.norm(v)


def _copy_identity(seeded, target_satrec):
    seeded.satnum_str = target_satrec.satnum_str
    seeded.classification = getattr(target_satrec, "classification", "U")
    seeded.intldesg = getattr(target_satrec, "intldesg", "")
    seeded.ephtype = getattr(target_satrec, "ephtype", 0)
    seeded.elnum = getattr(target_satrec, "elnum", 1)
    seeded.revnum = getattr(target_satrec, "revnum", 1)


def _plane_angle_deg(sat_a, sat_b, when):
    """Angle between the two objects' osculating orbit planes at `when`."""
    ra, va = _teme_state(sat_a, when)
    rb, vb = _teme_state(sat_b, when)
    ha, hb = _unit(np.cross(ra, va)), _unit(np.cross(rb, vb))
    return math.degrees(math.acos(float(np.clip(np.dot(ha, hb), -1.0, 1.0))))


def _encounter_schedule(now, lead_hours):
    """Encounter at the next whole UTC hour at least lead_hours ahead; TLE
    epoch one hour before (lead_hours + 1) so it is never in the future.
    Making the design a pure function of (asset TLE, encounter hour) keeps
    the seeded TLE -- and therefore the seeded-rng Monte Carlo Pc --
    reproducible across re-runs within the same hour."""
    earliest = now + timedelta(hours=lead_hours)
    t_e = earliest.replace(minute=0, second=0, microsecond=0)
    if t_e < earliest:
        t_e += timedelta(hours=1)
    return t_e, t_e - timedelta(hours=lead_hours + 1)


def _design_crossing(asset_satrec, target_satrec, t_e, epoch_time, plane_offset_deg, sma_offset_km, miss_km):
    """Crossing design with the requested *osculating* plane offset: the
    Gauss-Newton position fix in _design_crossing_once slightly rotates the
    plane, so the requested tilt is corrected until the achieved angle at
    the encounter matches. Returns (seeded Satrec, t_e, achieved angle)."""
    tilt = plane_offset_deg
    for _ in range(4):
        seeded = _design_crossing_once(asset_satrec, target_satrec, t_e, epoch_time, tilt,
                                            sma_offset_km, miss_km)
        achieved = _plane_angle_deg(asset_satrec, seeded, t_e)
        if abs(achieved - plane_offset_deg) < 0.005:
            break
        tilt += plane_offset_deg - achieved
    return seeded, achieved


def _design_crossing_once(asset_satrec, target_satrec, t_e, epoch_time, plane_offset_deg, sma_offset_km, miss_km):
    """Design a TLE that crosses the asset's predicted position at
    t_e (TLE epoch = epoch_time).

    1. Asset state (r_e, v_e) at t_e from its real TLE.
    2. Seeded orbit plane = asset plane rotated by plane_offset_deg about r_e,
       so both planes contain the encounter point. Mean motion is fixed at
       the asset's mean semi-major axis + sma_offset_km (sets the period
       difference); perigee is placed at the encounter point, with
       eccentricity chosen so the perigee radius is |r_e|.
    3. Those are elements *at t_e*; SGP4's own secular rates back them out
       to the TLE epoch.
    4. Gauss-Newton on (M, i, RAAN, e) against SGP4 itself removes the
       short-period residuals, landing the seeded position at
       r_e + miss_km in the direction perpendicular to the relative velocity.
       Mean motion is not a free parameter, so the period difference that
       keeps the encounter unique is preserved.
    Returns the seeded Satrec."""
    epoch_days = sum(_jd(epoch_time)) - _SGP4_EPOCH_JD
    dt_min = (t_e - epoch_time).total_seconds() / 60.0

    r_e, v_e = _teme_state(asset_satrec, t_e)
    r_hat = _unit(r_e)
    h_new = _rotate(_unit(np.cross(r_e, v_e)), r_hat, math.radians(plane_offset_deg))
    inclo = math.acos(float(np.clip(h_new[2], -1.0, 1.0)))
    nodeo_e = math.atan2(h_new[0], -h_new[1]) % (2 * math.pi)
    node_vec = np.array([math.cos(nodeo_e), math.sin(nodeo_e), 0.0])
    u_e = math.atan2(np.dot(r_hat, np.cross(h_new, node_vec)), np.dot(r_hat, node_vec)) % (2 * math.pi)

    a_km = _orbital_radius_km(asset_satrec) + sma_offset_km
    ecco = max(1.0 - np.linalg.norm(r_e) / a_km, 1e-4)
    argp_e = u_e  # perigee at the encounter point
    no_kozai = math.sqrt(GM_KM3_S2 / a_km ** 3) * 60.0  # rad/min

    def build(mo, incl, node, ecc, argp):
        if not 0.0 < ecc < 0.1:
            raise DemoScenarioError("Demo crossing design produced an invalid eccentricity.",
                                    reason="geometry_failed")
        sat = Satrec()
        sat.sgp4init(WGS72, "i", target_satrec.satnum, epoch_days, asset_satrec.bstar,
                     asset_satrec.ndot, asset_satrec.nddot, ecc, argp % (2 * math.pi),
                     incl, mo % (2 * math.pi), no_kozai, node % (2 * math.pi))
        return sat

    mo, nodeo, argpo = 0.0, nodeo_e, argp_e
    for _ in range(3):
        sat = build(mo, inclo, nodeo, ecco, argpo)
        nodeo = nodeo_e - sat.nodedot * dt_min
        argpo = argp_e - sat.argpdot * dt_min
        mo = 0.0 - sat.mdot * dt_min

    # Aim point: miss_km off the asset, perpendicular to the (approximate)
    # relative velocity and as close to radial as possible.
    v_rel_hat = _unit(_rotate(v_e, r_hat, math.radians(plane_offset_deg)) - v_e)
    offset_dir = _unit(r_hat - np.dot(r_hat, v_rel_hat) * v_rel_hat)
    target = r_e + miss_km * offset_dir

    x = np.array([mo, inclo, nodeo, ecco])
    scales = np.array([1e-6, 1e-6, 1e-6, 1e-6])

    def residual(params):
        r, _ = _teme_state(build(params[0], params[1], params[2], params[3], argpo), t_e)
        return r - target

    for _ in range(30):
        f = residual(x)
        if np.linalg.norm(f) < 1e-5:  # 1 cm
            break
        jac = np.column_stack([residual(x + scales * np.eye(4)[k]) - f for k in range(4)])
        dz = np.linalg.lstsq(jac, -f, rcond=None)[0]
        x = x + dz * scales
    else:
        raise DemoScenarioError("Could not converge the demo crossing-encounter design.",
                                reason="geometry_failed")

    seeded = build(x[0], x[1], x[2], x[3], argpo)
    _copy_identity(seeded, target_satrec)
    return seeded


def _crossing_check(asset_satrec, seeded_l1, seeded_l2, t_e, step_seconds=60):
    """Verify the *exported* TLE (after TLE field quantisation): refine the
    closest approach within +/-1 step of the design time with the same
    minimiser the screening pipeline uses."""
    seeded = Satrec.twoline2rv(seeded_l1, seeded_l2)
    tca, miss, rel_v, _, _, _ = refine_tca(
        t_e, step_seconds,
        lambda when: _teme_state(asset_satrec, when),
        lambda when: _teme_state(seeded, when),
    )
    return tca, miss, rel_v


def _build_seeded_tle(asset, target, asset_satrec, target_satrec, proximity, separation_km,
                      encounter_time=None):
    """Construct the disclosed demo TLE for `target` (geometry only; no DB
    writes). Returns (tle_line1, tle_line2, geometry dict, design_tca --
    None for proximity).

    encounter_time (collision only): reuse this encounter epoch instead of
    scheduling a new one (demo history step 2 keeps step 1's epoch so both
    observations describe the same designed encounter). The TLE epoch stays
    COLLISION_ENCOUNTER_LEAD_HOURS + 1 h before it, as for a fresh design."""
    design_tca = None
    if proximity:
        radius_km = _orbital_radius_km(asset_satrec)
        delta_mo_rad = separation_km / radius_km
        seeded = Satrec()
        seeded.sgp4init(
            WGS72,
            "i",
            target_satrec.satnum,
            asset_satrec.jdsatepoch + asset_satrec.jdsatepochF - _SGP4_EPOCH_JD,
            asset_satrec.bstar,
            asset_satrec.ndot,
            asset_satrec.nddot,
            asset_satrec.ecco,
            asset_satrec.argpo,
            asset_satrec.inclo,
            (asset_satrec.mo + delta_mo_rad) % (2 * math.pi),
            asset_satrec.no_kozai,
            asset_satrec.nodeo,
        )
        _copy_identity(seeded, target_satrec)
        new_line1, new_line2 = export_tle(seeded)
        geometry = {
            "geometry": "co-orbital",
            "mean_anomaly_offset_deg": round(math.degrees(delta_mo_rad), 4),
        }
        logger.warning(
            "SIMULATION ADJUSTMENT (proximity demo seed): %s (%s) orbit aligned to %s (%s), "
            "mean anomaly offset %.4f deg (~%.2f km along-track). Disclosed demo-only change.",
            target["name"], target["norad_id"], asset["name"], asset["norad_id"],
            math.degrees(delta_mo_rad), separation_km,
        )
    else:
        now = datetime.now(timezone.utc)
        if encounter_time is None:
            t_e, epoch_time = _encounter_schedule(now, COLLISION_ENCOUNTER_LEAD_HOURS)
        else:
            t_e = encounter_time
            epoch_time = t_e - timedelta(hours=COLLISION_ENCOUNTER_LEAD_HOURS + 1)
        seeded, plane_offset = _design_crossing(
            asset_satrec, target_satrec, t_e, epoch_time,
            COLLISION_PLANE_OFFSET_DEG, COLLISION_SMA_OFFSET_KM, separation_km,
        )
        new_line1, new_line2 = export_tle(seeded)
        design_tca, design_miss, design_rel_v = _crossing_check(asset_satrec, new_line1, new_line2, t_e)
        geometry = {
            "geometry": "crossing",
            "encounter_lead_hours": round((t_e - now).total_seconds() / 3600.0, 3),
            "plane_offset_deg": round(plane_offset, 3),
            "sma_offset_km": COLLISION_SMA_OFFSET_KM,
            "encounter_epoch": t_e.isoformat(),
            "design_tca": design_tca.isoformat(),
            "design_miss_distance_km": round(design_miss, 5),
            "design_rel_velocity_km_s": round(design_rel_v, 4),
            # Kept for result-dict compatibility; not meaningful for a crossing.
            "mean_anomaly_offset_deg": None,
        }
        logger.warning(
            "SIMULATION ADJUSTMENT (collision demo seed): %s (%s) given a crossing orbit through "
            "%s (%s)'s predicted position at %s (+%.1f h): plane tilted %.1f deg about the "
            "encounter point, semi-major axis +%.1f km; designed miss %.3f km at %.3f km/s "
            "relative velocity. Disclosed demo-only change; real catalog unchanged.",
            target["name"], target["norad_id"], asset["name"], asset["norad_id"],
            design_tca.isoformat(), (t_e - now).total_seconds() / 3600.0, plane_offset,
            COLLISION_SMA_OFFSET_KM, design_miss, design_rel_v,
        )
    return new_line1, new_line2, geometry, design_tca


def _history_step1_baseline():
    """The latest demo screening run's history block if it is a still-usable
    demo history step 1, else raise DemoScenarioError(history_not_started)."""
    from backend.event_history import latest_demo_scenario

    scenario = latest_demo_scenario()
    history = (scenario or {}).get("history") or {}
    refusal = ("Demo history step 2 needs demo history step 1 to be the most recent demo run. "
               "Start the demo history again with step 1.")
    if scenario is None or scenario.get("scenario") != "collision_demo" or history.get("step") != 1:
        raise DemoScenarioError(refusal, reason="history_not_started", status_code=409)
    try:
        t_e = datetime.fromisoformat(str(history["encounter_epoch"]))
    except (KeyError, TypeError, ValueError):
        raise DemoScenarioError(refusal, reason="history_not_started", status_code=409) from None
    if t_e.tzinfo is None:
        t_e = t_e.replace(tzinfo=timezone.utc)
    if t_e - datetime.now(timezone.utc) < timedelta(hours=HISTORY_MIN_REMAINING_LEAD_HOURS):
        raise DemoScenarioError(
            "Demo history step 1 is too old: its designed encounter is less than "
            f"{HISTORY_MIN_REMAINING_LEAD_HOURS:g} h ahead. Start the demo history again with step 1.",
            reason="history_not_started", status_code=409)
    return history, t_e


def seed_event(asset_hint="CARTOSAT", proximity=False, separation_km=None,
                debris_hint="FENGYUN", foreign_hint="SENTINEL", history_step=None):
    """Store a demo TLE for one object as a demo override (real catalog
    untouched) to guarantee an event, then run the normal screening pipeline
    in demo mode. Returns an info dict describing the disclosed simulation
    adjustment.

    separation_km: proximity mode -- along-track offset (default 10 km);
    collision mode -- designed miss distance at TCA (default 0.02 km).

    history_step (collision only): 1 or 2 runs the controlled two-step demo
    history (COLLISION_HISTORY_MISS_KM). Step 1 starts a fresh demo track;
    step 2 reuses step 1's encounter epoch and is correlated against it.
    Every seed except step 2 starts a fresh demo correlation baseline, so
    repeated rehearsals never chain onto older demo runs (rows are kept).

    Raises DemoScenarioError for expected scenario failures; any other
    exception is an internal error and propagates unchanged."""
    init_db()
    # Whole sequence (select objects -> set override -> demo screening ->
    # match event) is atomic w.r.t. /api/refresh: see pipeline.PIPELINE_LOCK.
    if history_step is not None:
        if proximity:
            raise ValueError("history_step is only supported for the collision demo")
        if history_step not in COLLISION_HISTORY_MISS_KM:
            raise ValueError(f"history_step must be one of {sorted(COLLISION_HISTORY_MISS_KM)}")
    with PIPELINE_LOCK:
        return _seed_event_locked(asset_hint, proximity, separation_km, debris_hint, foreign_hint,
                                  history_step)


def _seed_event_locked(asset_hint, proximity, separation_km, debris_hint, foreign_hint, history_step=None):
    history, encounter_time = None, None
    if history_step == 2:
        prev_history, encounter_time = _history_step1_baseline()
        history = {"id": prev_history.get("id"), "step": 2, "of": HISTORY_STEPS,
                   "encounter_epoch": encounter_time.isoformat()}
    elif history_step == 1:
        history = {"id": uuid.uuid4().hex[:12], "step": 1, "of": HISTORY_STEPS}
    if history_step is not None:
        separation_km = COLLISION_HISTORY_MISS_KM[history_step]

    objects = list_objects()

    asset = _pick_protected_asset(objects, asset_hint)
    if proximity:
        target = _pick(objects, "foreign_sat", foreign_hint)
        separation_km = separation_km or 10.0
    else:
        target = _pick(objects, "debris", debris_hint)
        separation_km = separation_km or COLLISION_TARGET_MISS_KM

    asset_satrec = _parse_tle(asset)
    target_satrec = _parse_tle(target)

    kind = "proximity" if proximity else "conjunction"
    scenario = "proximity_demo" if proximity else "collision_demo"
    expected_class = "proximity_watch" if proximity else "collision_risk"

    try:
        new_line1, new_line2, geometry, design_tca = _build_seeded_tle(
            asset, target, asset_satrec, target_satrec, proximity, separation_km,
            encounter_time=encounter_time)
    except (ValueError, ArithmeticError, np.linalg.LinAlgError) as exc:
        # Numeric failure building the encounter (sgp4init/export_tle
        # ValueError, math domain error, singular least-squares system).
        raise DemoScenarioError(
            f"Could not construct the {scenario.replace('_', ' ')} geometry for "
            f"{asset['name']} <-> {target['name']} ({type(exc).__name__}). Re-run a catalog "
            "refresh, then retry the scenario.",
            reason="geometry_failed",
        ) from exc

    set_demo_override(
        norad_id=target["norad_id"],
        tle_line1=new_line1,
        tle_line2=new_line2,
        scenario=scenario,
        derived_from_norad_id=asset["norad_id"],
    )

    demo_scenario = {
        "scenario": scenario,
        "asset_norad_id": asset["norad_id"],
        "asset_name": asset["name"],
        "adjusted_norad_id": target["norad_id"],
        "adjusted_name": target["name"],
        "separation_km": separation_km,
        **geometry,
    }
    if history is not None:
        history.setdefault("encounter_epoch", geometry.get("encounter_epoch"))
        demo_scenario["history"] = history
    # Each rehearsal starts its own demo track; only history step 2 is
    # correlated against the previous demo run (its step 1).
    summary = run_screening_and_briefs(is_demo=True, demo_scenario=demo_scenario,
                                       reset_baseline=history_step != 2)

    matched_event = _find_seeded_event(asset["norad_id"], target["norad_id"], expected_class,
                                       near_tca=None if proximity else design_tca)
    if matched_event is None:
        raise DemoScenarioError(
            f"The {scenario.replace('_', ' ')} scenario did not produce the expected "
            f"{expected_class.replace('_', ' ')} event for {asset['name']} <-> {target['name']}. "
            f"Possible causes: a stale TLE epoch (>{MAX_TLE_AGE_DAYS} days) for one of the two "
            "objects causing it to be skipped during propagation, a propagation failure, or the "
            "scenario target no longer being present in the working set. Re-running `/api/refresh` "
            "to re-ingest TLEs, then retrying the scenario, resolves most cases."
        )

    return {
        "simulation_adjustment": True,
        "scenario": scenario,
        "kind": kind,
        "expected_event_class": expected_class,
        "asset": {"name": asset["name"], "norad_id": asset["norad_id"]},
        "adjusted_object": {"name": target["name"], "norad_id": target["norad_id"], "type": target["object_type"]},
        "separation_km": separation_km,
        **geometry,
        "new_tle": [new_line1, new_line2],
        "override_storage": "demo_overrides",
        "real_catalog_modified": False,
        "event_id": matched_event["id"],
        "history": history,
        **summary,
    }
