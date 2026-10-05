"""CLI wrapper for the demo-reliability seed (blueprint Section 10).
Core logic lives in backend/demo_seed.py so the /api/demo/seed endpoint can
reuse it for live-recorded demos.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.demo_seed import seed_event  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", default="CARTOSAT", help="Name substring of the Indian protected asset to target")
    parser.add_argument("--debris", default="FENGYUN", help="Name substring of the debris object to perturb")
    parser.add_argument("--foreign", default="SENTINEL", help="Name substring of the foreign satellite to perturb in --proximity mode")
    parser.add_argument("--proximity", action="store_true", help="Seed a proximity_watch event instead of a collision_risk event")
    parser.add_argument("--separation-km", type=float, help="Collision: designed miss distance at TCA in km (default 0.02). Proximity: along-track separation in km (default 10.0)")
    args = parser.parse_args()

    info = seed_event(
        asset_hint=args.asset,
        proximity=args.proximity,
        separation_km=args.separation_km,
        debris_hint=args.debris,
        foreign_hint=args.foreign,
    )

    print(f"=== SIMULATION ADJUSTMENT ({info['kind']} demo seed) ===")
    print(f"Protected asset : {info['asset']['name']} ({info['asset']['norad_id']})")
    print(f"Adjusted object : {info['adjusted_object']['name']} ({info['adjusted_object']['norad_id']})")
    if info["geometry"] == "crossing":
        print(f"Change made     : crossing orbit through the asset's predicted position "
              f"{info['encounter_lead_hours']:.2f} h ahead; plane tilted {info['plane_offset_deg']} deg about the "
              f"encounter point, semi-major axis +{info['sma_offset_km']} km.")
        print(f"Designed TCA    : {info['design_tca']}  miss {info['design_miss_distance_km'] * 1000:.1f} m  "
              f"relative velocity {info['design_rel_velocity_km_s']:.3f} km/s")
        print("Pc / risk tier  : computed by the unchanged screening and Monte Carlo model (not injected).")
    else:
        print(f"Change made     : orbit aligned to asset (co-orbital); mean anomaly offset "
              f"{info['mean_anomaly_offset_deg']} deg (~{info['separation_km']:.2f} km along-track).")
    print("This is a disclosed, logged simulation adjustment for demo purposes only -- "
          "narrate it openly in any demo recording.")
    print("Stored as a demo override; the real catalog (objects table) is unchanged. "
          "The next normal screening (/api/refresh) clears it.")
    print(f"New TLE line 1  : {info['new_tle'][0]}")
    print(f"New TLE line 2  : {info['new_tle'][1]}")
    print(f"Screening complete: {info['collision_events']} collision_risk, {info['proximity_events']} proximity_watch event(s).")
