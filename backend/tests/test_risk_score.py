from datetime import datetime, timedelta, timezone
import numpy as np
import pytest
from scipy.stats import ncx2

from backend.config import (
    PC_HARD_BODY_RADIUS_KM,
    PC_N_SAMPLES,
    RISK_TIER_CRITICAL_PC,
    RISK_TIER_HIGH_PC,
    RISK_TIER_MEDIUM_PC,
)
from backend.risk_score import (
    PC_METHOD,
    PC_METHOD_3D_FALLBACK,
    _circle_probability_2d,
    analytic_collision_probability,
    compute_collision_probability,
    priority_score,
    risk_tier_from_pc,
    score_collision_event,
    sigma_km_for,
)

RNG = np.random.default_rng(42)
R = PC_HARD_BODY_RADIUS_KM


# --- Monte Carlo reference (validation only, not production scoring) ----------

def test_monte_carlo_reference_huge_miss_gives_near_zero_pc():
    pos_a = np.array([0.0, 0.0, 7000.0])
    pos_b = np.array([0.0, 0.0, 7000.0 + 50.0])  # 50 km separation
    pc = compute_collision_probability(pos_a, pos_b, rel_velocity_km_s=7.5, sigma_km=0.1, rng=RNG)
    assert pc < 1e-6


def test_monte_carlo_reference_near_zero_miss_gives_measurable_pc():
    pos_a = np.array([0.0, 0.0, 7000.0])
    pc = compute_collision_probability(
        pos_a, pos_a, rel_velocity_km_s=7.5, sigma_km=0.05, n_samples=20000, rng=RNG,
    )
    assert pc > 0.0


def test_tier1_outranks_tier3_at_equal_pc():
    assert priority_score(5e-5, "Tier1") > priority_score(5e-5, "Tier3")


def test_risk_tier_thresholds():
    assert risk_tier_from_pc(5e-4) == "Critical"
    assert risk_tier_from_pc(5e-5) == "High"
    assert risk_tier_from_pc(5e-6) == "Medium"
    assert risk_tier_from_pc(5e-7) == "Low"


# --- Analytic encounter-plane indicator ---------------------------------------

V_ALONG_Y = [0.0, 7.5, 0.0]  # relative velocity: encounter plane is x-z


def _event(pos_a, pos_b, hours_ahead=0.0, rel_v_vec=V_ALONG_Y):
    tca = datetime.now(timezone.utc) + timedelta(hours=hours_ahead)
    return {
        "object_a_id": "10001", "object_b_id": "30003",
        "tca_timestamp": tca.isoformat(),
        "miss_distance_km": float(np.linalg.norm(np.subtract(pos_a, pos_b))),
        "rel_velocity_km_s": float(np.linalg.norm(rel_v_vec)) if rel_v_vec is not None else None,
        "rel_velocity_vec_km_s": rel_v_vec,
        "pos_a_tca_km": list(pos_a), "pos_b_tca_km": list(pos_b),
    }


def _event_at_miss(miss_km, **kw):
    # Miss vector along x, perpendicular to the relative velocity (y).
    return _event([7000.0 + miss_km, 0.0, 0.0], [7000.0, 0.0, 0.0], **kw)


def test_analytic_formula_matches_noncentral_chi_square():
    """P(|X| < R), X ~ N(mu, s^2 I2) is the non-central chi-square CDF with
    2 dof -- an independent closed form for the Rician integral."""
    for miss, s in [(0.0, 0.1414), (0.0198, 0.1605), (0.3, 0.2), (0.46, 0.1414), (1.0, 0.5)]:
        expected = ncx2.cdf((R / s) ** 2, 2, (miss / s) ** 2)
        assert _circle_probability_2d(miss, s, R) == pytest.approx(expected, rel=1e-8)


def test_analytic_matches_projected_monte_carlo_reference():
    """Monte Carlo as a validation reference only: sample the 3D relative
    position, project onto the encounter plane, count hits. A large sample
    keeps the statistical error small; the analytic value must agree."""
    sigma = 0.1
    s = np.sqrt(2.0) * sigma
    rel_pos = np.array([0.15, 0.4, 0.05])  # includes an along-velocity component
    v_hat = np.array([0.0, 1.0, 0.0])
    pc, details = analytic_collision_probability(rel_pos, np.zeros(3), V_ALONG_Y, sigma)

    rng = np.random.default_rng(2026)
    n = 2_000_000
    samples = rel_pos + rng.normal(0.0, s, size=(n, 3))
    projected = samples - np.outer(samples @ v_hat, v_hat)
    mc = float(np.mean(np.linalg.norm(projected, axis=1) < R))
    stderr = np.sqrt(mc * (1 - mc) / n)
    assert details["geometry"] == "encounter_plane"
    assert abs(pc - mc) < 4 * stderr


def test_all_risk_bands_are_reachable_with_continuous_nonzero_pc():
    """Under the unchanged thresholds, plausible encounter-plane miss
    distances land in every tier, and every value is a continuous non-zero
    probability (no 1/N quantisation)."""
    bands = {0.0: "Critical", 0.46: "High", 0.55: "Medium", 1.0: "Low"}
    previous = 1.0
    for miss, tier in bands.items():
        scored = score_collision_event(_event_at_miss(miss))
        assert scored["risk_tier"] == tier, (miss, scored["pc_score"])
        assert 0.0 < scored["pc_score"] < previous  # strictly decreasing, never zero
        previous = scored["pc_score"]
    high = score_collision_event(_event_at_miss(0.46))["pc_score"]
    medium = score_collision_event(_event_at_miss(0.55))["pc_score"]
    assert RISK_TIER_HIGH_PC < high <= RISK_TIER_CRITICAL_PC
    assert RISK_TIER_MEDIUM_PC < medium <= RISK_TIER_HIGH_PC


def test_values_between_former_monte_carlo_steps_are_resolved():
    """Before: 5,000-sample Monte Carlo could only report 0 or >= 2e-4, so
    High/Medium were unreachable. After: values below 2e-4 are resolved."""
    pc = score_collision_event(_event_at_miss(0.46))["pc_score"]
    assert 0.0 < pc < 1.0 / PC_N_SAMPLES
    mc = compute_collision_probability(
        [7000.46, 0.0, 0.0], [7000.0, 0.0, 0.0], 7.5,
        sigma_km=sigma_km_for(0.0), rng=np.random.default_rng(7),
    )
    assert mc == 0.0  # the old method reports zero for this High-band geometry


def test_scoring_is_deterministic():
    evt = _event_at_miss(0.3)
    results = {score_collision_event(evt)["pc_score"] for _ in range(5)}
    assert len(results) == 1


def test_component_along_relative_velocity_does_not_change_pc():
    """Only the encounter-plane (perpendicular) miss matters: the along-v
    component is integrated out by the short-encounter assumption."""
    in_plane = score_collision_event(_event([7000.2, 0.0, 0.0], [7000.0, 0.0, 0.0]))
    with_along = score_collision_event(_event([7000.2, 0.7, 0.0], [7000.0, 0.0, 0.0]))
    assert with_along["pc_score"] == pytest.approx(in_plane["pc_score"], rel=1e-12)
    assert with_along["encounter_plane_miss_km"] == pytest.approx(0.2, abs=1e-9)


def test_zero_relative_velocity_uses_3d_fallback():
    evt = _event_at_miss(0.0, rel_v_vec=[0.0, 0.0, 0.0])
    scored = score_collision_event(evt)
    s = np.sqrt(2.0) * scored["sigma_km"]
    assert scored["geometry"] == "3d_fallback"
    assert scored["method"] == PC_METHOD_3D_FALLBACK
    assert scored["pc_score"] == pytest.approx(ncx2.cdf((R / s) ** 2, 3, 0.0), rel=1e-9)


def test_far_miss_gives_tiny_but_nonnegative_pc():
    scored = score_collision_event(_event_at_miss(50.0))
    assert 0.0 <= scored["pc_score"] < 1e-100
    assert scored["risk_tier"] == "Low"


# --- Pc is evaluated at the refined TCA state ----------------------------------

def test_score_missing_tca_state_raises():
    evt = _event_at_miss(0.0)
    del evt["pos_b_tca_km"]
    with pytest.raises(ValueError):
        score_collision_event(evt)


def test_score_reports_method_and_parameters():
    scored = score_collision_event(_event_at_miss(0.1, hours_ahead=48.0))
    assert scored["method"] == PC_METHOD
    assert "analytic encounter-plane" in scored["method"].lower()
    assert scored["hard_body_radius_km"] == PC_HARD_BODY_RADIUS_KM
    assert "n_samples" not in scored and "pc_resolution" not in scored
    # sigma from hours-until-TCA (48 h ahead -> base + 2 days of growth).
    assert abs(scored["sigma_km"] - sigma_km_for(48.0)) < 1e-3
    assert scored["relative_sigma_km"] == pytest.approx(np.sqrt(2.0) * scored["sigma_km"])


def test_pipeline_chain_pc_uses_tca_not_window_midpoint():
    """Screening -> scoring chain on a grid where the objects coincide at
    grid index 3 but are ~100 km apart at the window midpoint. Pc must come
    from the TCA state (Critical-level), which a midpoint-based Pc would miss."""
    from backend.conjunction import find_close_approaches

    n = 20
    start = datetime.now(timezone.utc)
    times = [start + timedelta(seconds=60 * i) for i in range(n)]
    pos_a = np.tile(np.array([7000.0, 0.0, 0.0]), (n, 1))
    pos_b = pos_a + np.array([[0.0, 15.0 * (i - 3), 0.0] for i in range(n)])
    objects = [{"norad_id": "10001", "object_type": "satellite"},
               {"norad_id": "30003", "object_type": "debris"}]
    events = find_close_approaches(times, {"10001": pos_a, "30003": pos_b},
                                   step_seconds=60, objects=objects)
    assert len(events) == 1
    evt = events[0]
    assert np.linalg.norm(pos_a[n // 2] - pos_b[n // 2]) > 90.0
    assert evt["miss_distance_km"] < 1e-6
    # The relative velocity vector at TCA is carried for the encounter plane.
    assert np.linalg.norm(evt["rel_velocity_vec_km_s"]) == pytest.approx(evt["rel_velocity_km_s"])

    scored = score_collision_event(evt)
    assert scored["geometry"] == "encounter_plane"
    assert scored["risk_tier"] == "Critical"
