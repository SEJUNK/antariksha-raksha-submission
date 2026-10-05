"""Simplified analytic Pc indicator + criticality-weighted priority (blueprint Section 6.4)."""

from datetime import datetime, timezone

import numpy as np
from scipy.integrate import quad
from scipy.special import i0e
from scipy.stats import ncx2

from backend.config import (
    CRITICALITY_MULTIPLIERS,
    PC_HARD_BODY_RADIUS_KM,
    PC_N_SAMPLES,
    PC_SIGMA_KM_BASE,
    PC_SIGMA_KM_GROWTH_PER_DAY,
    RISK_TIER_CRITICAL_PC,
    RISK_TIER_HIGH_PC,
    RISK_TIER_MEDIUM_PC,
)

# Fixed priority band for proximity_watch events, below any Medium collision
# event, per blueprint Section 6.4. A Medium collision event's floor is
# pc=1e-6, so PROXIMITY_WATCH_BASE_PRIORITY sits just under that band's typical
# score; extreme dwell times can still exceed it via the dwell_bonus.
PROXIMITY_WATCH_BASE_PRIORITY = 0.05


def sigma_km_for(hours_until_tca):
    """Uncertainty growth model: linear in time-to-TCA. A known simplification
    (real conjunction assessments use covariance propagation) but simple and
    defensible for a demo -- see README Limitations."""
    return PC_SIGMA_KM_BASE + PC_SIGMA_KM_GROWTH_PER_DAY * (hours_until_tca / 24.0)


def compute_collision_probability(pos_a, pos_b, rel_velocity_km_s, sigma_km=0.5,
                                   hard_body_radius_km=PC_HARD_BODY_RADIUS_KM,
                                   n_samples=PC_N_SAMPLES, rng=None):
    """Monte Carlo reference (tests/validation only -- NOT used for
    production scoring): sample perturbed positions for A and B
    independently from N(pos, sigma_km^2 * I3); return the fraction of pairs
    closer than hard_body_radius_km. Its resolution is 1/n_samples."""
    if rng is None:
        rng = np.random.default_rng()
    pos_a = np.asarray(pos_a, dtype=float)
    pos_b = np.asarray(pos_b, dtype=float)
    samples_a = rng.normal(loc=pos_a, scale=sigma_km, size=(n_samples, 3))
    samples_b = rng.normal(loc=pos_b, scale=sigma_km, size=(n_samples, 3))
    dist = np.linalg.norm(samples_a - samples_b, axis=1)
    return float(np.mean(dist < hard_body_radius_km))


def risk_tier_from_pc(pc):
    if pc > RISK_TIER_CRITICAL_PC:
        return "Critical"
    if pc > RISK_TIER_HIGH_PC:
        return "High"
    if pc > RISK_TIER_MEDIUM_PC:
        return "Medium"
    return "Low"


def priority_score(pc, criticality):
    """priority = pc * criticality_multiplier * 1e6.

    A negative-log-based 0-100 rescaling was considered and rejected as
    overkill for a sort key -- this monotonic transform of pc is sufficient
    and keeps the formula auditable in a brief/log line."""
    multiplier = CRITICALITY_MULTIPLIERS.get(criticality, 1.0)
    return max(pc, 1e-9) * multiplier * 1e6


def proximity_priority_score(dwell_minutes, extreme_dwell_minutes=180.0):
    """Proximity-watch events sit in a fixed band below Medium collision
    events, unless dwell time is extreme (Section 6.5), in which case they
    can exceed it."""
    dwell_bonus = 0.0
    if dwell_minutes > extreme_dwell_minutes:
        dwell_bonus = (dwell_minutes - extreme_dwell_minutes) / extreme_dwell_minutes
    return PROXIMITY_WATCH_BASE_PRIORITY + dwell_bonus


PC_METHOD = (
    "Simplified analytic encounter-plane indicator: the relative position at the refined TCA "
    "is projected onto the plane perpendicular to the relative velocity; with independent "
    "isotropic per-object sigma, the relative uncertainty in that plane is N(0, 2*sigma^2 I2); "
    "Pc = P(|projected relative position| < hard-body radius), evaluated deterministically"
)
PC_METHOD_3D_FALLBACK = (
    "Simplified analytic 3D indicator (encounter plane undefined because relative velocity "
    "is ~0): Pc = P(|relative position| < hard-body radius) with relative uncertainty "
    "N(0, 2*sigma^2 I3), evaluated deterministically"
)
# Below this relative speed the encounter plane (perpendicular to v_rel) is
# not defined, so the 3D fallback is used instead of the 2D projection.
ENCOUNTER_PLANE_MIN_REL_SPEED_KM_S = 1e-6


def _circle_probability_2d(miss_km, s_km, radius_km):
    """P(|X| < R) for X ~ N(mu, s^2 I2) with |mu| = miss (Rician CDF).

    Integrand: (rho/s^2) exp(-(rho^2 + d^2) / 2s^2) I0(rho d / s^2), written
    with the exponentially scaled Bessel i0e(x) = exp(-x) I0(x) as
    (rho/s^2) exp(-(rho - d)^2 / 2s^2) i0e(rho d / s^2), which cannot overflow
    and only underflows when Pc itself is below double precision."""
    d, s, r = float(miss_km), float(s_km), float(radius_km)

    def integrand(rho):
        return (rho / s**2) * np.exp(-((rho - d) ** 2) / (2 * s**2)) * i0e(rho * d / s**2)

    value, _ = quad(integrand, 0.0, r, epsabs=0.0, epsrel=1e-10, limit=200)
    return min(max(value, 0.0), 1.0)


def _sphere_probability_3d(miss_km, s_km, radius_km):
    """P(|X| < R) for X ~ N(mu, s^2 I3) with |mu| = miss: non-central
    chi-square CDF with 3 degrees of freedom."""
    return float(min(max(ncx2.cdf((radius_km / s_km) ** 2, 3, (miss_km / s_km) ** 2), 0.0), 1.0))


def analytic_collision_probability(pos_a, pos_b, rel_velocity_vec_km_s, sigma_km,
                                   hard_body_radius_km=PC_HARD_BODY_RADIUS_KM):
    """Deterministic encounter-plane Pc indicator under the simplified
    isotropic uncertainty model (see PC_METHOD). Returns (pc, details).

    Short-encounter assumption: during the encounter the relative motion is
    a straight line along v_rel, so the uncertainty along v_rel integrates
    out and only the components in the plane perpendicular to v_rel matter."""
    rel_pos = np.asarray(pos_a, dtype=float) - np.asarray(pos_b, dtype=float)
    s_km = np.sqrt(2.0) * sigma_km  # relative sigma per axis (two independent objects)
    v = None if rel_velocity_vec_km_s is None else np.asarray(rel_velocity_vec_km_s, dtype=float)

    if v is None or np.linalg.norm(v) < ENCOUNTER_PLANE_MIN_REL_SPEED_KM_S:
        miss = float(np.linalg.norm(rel_pos))
        pc = _sphere_probability_3d(miss, s_km, hard_body_radius_km)
        return pc, {"method": PC_METHOD_3D_FALLBACK, "geometry": "3d_fallback",
                    "encounter_plane_miss_km": None, "relative_sigma_km": float(s_km)}

    v_hat = v / np.linalg.norm(v)
    projected = rel_pos - np.dot(rel_pos, v_hat) * v_hat
    miss_2d = float(np.linalg.norm(projected))
    pc = _circle_probability_2d(miss_2d, s_km, hard_body_radius_km)
    return pc, {"method": PC_METHOD, "geometry": "encounter_plane",
                "encounter_plane_miss_km": miss_2d, "relative_sigma_km": float(s_km)}


def score_collision_event(event):
    """Compute the Pc indicator and risk tier for a collision_risk event dict
    (as returned by conjunction.find_close_approaches). Evaluated at the
    refined TCA state (event['pos_a_tca_km'] / ['pos_b_tca_km'] and
    ['rel_velocity_vec_km_s']) -- the same instant as the reported TCA, miss
    distance and relative velocity. Deterministic: identical inputs always
    give an identical Pc. Mutates nothing; returns a new dict."""
    pos_a = event.get("pos_a_tca_km")
    pos_b = event.get("pos_b_tca_km")
    if pos_a is None or pos_b is None:
        raise ValueError(
            "collision event is missing the refined TCA state (pos_a_tca_km/pos_b_tca_km); "
            "Pc must be evaluated at the same instant as the reported TCA"
        )

    tca = datetime.fromisoformat(event["tca_timestamp"])
    now = datetime.now(timezone.utc)
    hours_until_tca = max((tca - now).total_seconds() / 3600.0, 0.0)
    sigma_km = sigma_km_for(hours_until_tca)

    pc, details = analytic_collision_probability(
        pos_a, pos_b, event.get("rel_velocity_vec_km_s"), sigma_km,
        hard_body_radius_km=PC_HARD_BODY_RADIUS_KM,
    )
    return {
        "pc_score": pc,
        "risk_tier": risk_tier_from_pc(pc),
        "sigma_km": sigma_km,
        "hard_body_radius_km": PC_HARD_BODY_RADIUS_KM,
        "method": details["method"],
        "geometry": details["geometry"],
        "encounter_plane_miss_km": details["encounter_plane_miss_km"],
        "relative_sigma_km": details["relative_sigma_km"],
    }
