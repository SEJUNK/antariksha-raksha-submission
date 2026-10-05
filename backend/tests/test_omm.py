"""OMM readiness (backend/orbital_formats.py + ingest format flag).

Uses a small local fixture of the same objects in both CelesTrak formats
(FORMAT=TLE and FORMAT=json / CCSDS OMM) -- no network.
"""

import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest
from sgp4 import omm
from sgp4.api import Satrec
from sgp4.conveniences import sat_epoch_datetime

from backend.orbital_formats import omm_to_tle_lines, parse_omm_json, parse_tle_text
from backend.propagate import _TS, propagate_object, satellite_for

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TLE_TEXT = (FIXTURES / "celestrak_sample.tle").read_text(encoding="utf-8")
OMM_TEXT = (FIXTURES / "celestrak_sample_omm.json").read_text(encoding="utf-8")


def _by_id(records):
    return {r["norad_id"]: r for r in records}


def _epochs_around(tle_line1, tle_line2):
    """A few instants around the element-set epoch (epoch, +6 h, +1 d, +3 d)."""
    epoch = sat_epoch_datetime(Satrec.twoline2rv(tle_line1, tle_line2))
    return [epoch + timedelta(hours=h) for h in (0, 6, 24, 72)]


def test_omm_and_tle_give_same_objects_and_names():
    tle = _by_id(parse_tle_text(TLE_TEXT))
    omm_recs = _by_id(parse_omm_json(OMM_TEXT))
    assert set(tle) == {"44804", "25544"}
    # OMM is carried natively, so the 6-digit catalog number is kept too.
    assert set(omm_recs) == set(tle) | {"270000"}
    for nid in tle:
        assert omm_recs[nid]["name"] == tle[nid]["name"]
        assert omm_recs[nid]["source_format"] == "omm"
        assert tle[nid]["source_format"] == "tle"


def test_omm_records_carry_no_generated_tle():
    for rec in parse_omm_json(OMM_TEXT):
        assert rec["tle_line1"] is None and rec["tle_line2"] is None
        assert rec["omm"]["NORAD_CAT_ID"] == int(rec["norad_id"])


def test_generated_tle_fields_match_original_tle():
    """omm_to_tle_lines (utility; not used by ingest): element columns of the
    generated lines equal the CelesTrak TLE (checksum and the sign of a zero
    nddot exponent are cosmetic)."""
    tle = _by_id(parse_tle_text(TLE_TEXT))
    omm_by_id = {f"{r['NORAD_CAT_ID']:05d}": r for r in json.loads(OMM_TEXT)}
    for nid, t in tle.items():
        l1, l2 = omm_to_tle_lines(omm_by_id[nid])
        g = {"tle_line1": l1, "tle_line2": l2}
        assert g["tle_line2"][:68] == t["tle_line2"][:68]
        assert g["tle_line1"][:44] == t["tle_line1"][:44]   # id, class, intl desig, epoch, ndot
        assert g["tle_line1"][53:68] == t["tle_line1"][53:68]  # bstar, eph type, element set


def test_omm_and_tle_propagate_to_same_positions():
    tle = _by_id(parse_tle_text(TLE_TEXT))
    omm_recs = _by_id(parse_omm_json(OMM_TEXT))
    for nid, t in tle.items():
        times = _epochs_around(t["tle_line1"], t["tle_line2"])
        p_tle = propagate_object(t["tle_line1"], t["tle_line2"], times)
        sat = satellite_for(omm_recs[nid])
        p_omm = sat.at(_TS.from_datetimes(times)).position.km.T
        assert np.allclose(p_tle, p_omm, atol=1e-5), nid  # km


def test_generated_tle_matches_native_sgp4_omm_satrec():
    """Going OMM -> TLE lines loses only TLE field quantisation: SGP4 on the
    generated lines matches SGP4 initialised directly from the OMM record to
    within 1 cm (the TLE epoch field resolves 1e-8 day = 0.864 ms, i.e. a few
    mm along-track at LEO speed)."""
    for rec in json.loads(OMM_TEXT)[:2]:
        native = Satrec()
        omm.initialize(native, rec)
        l1, l2 = omm_to_tle_lines(rec)
        generated = Satrec.twoline2rv(l1, l2)
        for minutes in (0.0, 360.0, 1440.0, 4320.0):
            jd, fr = native.jdsatepoch, native.jdsatepochF + minutes / 1440.0
            e1, r1, _ = native.sgp4(jd, fr)
            e2, r2, _ = generated.sgp4(jd, fr)
            assert e1 == e2 == 0
            assert np.allclose(r1, r2, atol=1e-5)  # km


def test_six_digit_norad_id_rejected_with_clear_error():
    rec = json.loads(OMM_TEXT)[2]
    with pytest.raises(ValueError, match="5-digit"):
        omm_to_tle_lines(rec)


def test_small_norad_id_keyed_like_tle_path():
    rec = dict(json.loads(OMM_TEXT)[0], NORAD_CAT_ID=5)
    [norm] = parse_omm_json(json.dumps([rec]))
    assert norm["norad_id"] == "00005"
    assert omm_to_tle_lines(rec)[0][2:7] == "00005"


def test_missing_required_field_skipped():
    rec = dict(json.loads(OMM_TEXT)[0])
    del rec["MEAN_MOTION"]
    assert parse_omm_json(json.dumps([rec])) == []
    with pytest.raises(ValueError, match="MEAN_MOTION"):
        omm_to_tle_lines(rec)


def test_epoch_without_fraction_and_z_suffix_accepted():
    rec = dict(json.loads(OMM_TEXT)[1], EPOCH="2024-01-01T12:00:00Z")
    l1, _ = omm_to_tle_lines(rec)
    assert l1[18:32] == "24001.50000000"


# --- ingest format flag -----------------------------------------------------

class _Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


def _patch_network(monkeypatch, tmp_path, fmt, working_set):
    from backend import ingest

    ws_path = tmp_path / "ws.json"
    ws_path.write_text(json.dumps(working_set), encoding="utf-8")
    monkeypatch.setattr(ingest, "WORKING_SET_PATH", ws_path)
    monkeypatch.setattr(ingest, "TLE_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(ingest, "CELESTRAK_FORMAT", fmt)
    urls = []

    def fake_get(url, timeout=None):
        urls.append(url)
        return _Resp(OMM_TEXT if "FORMAT=json" in url else TLE_TEXT)

    monkeypatch.setattr(ingest.requests, "get", fake_get)
    return urls


_WS = {
    # The fake network returns the whole fixture for every NAME query, so
    # each entry needs exact_match to resolve unambiguously.
    "group_a": [{"name_query": "CARTOSAT-3", "exact_match": "CARTOSAT-3", "criticality": "Tier2"}],
    "group_b_debris_groups": [],
    "group_c": [{"name_query": "ISS", "exact_match": "ISS (ZARYA)"}],
}


def test_default_format_is_tle():
    from backend import config

    assert config.CELESTRAK_FORMAT in ("tle", "omm")
    if "ANTARIKSHA_CELESTRAK_FORMAT" not in __import__("os").environ:
        assert config.CELESTRAK_FORMAT == "tle"


@pytest.mark.parametrize("fmt,query,suffix", [("tle", "FORMAT=TLE", ".tle"), ("omm", "FORMAT=json", ".json")])
def test_fetch_tles_uses_selected_format(monkeypatch, tmp_path, fmt, query, suffix):
    from backend import ingest

    urls = _patch_network(monkeypatch, tmp_path, fmt, _WS)
    objs = {o["norad_id"]: o for o in ingest.fetch_tles()}
    assert urls and all(query in u for u in urls)
    assert set(objs) == {"44804", "25544"}
    assert objs["44804"]["group"] == "A" and objs["25544"]["group"] == "C"
    assert all(o["source_format"] == fmt for o in objs.values())
    assert all(p.suffix == suffix for p in (tmp_path / "cache").iterdir())


def test_unknown_format_raises_before_network(monkeypatch, tmp_path):
    from backend import ingest

    urls = _patch_network(monkeypatch, tmp_path, "xml", _WS)
    with pytest.raises(ValueError, match="ANTARIKSHA_CELESTRAK_FORMAT"):
        ingest.fetch_tles()
    assert urls == []
