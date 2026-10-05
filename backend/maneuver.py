"""Illustrative delta-v estimate (blueprint Section 6.6).

IMPORTANT: this module does NOT produce a maneuver recommendation or plan.
The figure it returns is an order-of-magnitude illustration only: a fixed
separation increase (1 km) spread evenly over a fixed lead time (6 h). It is
independent of orbital geometry, relative velocity, burn location, spacecraft
mass, thrust, fuel and execution constraints. Any maneuver decision rests
with the human operator using proper flight-dynamics tools.
"""

ESTIMATE_LABEL = "Illustrative Δv estimate"
ESTIMATE_LABEL_ASCII = "Illustrative delta-v estimate"

NO_ESTIMATE_LABEL = "No Δv estimate (proximity watch)"
NO_MANEUVER_TEXT = (
    "No delta-v estimate — proximity watch: continue enhanced tracking and analyst review."
)

ESTIMATE_CAVEAT = (
    "Not a maneuver plan — does not model orbital geometry, relative velocity, "
    "burn location, spacecraft mass, thrust, fuel, or execution constraints."
)


def estimate_delta_v(rel_velocity_km_s, desired_miss_increase_km=1.0, lead_time_hours=6.0):
    """Illustrative delta-v estimate: spread the desired miss-distance increase
    evenly over the lead time (delta_v = desired_miss_increase / lead_time).

    Deliberately independent of rel_velocity_km_s and approach geometry; the
    argument is accepted only for interface stability. This is NOT a maneuver
    recommendation. A real maneuver-planning system would solve this via
    covariance-aware B-plane targeting with spacecraft and propulsion models
    (documented in README Limitations)."""
    delta_v_m_s = (desired_miss_increase_km / (lead_time_hours * 3600)) * 1000
    return delta_v_m_s


def collision_estimate_text(delta_v_m_s, lead_time_hours=6.0, desired_miss_increase_km=1.0):
    """Single source of truth for the collision-event delta-v wording (also
    used by brief_agent for LLM and fallback briefs)."""
    if delta_v_m_s is None:
        return f"{ESTIMATE_LABEL_ASCII}: not available. {ESTIMATE_CAVEAT}"
    return (
        f"{ESTIMATE_LABEL_ASCII}: ~{delta_v_m_s:.2f} m/s (simplified: "
        f"{desired_miss_increase_km:g} km separation spread over a {lead_time_hours:g} h lead time). "
        f"{ESTIMATE_CAVEAT}"
    )


def maneuver_for_event(event_class, rel_velocity_km_s, lead_time_hours=6.0):
    """Return {'maneuver_text', 'delta_v_m_s', 'label'}.

    collision_risk events get an *illustrative* delta-v estimate (not a
    maneuver plan); proximity_watch events get the fixed no-estimate text with
    delta_v_m_s = None."""
    if event_class != "collision_risk":
        return {"maneuver_text": NO_MANEUVER_TEXT, "delta_v_m_s": None, "label": NO_ESTIMATE_LABEL}

    delta_v = estimate_delta_v(rel_velocity_km_s, lead_time_hours=lead_time_hours)
    return {
        "maneuver_text": collision_estimate_text(delta_v, lead_time_hours=lead_time_hours),
        "delta_v_m_s": delta_v,
        "label": ESTIMATE_LABEL,
    }


if __name__ == "__main__":
    print(maneuver_for_event("collision_risk", rel_velocity_km_s=14.2, lead_time_hours=6.0))
    print(maneuver_for_event("proximity_watch", rel_velocity_km_s=None))
