"""Named-object resolution (never arbitrary) and ingest safety rules:
unresolved entries keep their previous object active; too few Group A
resolutions or a mass deactivation refuse the ingest without touching the
catalog. Local data only -- no network."""

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sgp4.api import WGS72, Satrec, jday
from sgp4.exporter import export_tle

from backend.ingest import resolve_matches

REPO = Path(__file__).resolve().parents[2]


def _rec(name, norad="10000"):
    return {"norad_id": norad, "name": name, "source_format": "tle", "tle_line1": "l1", "tle_line2": "l2"}


# --- resolution rule ---------------------------------------------------------------

def test_zero_matches_unresolved_with_reason():
    obj, reason = resolve_matches({"name_query": "GONE-1"}, [])
    assert obj is None and "no object matched" in reason and "GONE-1" in reason


def test_single_match_resolved():
    obj, reason = resolve_matches({"name_query": "SAT-1"}, [_rec("SAT-1 (ALT NAME)")])
    assert obj["name"] == "SAT-1 (ALT NAME)" and reason is None


def test_multiple_matches_without_exact_match_are_ambiguous_not_first():
    parsed = [_rec("SAT-7", "1"), _rec("SAT-7A", "2"), _rec("SAT-7R", "3")]
    obj, reason = resolve_matches({"name_query": "SAT-7"}, parsed)
    assert obj is None and reason.startswith("ambiguous: 3 objects") and "exact_match" in reason


def test_exact_match_selects_exactly_one():
    parsed = [_rec("SAT-7A", "2"), _rec("SAT-7", "1"), _rec("SAT-7R", "3")]
    obj, _ = resolve_matches({"name_query": "SAT-7", "exact_match": "SAT-7"}, parsed)
    assert obj["norad_id"] == "1"


def test_exact_match_without_hit_has_no_fallback():
    parsed = [_rec("SAT-7A", "2"), _rec("SAT-7R", "3")]
    obj, reason = resolve_matches({"name_query": "SAT-7", "exact_match": "SAT-7"}, parsed)
    assert obj is None and "matched 0 of 2" in reason


def test_exact_match_with_duplicate_names_unresolved():
    parsed = [_rec("SAT-7", "1"), _rec("SAT-7", "9")]
    obj, reason = resolve_matches({"name_query": "SAT-7", "exact_match": "SAT-7"}, parsed)
    assert obj is None and "matched 2 of 2" in reason


def test_working_set_resolves_every_entry_from_local_cache(monkeypatch):
    """Every Group A/C entry in data/working_set.json resolves to exactly one
    object from the offline cache (skipped where the cache is absent)."""
    from backend import ingest
    from backend.config import TLE_CACHE_DIR

    ws = json.loads((REPO / "data" / "working_set.json").read_text(encoding="utf-8"))
    checked = 0
    for prefix, entries in (("groupA", ws["group_a"]), ("groupC", ws["group_c"])):
        for entry in entries:
            path = ingest._cache_path(f"{prefix}_{entry['name_query']}")
            if not path.exists():
                continue
            parsed = ingest._parse_catalog_text(path.read_text(encoding="utf-8"), "tle")
            obj, reason = resolve_matches(entry, parsed)
            assert obj is not None, reason
            assert obj["name"] == entry.get("exact_match", obj["name"])
            checked += 1
    if not checked:
        pytest.skip(f"no local TLE cache at {TLE_CACHE_DIR}")


# --- run_ingest safety ---------------------------------------------------------------

@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.db.DB_PATH", tmp_path / "resolution_test.db")
    from backend.db import init_db
    init_db()


def _tle_lines(satnum):
    now = datetime.now(timezone.utc)
    jd, fr = jday(now.year, now.month, now.day, now.hour, now.minute, now.second)
    sat = Satrec()
    sat.sgp4init(WGS72, "i", satnum, (jd + fr) - 2433281.5, 0.0001, 0.0, 0.0, 0.001, math.radians(20),
                 math.radians(51.6), math.radians(satnum % 360), 15.5 * 2 * math.pi / 1440.0, math.radians(10))
    return export_tle(sat)


def _obj(norad, group, name):
    l1, l2 = _tle_lines(int(norad))
    types = {"A": ("satellite", "India", "Tier1"), "B": ("debris", None, None), "C": ("foreign_sat", "foreign", None)}
    t, owner, crit = types[group]
    return {"norad_id": norad, "name": name, "tle_line1": l1, "tle_line2": l2, "source_format": "tle",
            "object_type": t, "owner_country": owner, "criticality": crit, "group": group}


ASSETS = {f"A{i}": _obj(str(91000 + i), "A", f"A{i}") for i in range(4)}
DEBRIS = [_obj(str(92000 + i), "B", f"DEB-{i}") for i in range(4)]


def _fake_fetch(monkeypatch, resolved_names, debris=DEBRIS, total_a=4):
    """Patch ingest.fetch_tles: Group A entries A0..A{total_a-1}; only
    `resolved_names` resolve this time."""
    from backend import ingest

    def fake():
        objs, resolution, unresolved = [], {}, []
        for i in range(total_a):
            name = f"A{i}"
            if name in resolved_names:
                objs.append(dict(ASSETS[name]))
                resolution[f"A:{name}"] = ASSETS[name]["norad_id"]
            else:
                unresolved.append({"group": "A", "name_query": name, "reason": f"ambiguous: 2 objects matched '{name}'"})
        objs.extend(dict(d) for d in debris)
        ingest.fetch_tles.using_cache = False
        ingest.fetch_tles.source_format = "tle"
        ingest.fetch_tles.resolution = resolution
        ingest.fetch_tles.unresolved = unresolved
        ingest.fetch_tles.rejected = []
        ingest.fetch_tles.group_a_total = total_a
        ingest.fetch_tles.group_a_resolved = sum(1 for i in range(total_a) if f"A{i}" in resolved_names)
        return objs

    monkeypatch.setattr(ingest, "fetch_tles", fake)
    return ingest


def _active():
    from backend.db import list_objects
    return {o["norad_id"] for o in list_objects()}


def test_unresolved_entry_keeps_previous_object_active_and_is_recorded(db, monkeypatch):
    from backend.db import latest_ingest_run, list_objects

    ingest = _fake_fetch(monkeypatch, {"A0", "A1", "A2", "A3"})
    ingest.run_ingest()
    before = {o["norad_id"]: o["tle_line1"] for o in list_objects()}

    ingest = _fake_fetch(monkeypatch, {"A0", "A1", "A2"})  # A3 now ambiguous
    ingest.run_ingest()
    assert ASSETS["A3"]["norad_id"] in _active()  # not deactivated
    assert {o["norad_id"]: o["tle_line1"] for o in list_objects()} == before  # previous elements kept
    run = latest_ingest_run()
    assert run["status"] == "ok" and run["resolved_count"] == 3 and run["unresolved_count"] == 1
    [u] = json.loads(run["unresolved_json"])
    assert u["name_query"] == "A3" and u["kept_norad_id"] == ASSETS["A3"]["norad_id"]
    assert "ambiguous" in u["reason"]
    # The kept mapping carries forward, so a second unresolved run still keeps it.
    assert json.loads(run["resolution_json"])["A:A3"] == ASSETS["A3"]["norad_id"]
    ingest.run_ingest()
    assert ASSETS["A3"]["norad_id"] in _active()


def test_status_payload_surfaces_unresolved_entries(db, monkeypatch):
    from backend.provenance import get_live_data_status

    _fake_fetch(monkeypatch, {"A0", "A1", "A2", "A3"}).run_ingest()
    _fake_fetch(monkeypatch, {"A0", "A1", "A2"}).run_ingest()
    status = get_live_data_status()
    assert [u["name_query"] for u in status["ingest"]["unresolved"]] == ["A3"]
    assert status["ingest"]["last_failure"] is None
    assert any("did not resolve" in w for w in status["warnings"])


def test_too_few_group_a_resolved_refuses_ingest(db, monkeypatch):
    from backend.db import latest_failed_ingest_run, latest_ingest_run
    from backend.ingest import IngestSafetyError
    from backend.provenance import get_live_data_status

    ingest = _fake_fetch(monkeypatch, {"A0", "A1", "A2", "A3"})
    ingest.run_ingest()
    ok_run = latest_ingest_run()
    active_before = _active()

    ingest = _fake_fetch(monkeypatch, {"A0"}, debris=DEBRIS[:1])  # 1/4 < 50%
    with pytest.raises(IngestSafetyError, match="Only 1 of 4") as exc:
        ingest.run_ingest()
    assert exc.value.reason == "group_a_unresolved"
    assert _active() == active_before
    assert latest_ingest_run()["id"] == ok_run["id"]
    failed = latest_failed_ingest_run()
    assert failed["id"] == exc.value.ingest_run_id and failed["status"] == "failed"
    assert failed["unresolved_count"] == 3
    status = get_live_data_status()
    assert status["ingest"]["last_failure"]["reason"].startswith("Only 1 of 4")
    assert any("refused" in w for w in status["warnings"])

    # A later successful ingest clears the failure from the status payload.
    _fake_fetch(monkeypatch, {"A0", "A1", "A2", "A3"}).run_ingest()
    assert latest_failed_ingest_run() is None


def test_half_of_group_a_resolved_is_accepted(db, monkeypatch):
    ingest = _fake_fetch(monkeypatch, {"A0", "A1", "A2", "A3"})
    ingest.run_ingest()
    _fake_fetch(monkeypatch, {"A0", "A1"}).run_ingest()  # 2/4 == default 0.5 floor
    assert {a["norad_id"] for a in ASSETS.values()} <= _active()


def test_mass_deactivation_refused_and_rolled_back(db, monkeypatch):
    from backend.db import latest_failed_ingest_run, list_objects
    from backend.ingest import IngestSafetyError

    many = [_obj(str(93000 + i), "B", f"D{i}") for i in range(10)]
    _fake_fetch(monkeypatch, {"A0", "A1", "A2", "A3"}, debris=many).run_ingest()
    before = {o["norad_id"]: (o["active"], o["last_updated"]) for o in list_objects(include_inactive=True)}

    ingest = _fake_fetch(monkeypatch, {"A0", "A1", "A2", "A3"}, debris=many[:2])  # 8 of 14 dropped
    with pytest.raises(IngestSafetyError, match="deactivate 8 of 14") as exc:
        ingest.run_ingest()
    assert exc.value.reason == "mass_deactivation"
    after = {o["norad_id"]: (o["active"], o["last_updated"]) for o in list_objects(include_inactive=True)}
    assert after == before  # upserts rolled back too
    assert latest_failed_ingest_run()["failure_reason"].startswith("Ingest would deactivate")


def test_thresholds_are_configurable(db, monkeypatch):
    from backend import ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "INGEST_MIN_GROUP_A_RESOLVED_FRACTION", 0.0)
    monkeypatch.setattr(ingest_mod, "INGEST_MAX_DEACTIVATE_FRACTION", 1.0)
    many = [_obj(str(93000 + i), "B", f"D{i}") for i in range(10)]
    _fake_fetch(monkeypatch, {"A0", "A1", "A2", "A3"}, debris=many).run_ingest()
    _fake_fetch(monkeypatch, set(), debris=many[:1]).run_ingest()  # accepted under relaxed limits


def test_refresh_endpoint_returns_409_when_ingest_refused(monkeypatch):
    """/api/refresh surfaces an ingest safety refusal as structured JSON."""
    from backend import main
    from backend.ingest import IngestSafetyError
    from backend.tests._asgi import request

    def refuse():
        raise IngestSafetyError("Only 3 of 11 Group A entries resolved.", "group_a_unresolved", 7)

    monkeypatch.setattr(main, "run_full_pipeline", refuse)
    monkeypatch.setattr("backend.config.API_KEY", "")
    status, _, body = request(main.app, "POST", "/api/refresh")
    assert status == 409
    assert body["error"] == "ingest_refused" and body["reason"] == "group_a_unresolved"
    assert body["ingest_run_id"] == 7 and "Group A" in body["detail"]


# --- fetch validation before caching (no cache poisoning) ------------------------

class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _good_tle_text():
    l1, l2 = _fresh_tle_pair(90123)
    return f"GOOD-OBJ\n{l1}\n{l2}\n"


def _fresh_tle_pair(satnum):
    now = datetime.now(timezone.utc)
    jd, fr = jday(now.year, now.month, now.day, now.hour, now.minute, now.second)
    sat = Satrec()
    sat.sgp4init(WGS72, "i", satnum, (jd + fr) - 2433281.5, 0.0001, 0.0, 0.0, 0.001,
                 math.radians(20), math.radians(51.6), math.radians(30), 15.5 * 2 * math.pi / 1440.0,
                 math.radians(10))
    return export_tle(sat)


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    from backend import ingest
    d = tmp_path / "tle_cache"
    monkeypatch.setattr(ingest, "TLE_CACHE_DIR", d)
    return d


@pytest.mark.parametrize("body,suffix,fmt", [
    ("", ".tle", "tle"),                                   # HTTP 200, empty body
    ("   \n", ".tle", "tle"),                              # whitespace only
    ("<html><body>rate limited</body></html>", ".tle", "tle"),  # garbage, 0 TLE records
    ("[]", ".tle", "tle"),
    ("[]", ".json", "omm"),                                # FORMAT=json "no data"
    ("<html>oops</html>", ".json", "omm"),                 # not JSON at all
    ('[{"OBJECT_NAME": "BAD", "NORAD_CAT_ID": 5}]', ".json", "omm"),  # only invalid records
])
def test_bad_200_response_falls_back_to_cache_and_never_overwrites_it(cache_dir, monkeypatch, body, suffix, fmt):
    from backend import ingest

    if fmt == "omm":
        good = json.dumps([{"OBJECT_NAME": "GOOD", "NORAD_CAT_ID": 90123, "EPOCH": "2026-10-01T00:00:00",
                            "MEAN_MOTION": 15.5, "ECCENTRICITY": 0.001, "INCLINATION": 51.6,
                            "RA_OF_ASC_NODE": 10, "ARG_OF_PERICENTER": 20, "MEAN_ANOMALY": 30,
                            "BSTAR": 0.0001, "MEAN_MOTION_DOT": 0.0}])
    else:
        good = _good_tle_text()
    cache_file = ingest._cache_path("groupB_test", suffix)
    cache_file.write_text(good, encoding="utf-8")
    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: _Resp(body))

    text, used_cache = ingest._fetch_with_cache("http://x", "groupB_test", suffix, fmt)
    assert used_cache is True and text == good
    assert cache_file.read_text(encoding="utf-8") == good  # never overwritten


def test_bad_200_response_without_cache_is_a_failed_fetch(cache_dir, monkeypatch):
    from backend import ingest

    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: _Resp(""))
    with pytest.raises(RuntimeError, match="no cache"):
        ingest._fetch_with_cache("http://x", "groupB_none", ".tle", "tle")
    assert not ingest._cache_path("groupB_none", ".tle").exists()


def test_http_5xx_uses_cache_and_good_response_refreshes_it(cache_dir, monkeypatch):
    from backend import ingest

    old, new = _good_tle_text(), _good_tle_text().replace("GOOD-OBJ", "NEW-OBJ")
    cache_file = ingest._cache_path("groupB_t2", ".tle")
    cache_file.write_text(old, encoding="utf-8")
    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: _Resp("x", 503))
    assert ingest._fetch_with_cache("http://x", "groupB_t2", ".tle", "tle") == (old, True)
    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: _Resp(new))
    assert ingest._fetch_with_cache("http://x", "groupB_t2", ".tle", "tle") == (new, False)
    assert cache_file.read_text(encoding="utf-8") == new


def test_refresh_returns_clean_502_when_ingest_fails_and_catalog_is_untouched(tmp_path, monkeypatch, caplog):
    """A fetch/parse failure (no network and no cache, malformed data, ...)
    is a structured 502 without paths or tracebacks; nothing is changed."""
    from backend import ingest, main
    from backend.db import init_db, latest_ingest_run, list_objects, upsert_object
    from backend.tests._asgi import request

    monkeypatch.setattr("backend.db.DB_PATH", tmp_path / "refresh_fail.db")
    monkeypatch.setattr("backend.config.API_KEY", "")
    init_db()
    l1, l2 = _fresh_tle_pair(90200)
    upsert_object("90200", "KEEP-ME", "satellite", "India", "Tier1", l1, l2)
    before = list_objects(include_inactive=True)

    def failing_fetch():
        raise RuntimeError("Could not fetch 'groupA_X' and no cache exists at /home/secret/tle_cache/x.tle")

    monkeypatch.setattr(ingest, "fetch_tles", failing_fetch)
    with caplog.at_level("ERROR"):
        status, headers, body = request(main.app, "POST", "/api/refresh")
    assert status == 502
    assert body["error"] == "ingest_failed"
    assert "left unchanged" in body["detail"]
    assert "secret" not in body["detail"] and "Traceback" not in body["detail"]
    assert list_objects(include_inactive=True) == before
    assert latest_ingest_run() is None
    assert any(r.exc_info for r in caplog.records)  # full traceback kept server-side


def test_refresh_unexpected_screening_error_is_generic_json_500(tmp_path, monkeypatch):
    from backend import main

    def boom():
        raise KeyError("/home/secret/internal")
    monkeypatch.setattr(main, "run_full_pipeline", boom)
    monkeypatch.setattr("backend.config.API_KEY", "")
    from backend.tests._asgi import request
    status, _, body = request(main.app, "POST", "/api/refresh")
    assert status == 500 and body["error"] == "internal_error" and "secret" not in body["detail"]
