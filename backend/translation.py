"""Local, optional machine translation of AI briefs into Indian languages.

Design constraints (see config.py "Local dynamic translation"):

* No external translation API of any kind. The only real backend is a LOCAL
  IndicTrans2 model (ai4bharat indictrans2-en-indic) run via transformers +
  IndicTransToolkit.
* Disabled by default (LOCAL_TRANSLATION_ENABLED=false). When disabled, heavy
  dependencies (torch / transformers / IndicTransToolkit) are never imported.
* Lazy: the model is loaded on the first non-English request, from local files
  only (``local_files_only=True`` plus HF offline env vars) -- it is never
  downloaded at startup or at request time.
* Safety: numbers with units, timestamps, NORAD IDs, risk-tier words and
  technical acronyms are masked with placeholders before translation and
  restored afterwards. If any protected token does not survive verbatim, the
  translation is treated as failed and the English text is returned.
* English is the source of record. This module never touches the database.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import threading
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)

from backend.config import (
    LOCAL_TRANSLATION_ENABLED_DEFAULT,
    TRANSLATION_DEVICE_DEFAULT,
    TRANSLATION_MAX_CHARS,
    TRANSLATION_MODEL_DEFAULT,
)

# UI language code -> IndicTrans2 (FLORES-200 style) language tag.
LANGUAGE_TAGS = {
    "en": "eng_Latn",
    "hi": "hin_Deva",
    "ta": "tam_Taml",
    "te": "tel_Telu",
    "mr": "mar_Deva",
    "bn": "ben_Beng",
    "gu": "guj_Gujr",
    "kn": "kan_Knda",
    "ml": "mal_Mlym",
    "pa": "pan_Guru",
    "or": "ory_Orya",
}
SUPPORTED_TARGETS = tuple(k for k in LANGUAGE_TAGS if k != "en")

ENGINE_NAME = "indictrans2"

# ---------------------------------------------------------------------------
# Protected-token masking
# ---------------------------------------------------------------------------
PROTECTED_ACRONYMS = (
    "TLE", "SGP4", "TCA", "Pc", "NORAD", "RPO", "CDM", "Δv", "delta-v", "LLM",
    "Ollama", "CelesTrak", "UTC", "IST", "SSA", "GP", "LEO", "GEO", "ISRO",
    "km/s", "m/s", "km", "m",
)
RISK_TIER_WORDS = ("Critical", "High", "Medium", "Low", "CRITICAL", "HIGH", "MEDIUM", "LOW")

_UNIT = r"(?:km/s|m/s|km|m|s|h|hr|hrs|hours?|min(?:utes?)?|d|days?|seconds?|%|°)"
_NUMBER = r"[-+−]?\d[\d,]*(?:\.\d+)?(?:[eE][-+−]?\d+)?"

# Order matters: longest / most specific patterns first.
_PROTECT_PATTERNS = [
    # ISO-8601 timestamps, e.g. 2026-07-21T04:12:00+00:00 / 2026-07-21 04:12 UTC
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?(?:\s?UTC)?",
    # Clock times, e.g. 04:12:00 UTC
    r"\b\d{1,2}:\d{2}(?::\d{2})?(?:\s?(?:UTC|IST))?",
    # "NORAD 12345" / "NORAD ID 12345"
    r"\bNORAD(?:\s+ID)?\s*#?\s*\d{1,9}\b",
    # "1 in 5,000"
    r"\b1\s+in\s+[\d,]+\b",
    # Δv / delta-v with value
    r"(?:Δv|delta-v)\s*(?:≈|=|~|of)?\s*" + _NUMBER + r"\s*" + _UNIT + r"?",
    # Scientific notation written as 3.5×10^-5 / 3.5 x 10-5
    r"\d+(?:\.\d+)?\s?[×x]\s?10\^?[-−]?\d+",
    # Digit-led designators, e.g. the "1C" in FENGYUN 1C, "2BR1", "3b"
    r"\b\d+[A-Za-z][A-Za-z0-9]*\b",
    # Numbers with optional unit (incl. scientific notation 1.2e-4)
    _NUMBER + r"(?:\s?" + _UNIT + r"(?![A-Za-z]))?",
    # Object designators like RISAT-2B, COSMOS-2251, CARTOSAT-3 (all-caps with - or digits)
    r"\b[A-Z][A-Z0-9]*(?:[-/][A-Z0-9]+)+\b",
    r"\b[A-Z]{2,}[0-9]+[A-Z0-9]*\b",
    # Acronyms / risk tiers (word-bounded)
    r"(?<![\w])(?:" + "|".join(re.escape(a) for a in sorted(PROTECTED_ACRONYMS + RISK_TIER_WORDS, key=len, reverse=True)) + r")(?![\w])",
    # Any remaining ALL-CAPS word (object names such as CARTOSAT, DEB)
    r"\b[A-Z]{2,}\b",
]
_PROTECT_RE = re.compile("|".join(f"(?:{p})" for p in _PROTECT_PATTERNS))

_PLACEHOLDER_FMT = "<P{}>"
# Tolerant placeholder matcher: models sometimes add spaces or emit native
# digits inside tags; restore handles both.
_INDIC_DIGITS = str.maketrans(
    "०१२३४५६७८९" "০১২৩৪৫৬৭৮৯" "૦૧૨૩૪૫૬૭૮૯" "੦੧੨੩੪੫੬੭੮੯" "୦୧୨୩୪୫୬୭୮୯"
    "௦௧௨௩௪௫௬௭௮௯" "౦౧౨౩౪౫౬౭౮౯" "೦೧೨೩೪೫೬೭೮೯" "൦൧൨൩൪൫൬൭൮൯",
    "0123456789" * 9,
)
_PLACEHOLDER_RE = re.compile(r"<\s*[Pp]\s*([0-9०-९০-৯૦-૯੦-੯୦-୯௦-௯౦-౯೦-೯൦-൯]+)\s*>")


def _protect_re(extra_terms=()):
    """Pattern set, with caller-supplied literal terms (e.g. the event's full
    object names such as "FENGYUN 1C DEB") matched first, as whole units."""
    terms = sorted({t for t in extra_terms if t and t.strip()}, key=len, reverse=True)
    if not terms:
        return _PROTECT_RE
    literal = "|".join(re.escape(t) for t in terms)
    return re.compile(f"(?:{literal})|" + "|".join(f"(?:{p})" for p in _PROTECT_PATTERNS))


def mask_protected(text: str, extra_terms=()) -> tuple[str, list[str]]:
    """Replace protected tokens with <P0>, <P1>, ... Returns (masked, tokens)."""
    tokens: list[str] = []

    def _sub(m: re.Match) -> str:
        tok = m.group(0)
        if not tok.strip():
            return tok
        tokens.append(tok)
        return _PLACEHOLDER_FMT.format(len(tokens) - 1)

    return _protect_re(extra_terms).sub(_sub, text), tokens


def unmask_protected(text: str, tokens: list[str]) -> str:
    """Restore placeholders. Raises ValueError unless every token is restored
    exactly once and no stray placeholder remains."""
    seen: dict[int, int] = {}

    def _sub(m: re.Match) -> str:
        idx = int(m.group(1).translate(_INDIC_DIGITS))
        if idx >= len(tokens):
            raise ValueError(f"unknown placeholder index {idx}")
        seen[idx] = seen.get(idx, 0) + 1
        return tokens[idx]

    out = _PLACEHOLDER_RE.sub(_sub, text)
    missing = [i for i in range(len(tokens)) if seen.get(i, 0) == 0]
    duplicated = [i for i, n in seen.items() if n > 1]
    if missing or duplicated:
        raise ValueError(f"protected tokens not preserved (missing={missing}, duplicated={duplicated})")
    return out


def protected_tokens_preserved(original: str, translated: str, extra_terms=()) -> bool:
    """Every protected token of the English original appears verbatim in the
    translation (multiset containment)."""
    _, tokens = mask_protected(original, extra_terms)
    remaining = translated
    for tok in tokens:
        pos = remaining.find(tok)
        if pos < 0:
            return False
        remaining = remaining[:pos] + remaining[pos + len(tok):]
    return True


def _split_segments(masked: str) -> list[str]:
    """Split into sentence-sized segments (keeps model inputs short). Newlines
    are kept as their own separators so layout survives."""
    parts = re.split(r"(\n+)", masked)
    segs: list[str] = []
    for p in parts:
        if not p or p.startswith("\n"):
            segs.append(p)
            continue
        segs.extend(s for s in re.split(r"(?<=[.!?])\s+", p) if s)
    return segs


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _result(ok: bool, text: str, status: str, reason: str | None = None) -> dict:
    return {"ok": ok, "text": text, "status": status, "reason": reason}


# ---------------------------------------------------------------------------
# Service interface
# ---------------------------------------------------------------------------
class LocalTranslationService(ABC):
    """Abstract local translation service. Subclasses implement
    ``status()`` and ``_translate_segments()``; ``translate()`` wraps them with
    masking, verification and English fallback."""

    engine = "abstract"

    @abstractmethod
    def status(self) -> dict:
        """{"available": bool, "state": str, "engine": str, "reason": str|None, ...}"""

    @abstractmethod
    def _translate_segments(self, segments: list[str], source_tag: str, target_tag: str) -> list[str]:
        """Translate a batch of already-masked segments."""

    def translate(self, text: str, source_language: str = "en", target_language: str = "en",
                  protected_terms=()) -> dict:
        """Translate ``text``. Always returns a result dict; on any problem the
        English input is returned with ok=False and a reason."""
        text = text or ""
        if target_language == source_language or not text.strip():
            return _result(True, text, "source_language")
        if source_language != "en":
            return _result(False, text, "unsupported", "only English source text is supported")
        if target_language not in SUPPORTED_TARGETS:
            return _result(False, text, "unsupported", f"unsupported target language: {target_language}")
        if len(text) > TRANSLATION_MAX_CHARS:
            return _result(False, text, "failed", "text too long for local translation")
        st = self.status()
        if not st.get("available"):
            return _result(False, text, "unavailable", st.get("reason") or "local translation unavailable")

        masked, tokens = mask_protected(text, protected_terms)
        segments = _split_segments(masked)
        work_idx = [i for i, s in enumerate(segments) if s.strip() and not s.startswith("\n")
                    and _PLACEHOLDER_RE.sub("", s).strip(" .,;:—-()")]
        try:
            translated = self._translate_segments(
                [segments[i] for i in work_idx], LANGUAGE_TAGS[source_language], LANGUAGE_TAGS[target_language],
            )
            if len(translated) != len(work_idx):
                raise ValueError("translator returned a different number of segments")
            out_segments = list(segments)
            for i, t in zip(work_idx, translated):
                out_segments[i] = t
            joined = ""
            for i, s in enumerate(out_segments):
                if i and not s.startswith("\n") and not out_segments[i - 1].endswith("\n") and joined:
                    joined += " "
                joined += s
            restored = unmask_protected(joined, tokens)
            if not protected_tokens_preserved(text, restored, protected_terms):
                raise ValueError("protected token check failed after restore")
        except Exception as exc:  # noqa: BLE001 -- any failure falls back to English
            # Detail stays in the server log (it can contain local paths).
            logger.warning("Local translation failed (%s): %s", type(exc).__name__, exc)
            return _result(False, text, "failed", "translation failed verification; showing English")
        return _result(True, restored, "translated")

    def translate_brief(self, brief_text: str | None, maneuver_text: str | None, target_language: str,
                        protected_terms=()) -> dict:
        """Structured brief translation. Both fields must succeed or both fall
        back to English (never a half-translated brief)."""
        english = {"brief_text": brief_text, "maneuver_text": maneuver_text}
        if not brief_text and not maneuver_text:
            return {"status": "no_text", "reason": "no brief text yet", "texts": dict(english), "english": english}
        out = {}
        status = "translated"
        reason = None
        for key, value in english.items():
            if not value:
                out[key] = value
                continue
            r = self.translate(value, "en", target_language, protected_terms)
            if not r["ok"]:
                return {"status": r["status"], "reason": r["reason"], "texts": dict(english), "english": english}
            if r["status"] == "source_language":
                status = "source_language"
            out[key] = r["text"]
        return {"status": status, "reason": reason, "texts": out, "english": english}


class UnavailableTranslationService(LocalTranslationService):
    """Used when local translation is disabled or cannot be set up."""

    engine = "none"

    def __init__(self, reason: str, state: str = "disabled"):
        self._reason = reason
        self._state = state

    def status(self) -> dict:
        return {"available": False, "state": self._state, "engine": self.engine, "reason": self._reason,
                "external_calls": False}

    def _translate_segments(self, segments, source_tag, target_tag):  # pragma: no cover - never called
        raise RuntimeError(self._reason)


def _hf_cache_dir() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    if os.environ.get("HF_HOME"):
        return Path(os.environ["HF_HOME"]) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def model_locally_present(model: str) -> bool:
    """Cheap, import-free check that the model exists on disk: either a local
    directory, or a repo id already present in the local Hugging Face cache."""
    if not model:
        return False
    p = Path(model).expanduser()
    if p.is_dir():
        return (p / "config.json").exists()
    looks_like_path = any(sep in model for sep in ("\\", ":")) or model.startswith((".", "/", "~"))
    if looks_like_path:
        return False
    snapshots = _hf_cache_dir() / ("models--" + model.replace("/", "--")) / "snapshots"
    return snapshots.is_dir() and any(snapshots.iterdir())


def _deps_present() -> list[str]:
    """Names of missing dependencies (find_spec does not import the package)."""
    return [m for m in ("torch", "transformers", "IndicTransToolkit") if importlib.util.find_spec(m) is None]


class IndicTrans2TranslationService(LocalTranslationService):
    """ai4bharat IndicTrans2 (en->indic), loaded lazily from local files."""

    engine = ENGINE_NAME

    def __init__(self, model: str, device: str = "cpu"):
        self.model_name = model
        self.device = device
        self._lock = threading.Lock()
        self._model = None
        self._tokenizer = None
        self._processor = None
        self._load_error: str | None = None

    # -- status ------------------------------------------------------------
    def status(self) -> dict:
        base = {"engine": self.engine, "model": self.model_name, "device": self.device, "external_calls": False}
        if self._model is not None:
            return {**base, "available": True, "state": "loaded", "reason": None}
        if self._load_error:
            return {**base, "available": False, "state": "error", "reason": self._load_error}
        missing = _deps_present()
        if missing:
            return {**base, "available": False, "state": "unavailable",
                    "reason": f"local translation dependencies not installed: {', '.join(missing)}"}
        if not model_locally_present(self.model_name):
            return {**base, "available": False, "state": "unavailable",
                    "reason": f"translation model not found locally: {self.model_name}"}
        return {**base, "available": True, "state": "ready_to_load",
                "reason": "model will be loaded from local files on first request"}

    # -- lazy load ---------------------------------------------------------
    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            if self._load_error:
                raise RuntimeError(self._load_error)
            # Hard guarantee of no network access from the HF stack.
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            try:
                import torch  # noqa: F401
                from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
                try:
                    from IndicTransToolkit.processor import IndicProcessor
                except ImportError:
                    from IndicTransToolkit import IndicProcessor
                tokenizer = AutoTokenizer.from_pretrained(
                    self.model_name, trust_remote_code=True, local_files_only=True)
                model = AutoModelForSeq2SeqLM.from_pretrained(
                    self.model_name, trust_remote_code=True, local_files_only=True)
                model = model.to(self.device)
                model.eval()
                self._processor = IndicProcessor(inference=True)
                self._tokenizer = tokenizer
                self._model = model
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to load local translation model")
                self._load_error = f"failed to load local translation model ({type(exc).__name__}); see backend log"
                raise RuntimeError(self._load_error) from exc

    def _translate_segments(self, segments, source_tag, target_tag):
        if not segments:
            return []
        self._ensure_loaded()
        import torch

        ip, tok, model = self._processor, self._tokenizer, self._model
        batch = ip.preprocess_batch(segments, src_lang=source_tag, tgt_lang=target_tag)
        inputs = tok(batch, truncation=True, padding="longest", return_tensors="pt",
                     return_attention_mask=True).to(self.device)
        with torch.no_grad():
            generated = model.generate(**inputs, use_cache=True, min_length=0, max_length=256,
                                       num_beams=5, num_return_sequences=1)
        decoded = tok.batch_decode(generated, skip_special_tokens=True, clean_up_tokenization_spaces=True)
        return ip.postprocess_batch(decoded, lang=target_tag)


# ---------------------------------------------------------------------------
# Factory / singleton
# ---------------------------------------------------------------------------
_service: LocalTranslationService | None = None
_service_lock = threading.Lock()


def build_translation_service() -> LocalTranslationService:
    """Build a service from the environment. Never imports heavy deps."""
    if not _env_bool("LOCAL_TRANSLATION_ENABLED", LOCAL_TRANSLATION_ENABLED_DEFAULT):
        return UnavailableTranslationService(
            "local translation disabled (set LOCAL_TRANSLATION_ENABLED=true and install IndicTrans2 locally)")
    model = os.environ.get("TRANSLATION_MODEL", TRANSLATION_MODEL_DEFAULT)
    device = os.environ.get("TRANSLATION_DEVICE", TRANSLATION_DEVICE_DEFAULT)
    return IndicTrans2TranslationService(model, device)


def get_translation_service() -> LocalTranslationService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = build_translation_service()
    return _service


def reset_translation_service(service: LocalTranslationService | None = None) -> None:
    """Testing hook: replace (or clear) the process-wide service."""
    global _service
    with _service_lock:
        _service = service
