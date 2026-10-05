"""TLE/OMM fetch + parse + store (blueprint Section 6.1).

Resolution strategy: rather than hardcoding NORAD IDs (which drift as objects
decay), Group A/C entries in working_set.json are resolved by CelesTrak NAME=
query at fetch time, and Group B debris by CelesTrak GROUP= query. This is
more robust to catalog churn than pinned IDs, at the cost of an extra network
round trip per name -- acceptable since ingest only runs on /api/refresh.

Group A source: the operator-managed protected-asset registry in the DB
(protected_assets, status='active'), seeded once from working_set.json
group_a (db.ensure_protected_assets_seeded). Group B/C: working_set.json.

Format: CelesTrak is queried as FORMAT=TLE by default. With
ANTARIKSHA_CELESTRAK_FORMAT=omm it is queried as FORMAT=json (CCSDS OMM)
instead. Either way each object is first converted to the same normalized
record (backend/orbital_formats.py): external format -> normalized orbital
object -> existing pipeline. OMM records are kept as native OMM elements
(no generated TLE), so catalog numbers beyond the 5-digit TLE field are
preserved; OMM records failing validation are rejected with a reason.

Named-entry resolution (Group A/C; CelesTrak NAME= is a substring query):
  - 0 results -> unresolved ("no object matched").
  - exactly 1 result (and no exact_match configured) -> resolved.
  - exact_match configured -> resolved only if exactly one result has that
    exact name; otherwise unresolved.
  - several results and no exact_match -> unresolved ("ambiguous").
Nothing is ever picked arbitrarily. An unresolved entry keeps the object it
resolved to in the previous successful ingest active (its previous elements,
never overwritten) and is recorded in ingest_runs.unresolved_json.

Catalog reconciliation: a successful ingest makes exactly the fetched set
(plus kept unresolved entries) the active catalog (db.reconcile_catalog);
objects that dropped out are marked inactive, never deleted. A failed ingest
(any exception, or no objects at all) raises before the catalog is touched,
so the previous working set stays active. Safety checks raise
IngestSafetyError -- catalog untouched, an ingest_runs row with
status='failed' records why -- when fewer than
INGEST_MIN_GROUP_A_RESOLVED_FRACTION of Group A entries resolve, or when
reconciliation would deactivate more than INGEST_MAX_DEACTIVATE_FRACTION of
the active catalog. Group A remains exactly the operator-configured
protected-asset list; nothing here infers ownership.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import requests

from backend.config import (
    CELESTRAK_BASE_URL,
    CELESTRAK_FORMAT,
    CELESTRAK_FORMATS,
    INGEST_MAX_DEACTIVATE_FRACTION,
    INGEST_MIN_GROUP_A_RESOLVED_FRACTION,
    TLE_CACHE_DIR,
    WORKING_SET_PATH,
)
from backend.db import (
    CatalogDeactivationRefused,
    active_group_a_entries,
    ensure_protected_assets_seeded,
    init_db,
    insert_ingest_run,
    latest_ingest_run,
    reconcile_catalog,
)
from backend.orbital_formats import parse_omm_records, parse_tle_text
from backend.provenance import DATA_SOURCE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 15


class IngestSafetyError(RuntimeError):
    """An ingest was refused by a safety check; the catalog is unchanged.
    `reason` is a short machine-readable code ("group_a_unresolved",
    "mass_deactivation"); `ingest_run_id` is the status='failed' row."""

    def __init__(self, message, reason, ingest_run_id=None):
        super().__init__(message)
        self.reason = reason
        self.ingest_run_id = ingest_run_id


class IngestFailedError(RuntimeError):
    """The ingest could not fetch/parse the working set (raised by
    pipeline.run_full_pipeline around run_ingest); the catalog is unchanged.
    The message is operator-facing and contains no paths or tracebacks."""


def _load_working_set():
    with open(WORKING_SET_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_group_a():
    """Group A (protected assets) = status='active' rows of the DB
    protected-asset registry, in working_set.json entry shape and order (so
    resolution is unchanged). The registry is seeded ONCE from this working
    set's group_a; afterwards working_set.json group_a is no longer read.
    Group B/C still come from working_set.json."""
    ensure_protected_assets_seeded(WORKING_SET_PATH)
    return active_group_a_entries()


def _parse_tle_text(text):
    """Parse standard 3-line TLE blocks (name, line1, line2) into normalized
    records (see backend/orbital_formats.py)."""
    return parse_tle_text(text)


def _celestrak_format():
    fmt = CELESTRAK_FORMAT
    if fmt not in CELESTRAK_FORMATS:
        raise ValueError(f"Unknown ANTARIKSHA_CELESTRAK_FORMAT '{fmt}'; expected one of {list(CELESTRAK_FORMATS)}")
    return fmt


def _format_query_param(fmt):
    return "TLE" if fmt == "tle" else "json"


def _cache_suffix(fmt):
    # OMM responses are cached as .json so the two formats never mix.
    return ".json" if fmt == "omm" else ".tle"


def _parse_catalog_text(text, fmt, rejected=None):
    """External format -> list of normalized orbital records. OMM records
    failing validation are appended to `rejected` (each with a reason)."""
    if fmt == "omm":
        records, bad = parse_omm_records(text)
        if rejected is not None:
            rejected.extend(bad)
        return records
    return _parse_tle_text(text)


def _cache_path(cache_key, suffix=".tle"):
    TLE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe_key = "".join(c if c.isalnum() or c in "-_." else "_" for c in cache_key)
    return TLE_CACHE_DIR / f"{safe_key}{suffix}"


def _count_records(text, fmt):
    """Number of usable records in a response (raises for unparseable data)."""
    return len(_parse_catalog_text(text, fmt))


def _fetch_with_cache(url, cache_key, suffix=".tle", fmt=None):
    """Fetch one CelesTrak query. The response is parsed and validated BEFORE
    it is cached: an HTTP error, a network failure, an explicit no-data
    reply, an empty/garbage body or one with zero usable records is a failed
    fetch -- the previous good cache is used (and never overwritten)."""
    cache_file = _cache_path(cache_key, suffix)
    if fmt is None:
        fmt = "omm" if suffix == ".json" else "tle"
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        text = resp.text
        # "[]" is the FORMAT=json form of "no data".
        if "No GP data found" in text or "Invalid query" in text or text.strip() in ("", "[]"):
            raise ValueError(f"CelesTrak returned no data for {cache_key}: {text.strip()[:200]}")
        if _count_records(text, fmt) == 0:
            raise ValueError(f"CelesTrak response for {cache_key} contained no usable records")
        cache_file.write_text(text, encoding="utf-8")
        return text, False
    except Exception as exc:
        if cache_file.exists():
            logger.warning("Network fetch failed for %s (%s); loading from cache %s", cache_key, exc, cache_file)
            return cache_file.read_text(encoding="utf-8"), True
        raise RuntimeError(
            f"Could not fetch '{cache_key}' from CelesTrak and no cache exists at {cache_file}. "
            "Run once with network access to populate the offline cache."
        ) from exc


def _entry_key(group, entry):
    return f"{group}:{entry['name_query']}"


def resolve_matches(entry, parsed):
    """Apply the resolution rule to one named entry's parsed results.
    Returns (obj, None) when resolved, (None, reason) otherwise."""
    query = entry["name_query"]
    exact = entry.get("exact_match")
    if exact:
        hits = [p for p in parsed if p["name"] == exact]
        if len(hits) == 1:
            return hits[0], None
        return None, (f"exact_match '{exact}' matched {len(hits)} of {len(parsed)} "
                      f"result(s) for NAME query '{query}'")
    if len(parsed) == 1:
        return parsed[0], None
    if not parsed:
        return None, f"no object matched NAME query '{query}' (possibly decayed or renamed)"
    names = sorted(p["name"] for p in parsed)
    shown = ", ".join(names[:5]) + (", ..." if len(names) > 5 else "")
    return None, (f"ambiguous: {len(parsed)} objects matched NAME query '{query}' ({shown}); "
                  "configure exact_match to select one")


def _resolve_named_entry(entry, cache_prefix, object_type, extra_fields, fmt="tle"):
    """Fetch+parse a single name_query entry. Returns (obj | None,
    unresolved_reason | None, used_cache, rejected_records). Designed to run
    inside a thread pool -- each call is one independent network round trip
    to CelesTrak."""
    query = entry["name_query"]
    url = f"{CELESTRAK_BASE_URL}?NAME={quote(query)}&FORMAT={_format_query_param(fmt)}"
    text, used_cache = _fetch_with_cache(url, f"{cache_prefix}_{query}", _cache_suffix(fmt), fmt)
    rejected = []
    parsed = _parse_catalog_text(text, fmt, rejected)
    obj, reason = resolve_matches(entry, parsed)
    if obj is None:
        logger.warning("%s entry '%s' unresolved: %s", cache_prefix, query, reason)
        return None, reason, used_cache, rejected
    obj.update({"object_type": object_type, **extra_fields})
    return obj, None, used_cache, rejected


def fetch_tles():
    """Download orbital elements from CelesTrak for all three working-set
    groups.

    Group A/C entries (one CelesTrak name lookup each) are fetched
    concurrently via a thread pool -- these are independent network I/O
    calls, so this cuts wall-clock time roughly by the pool size compared to
    fetching them one at a time (matters for the /api/refresh <30s target).

    Returns list[dict]: normalized records (backend/orbital_formats.py:
    norad_id, name, source_format 'tle'/'omm', tle_line1/tle_line2 or omm)
    plus object_type, criticality, owner_country, group ('A'/'B'/'C').
    Resolution details are left on function attributes for run_ingest:
    using_cache, source_format, resolution {entry_key: norad_id},
    unresolved [{group, name_query, reason}], rejected (invalid OMM
    records with reasons), group_a_total, group_a_resolved.
    """
    fmt = _celestrak_format()
    working_set = _load_working_set()
    group_a = _load_group_a()
    results = []
    using_cache_any = False
    resolution, unresolved, rejected_all = {}, [], []

    def resolve_a(entry):
        return _resolve_named_entry(
            entry, "groupA", "satellite",
            {"criticality": entry["criticality"], "owner_country": "India", "group": "A"}, fmt,
        )

    def resolve_c(entry):
        return _resolve_named_entry(
            entry, "groupC", "foreign_sat",
            {"criticality": None, "owner_country": "foreign", "group": "C"}, fmt,
        )

    jobs = ([("A", resolve_a, e) for e in group_a]
            + [("C", resolve_c, e) for e in working_set["group_c"]])

    group_a_resolved = 0
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [(group, entry, pool.submit(fn, entry)) for group, fn, entry in jobs]
        for group, entry, future in futures:
            obj, reason, used_cache, rejected = future.result()
            using_cache_any = using_cache_any or used_cache
            rejected_all.extend(rejected)
            if obj is not None:
                results.append(obj)
                resolution[_entry_key(group, entry)] = obj["norad_id"]
                group_a_resolved += group == "A"
            else:
                unresolved.append({"group": group, "name_query": entry["name_query"], "reason": reason})

    # Group B: debris, resolved by CelesTrak GROUP query, subsampled.
    # Only 3 requests total -- not worth parallelizing separately.
    for entry in working_set["group_b_debris_groups"]:
        group_name = entry["celestrak_group"]
        url = f"{CELESTRAK_BASE_URL}?GROUP={quote(group_name)}&FORMAT={_format_query_param(fmt)}"
        text, used_cache = _fetch_with_cache(url, f"groupB_{group_name}", _cache_suffix(fmt), fmt)
        using_cache_any = using_cache_any or used_cache
        parsed = _parse_catalog_text(text, fmt, rejected_all)[: entry.get("max_count", 50)]
        for obj in parsed:
            obj.update({
                "object_type": "debris",
                "criticality": None,
                "owner_country": None,
                "group": "B",
            })
        results.extend(parsed)

    fetch_tles.using_cache = using_cache_any
    fetch_tles.source_format = fmt
    fetch_tles.resolution = resolution
    fetch_tles.unresolved = unresolved
    fetch_tles.rejected = rejected_all
    fetch_tles.group_a_total = len(group_a)
    fetch_tles.group_a_resolved = group_a_resolved
    return results


def store_objects(tles, keep_active_ids=(), max_deactivate_fraction=None):
    """Make `tles` (plus keep_active_ids) the active catalog in one
    transaction: upsert each record (active) and mark every other stored
    object inactive. Returns (n_active, n_deactivated)."""
    return reconcile_catalog(tles, keep_active_ids=keep_active_ids,
                             max_deactivate_fraction=max_deactivate_fraction)


def _previous_resolution():
    """{entry_key: norad_id} from the latest successful ingest, or {}."""
    run = latest_ingest_run()
    if not run or not run.get("resolution_json"):
        return {}
    try:
        return json.loads(run["resolution_json"]) or {}
    except (TypeError, ValueError):
        return {}


def run_ingest():
    """Fetch the working set and reconcile the catalog.

    Success: fetch_tles() returns a record for every working-set entry it
    could resolve -- from the network or, per entry, from that entry's last
    good cached response -- or raises (an entry with neither network nor
    cache aborts the whole ingest). A cache-backed result counts as a
    successful ingest: it reconciles the catalog, and its ingest_runs row
    has used_cache=1 so the following screening is labelled 'cached', never
    'live'. A named entry that does not resolve to exactly one object keeps
    its previously ingested object active and is recorded as unresolved.

    Failure: any exception from fetch_tles(), or an empty result, raises
    before the catalog is touched -- nothing is deactivated and no
    ingest_runs row is written. A safety-check refusal (see module
    docstring) raises IngestSafetyError and writes a status='failed' row."""
    init_db()
    tles = fetch_tles()
    if not tles:
        raise RuntimeError("Ingest returned no objects for the working set; existing catalog left unchanged.")
    counts = {"A": 0, "B": 0, "C": 0}
    for obj in tles:
        counts[obj["group"]] += 1
    using_cache = getattr(fetch_tles, "using_cache", False)
    source_format = getattr(fetch_tles, "source_format", None)
    resolution = dict(getattr(fetch_tles, "resolution", None) or {})
    unresolved = [dict(u) for u in (getattr(fetch_tles, "unresolved", None) or [])]
    rejected = list(getattr(fetch_tles, "rejected", None) or [])
    group_a_total = getattr(fetch_tles, "group_a_total", None)
    group_a_resolved = getattr(fetch_tles, "group_a_resolved", None)

    n_resolved = len(resolution)
    # Unresolved entries keep the object they resolved to last time.
    previous = _previous_resolution()
    fetched_ids = {o["norad_id"] for o in tles}
    keep_ids = []
    for u in unresolved:
        kept = previous.get(f"{u['group']}:{u['name_query']}")
        u["kept_norad_id"] = kept
        if kept is not None:
            resolution[f"{u['group']}:{u['name_query']}"] = kept
            if kept not in fetched_ids:
                keep_ids.append(kept)

    run_fields = dict(
        source=DATA_SOURCE, used_cache=using_cache, object_count=sum(counts.values()), counts=counts,
        source_format=source_format, resolved_count=n_resolved,
        unresolved=unresolved, resolution=resolution, rejected_records=rejected,
    )

    def refuse(message, reason):
        run_id = insert_ingest_run(status="failed", failure_reason=message, **run_fields)
        logger.error("Ingest refused (%s): %s", reason, message)
        raise IngestSafetyError(message, reason, run_id)

    if group_a_total:
        fraction = group_a_resolved / group_a_total
        if fraction < INGEST_MIN_GROUP_A_RESOLVED_FRACTION:
            refuse(
                f"Only {group_a_resolved} of {group_a_total} configured Group A (protected) entries "
                f"resolved (minimum {INGEST_MIN_GROUP_A_RESOLVED_FRACTION:.0%}); catalog left unchanged. "
                "Check the protected-asset registry name_query/exact_match against the catalog.",
                "group_a_unresolved",
            )

    try:
        _, n_deactivated = store_objects(tles, keep_active_ids=keep_ids,
                                         max_deactivate_fraction=INGEST_MAX_DEACTIVATE_FRACTION)
    except CatalogDeactivationRefused as exc:
        refuse(str(exc), "mass_deactivation")
    if n_deactivated:
        logger.info("Catalog reconciliation: %d object(s) no longer in the working set marked inactive", n_deactivated)
    if unresolved:
        logger.warning("Ingest: %d named entr%s unresolved (previous object kept active where known)",
                       len(unresolved), "y" if len(unresolved) == 1 else "ies")
    # Persistent run provenance: one ingest_runs row per ingest (used_cache =
    # any group fell back to the offline cache).
    run_ingest.last_ingest_run_id = insert_ingest_run(status="ok", **run_fields)
    return counts, using_cache


if __name__ == "__main__":
    counts, using_cache = run_ingest()
    print(f"Group A (Indian assets):     {counts['A']}")
    print(f"Group B (debris):            {counts['B']}")
    print(f"Group C (other active sats): {counts['C']}")
    print(f"Total objects:               {sum(counts.values())}")
    print(f"Using offline cache:         {using_cache}")
