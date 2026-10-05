"""External orbital-element formats -> normalized orbital records.

ingest.py fetches CelesTrak GP data in one of two external formats and
converts each object into the same normalized record before anything is
stored or screened:

    {"norad_id": str, "name": str, "source_format": "tle" | "omm",
     "tle_line1": str | None, "tle_line2": str | None,   # TLE records only
     "omm": dict | None}                                 # OMM records only

  - TLE (default): the 3-line blocks are passed through unchanged; the
    norad_id is line-1 columns 3-7 (zero-padded, e.g. "00005").
  - OMM (CCSDS Orbit Mean-Elements Message, CelesTrak FORMAT=json): the
    record's mean elements are validated and kept natively (no TLE lines are
    generated). Propagation initialises SGP4 straight from them with sgp4's
    own OMM initializer (sgp4.omm.initialize -> Satrec.sgp4init), so catalog
    numbers beyond the 5-digit TLE field (e.g. 270000) are supported. The
    norad_id is f"{NORAD_CAT_ID:05d}", identical to the TLE-path key for any
    object both formats can express.

satrec_for(obj) is the single source-independent Satrec factory used by
propagate.py and demo_seed.py; it reads TLE lines or the stored OMM JSON
according to obj["source_format"] (rows without one are TLE).
"""

import json
import logging
import math

from sgp4 import exporter, omm
from sgp4.api import Satrec

logger = logging.getLogger(__name__)

# Largest catalog number representable in the classic 5-digit TLE field.
MAX_TLE_CATALOG_NUMBER = 99999
# Upper bound accepted for an OMM NORAD_CAT_ID (9 digits, as in CCSDS OMM).
MAX_OMM_CATALOG_NUMBER = 999999999

SOURCE_FORMATS = ("tle", "omm")

# Fields sgp4.omm.initialize needs but which are bookkeeping rather than
# orbital elements; CelesTrak always sends them, defaults cover other
# producers that omit them.
_OMM_BOOKKEEPING_DEFAULTS = {
    "OBJECT_ID": "",
    "CLASSIFICATION_TYPE": "U",
    "EPHEMERIS_TYPE": 0,
    "ELEMENT_SET_NO": 999,
    "REV_AT_EPOCH": 0,
    "MEAN_MOTION_DDOT": 0.0,
}

_OMM_REQUIRED = (
    "NORAD_CAT_ID", "EPOCH", "MEAN_MOTION", "ECCENTRICITY", "INCLINATION",
    "RA_OF_ASC_NODE", "ARG_OF_PERICENTER", "MEAN_ANOMALY", "BSTAR", "MEAN_MOTION_DOT",
)
_OMM_NUMERIC = (
    "MEAN_MOTION", "ECCENTRICITY", "INCLINATION", "RA_OF_ASC_NODE", "ARG_OF_PERICENTER",
    "MEAN_ANOMALY", "BSTAR", "MEAN_MOTION_DOT", "MEAN_MOTION_DDOT",
)


def parse_tle_text(text):
    """Parse standard 3-line TLE blocks (name, line1, line2) into normalized
    records. Lines are passed through unchanged."""
    lines = [ln.rstrip("\r") for ln in text.split("\n") if ln.strip() != ""]
    objects = []
    i = 0
    while i + 2 < len(lines):
        name, l1, l2 = lines[i], lines[i + 1], lines[i + 2]
        if not (l1.startswith("1 ") and l2.startswith("2 ")):
            i += 1
            continue
        norad_id = l1[2:7].strip()
        objects.append({
            "norad_id": norad_id,
            "name": name.strip(),
            "tle_line1": l1,
            "tle_line2": l2,
            "source_format": "tle",
            "omm": None,
        })
        i += 3
    return objects


def _normalize_epoch(epoch):
    # sgp4.omm.initialize expects exactly YYYY-MM-DDTHH:MM:SS.ffffff.
    epoch = str(epoch).strip().rstrip("Z")
    if "." not in epoch:
        epoch += ".000000"
    return epoch


def norad_key(catalog_number):
    """Canonical norad_id string: zero-padded to 5 digits, like the TLE
    line-1 field ("00005", "44804"), unpadded beyond it ("270000")."""
    return f"{int(catalog_number):05d}"


def _catalog_number(fields):
    raw = fields.get("NORAD_CAT_ID")
    try:
        norad = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError(f"NORAD_CAT_ID {raw!r} is not an integer catalog number") from None
    if not 0 < norad <= MAX_OMM_CATALOG_NUMBER:
        raise ValueError(f"NORAD_CAT_ID {norad} is outside 1..{MAX_OMM_CATALOG_NUMBER}")
    return norad


def validate_omm(fields):
    """Validate one OMM record (dict keyed by CCSDS OMM keyword names) and
    return the normalized field dict that SGP4 is initialised from (the form
    stored in objects.omm_json). Raises ValueError with a specific reason for
    anything SGP4 cannot use; never repairs or invents elements."""
    if not isinstance(fields, dict):
        raise ValueError("OMM record is not a JSON object")
    missing = [k for k in _OMM_REQUIRED if fields.get(k) in (None, "")]
    if missing:
        raise ValueError(f"OMM record missing required fields: {missing}")
    norad = _catalog_number(fields)

    merged = {**_OMM_BOOKKEEPING_DEFAULTS, **{k: v for k, v in fields.items() if v is not None}}
    for key in _OMM_NUMERIC:
        try:
            value = float(merged[key])
        except (TypeError, ValueError):
            raise ValueError(f"OMM field {key}={merged[key]!r} is not numeric") from None
        if not math.isfinite(value):
            raise ValueError(f"OMM field {key}={merged[key]!r} is not finite")
        merged[key] = value
    if merged["MEAN_MOTION"] <= 0:
        raise ValueError(f"MEAN_MOTION {merged['MEAN_MOTION']} must be > 0 rev/day")
    if not 0.0 <= merged["ECCENTRICITY"] < 1.0:
        raise ValueError(f"ECCENTRICITY {merged['ECCENTRICITY']} must be in [0, 1)")
    if not 0.0 <= merged["INCLINATION"] <= 180.0:
        raise ValueError(f"INCLINATION {merged['INCLINATION']} must be in [0, 180] deg")

    merged["NORAD_CAT_ID"] = norad
    merged["EPOCH"] = _normalize_epoch(merged["EPOCH"])
    try:
        merged["ELEMENT_SET_NO"] = int(merged["ELEMENT_SET_NO"])
        merged["REV_AT_EPOCH"] = int(merged["REV_AT_EPOCH"])
        merged["EPHEMERIS_TYPE"] = int(merged["EPHEMERIS_TYPE"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"OMM bookkeeping field is not an integer: {exc}") from None
    merged["CLASSIFICATION_TYPE"] = str(merged["CLASSIFICATION_TYPE"] or "U")
    merged["OBJECT_ID"] = str(merged["OBJECT_ID"] or "")
    if "OBJECT_NAME" in merged:
        merged["OBJECT_NAME"] = str(merged["OBJECT_NAME"] or "")

    sat = _satrec_from_omm_fields(merged)
    # One evaluation at epoch catches element sets sgp4init accepts but the
    # propagator cannot evaluate.
    err, _, _ = sat.sgp4(sat.jdsatepoch, sat.jdsatepochF)
    if err:
        raise ValueError(f"SGP4 cannot propagate OMM elements for {norad} at epoch (error code {err})")
    return merged


def _satrec_from_omm_fields(fields):
    sat = Satrec()
    try:
        omm.initialize(sat, fields)
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(f"sgp4 could not initialise OMM elements: {exc}") from None
    if sat.error:
        raise ValueError(f"sgp4 rejected OMM elements for {fields.get('NORAD_CAT_ID')} (error code {sat.error})")
    return sat


def omm_to_tle_lines(fields):
    """One OMM record -> classic (line1, line2). Utility only: ingest stores
    OMM natively and never relies on this. Raises ValueError for a record
    that cannot be represented as a classic TLE (NORAD_CAT_ID > 99999)."""
    missing = [k for k in _OMM_REQUIRED if fields.get(k) in (None, "")]
    if missing:
        raise ValueError(f"OMM record missing required fields: {missing}")
    norad = int(fields["NORAD_CAT_ID"])
    if not 0 < norad <= MAX_TLE_CATALOG_NUMBER:
        raise ValueError(
            f"NORAD_CAT_ID {norad} does not fit the 5-digit TLE catalog field; "
            "such objects are carried as native OMM elements instead"
        )
    merged = validate_omm(fields)
    # TLE columns wrap these counters (4-digit element set, 5-digit rev).
    merged["ELEMENT_SET_NO"] %= 10000
    merged["REV_AT_EPOCH"] %= 100000
    return exporter.export_tle(_satrec_from_omm_fields(merged))


def omm_record(fields):
    """Validated OMM fields -> normalized record (raises ValueError)."""
    merged = validate_omm(fields)
    return {
        "norad_id": norad_key(merged["NORAD_CAT_ID"]),
        "name": str(merged.get("OBJECT_NAME") or "").strip(),
        "tle_line1": None,
        "tle_line2": None,
        "source_format": "omm",
        "omm": merged,
    }


def parse_omm_records(text):
    """Parse CelesTrak FORMAT=json (a JSON array of OMM records). Returns
    (records, rejected) where rejected is a list of {norad_id, name, reason}
    for records that failed validation (also logged)."""
    data = json.loads(text)
    if isinstance(data, dict):
        data = [data]
    records, rejected = [], []
    for rec in data:
        try:
            records.append(omm_record(rec))
        except ValueError as exc:
            name, cat = (rec.get("OBJECT_NAME"), rec.get("NORAD_CAT_ID")) if isinstance(rec, dict) else (None, None)
            logger.warning("Rejecting OMM record %s (%s): %s", name, cat, exc)
            rejected.append({"norad_id": None if cat is None else str(cat), "name": name, "reason": str(exc)})
    return records, rejected


def parse_omm_json(text):
    """parse_omm_records without the rejection list (rejections are logged)."""
    return parse_omm_records(text)[0]


def source_format_of(obj):
    """'tle' or 'omm'; rows stored before source_format existed are TLE."""
    return obj.get("source_format") or "tle"


def omm_fields_of(obj):
    """The stored OMM field dict of an OMM object (record or DB row)."""
    fields = obj.get("omm")
    if fields is None:
        raw = obj.get("omm_json")
        if not raw:
            raise ValueError(f"OMM object {obj.get('norad_id')} has no stored OMM elements")
        fields = json.loads(raw)
    return fields


def satrec_for(obj):
    """Source-independent sgp4 Satrec for one object (normalized record or
    db.objects row): TLE lines via Satrec.twoline2rv, OMM via the stored OMM
    elements. Raises ValueError for an unusable record."""
    fmt = source_format_of(obj)
    if fmt == "tle":
        return Satrec.twoline2rv(obj["tle_line1"], obj["tle_line2"])
    if fmt == "omm":
        return _satrec_from_omm_fields(omm_fields_of(obj))
    raise ValueError(f"Unknown source_format {fmt!r} for {obj.get('norad_id')}")


def tle_representable(obj):
    """True if the object's catalog number fits the classic TLE field (used
    by the demo, whose disclosed override is written as TLE lines)."""
    try:
        return 0 < int(obj["norad_id"]) <= MAX_TLE_CATALOG_NUMBER
    except (TypeError, ValueError, KeyError):
        return False
