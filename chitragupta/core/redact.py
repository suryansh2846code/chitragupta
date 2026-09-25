"""Secret redaction — runs BEFORE anything is written or extracted.

Sensitive content is excluded/redacted before extraction so OTPs, passwords,
tokens and account numbers can never be embedded into a durable canonical fact.
Precision over recall: better to redact an innocent number than to persist a
live credential.

**It lives in `core/` because it is a rule about what may be written to the
database**, and that is what `core/` is. It used to live in
`brain/canonical/`, which meant `core/store.py` imported `brain` to enforce it
— an arrow pointing the wrong way up the dependency graph
(`docs/ARCHITECTURE.md` §3 rule 1, recorded as deviation §6.2), and the edge
that made `brain`, `connectors` and `core` one cycle at package level.

The redaction itself has not moved: it still runs on the ingest path, before a
credential can reach a model or the index. Only the direction changed.
`brain.canonical.redact` re-exports both names, so every existing import reads
the same.
"""
from __future__ import annotations

import re

# Each pattern → replacement tag. Order matters (more specific first).
_PATTERNS: list[tuple[re.Pattern, str]] = [
    # OTP / verification codes stated in context ("code is 483920", "OTP: 12345")
    (re.compile(r"\b(?:otp|one[-\s]?time\s+(?:code|password|passcode)|verification"
                r"\s+code|security\s+code|auth(?:entication)?\s+code|passcode|pin)\b"
                r"[^0-9]{0,20}\d{4,8}\b", re.I), "[REDACTED_OTP]"),
    # "password is hunter2" / "password: ..."
    (re.compile(r"\b(?:password|passwd|pwd)\b\s*(?:is|:|=)\s*\S+", re.I),
     "[REDACTED_PASSWORD]"),
    # API keys / bearer tokens / common secret prefixes
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\bgh[posru]_[A-Za-z0-9]{20,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "[REDACTED_KEY]"),
    # The key shapes of the providers this app itself connects to. `errors.py`
    # has always stripped `xai-` and `AIza` from anything user-visible; the
    # ingest path did not, so a key pasted into an email was redacted in an
    # error message and stored verbatim in the brain — then written to
    # `brain-export/` as plaintext and replayed to the model on every turn.
    (re.compile(r"\bxai-[A-Za-z0-9_\-]{20,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\bAIza[A-Za-z0-9_\-]{30,}\b"), "[REDACTED_KEY]"),
    # Tokens for the connectors Chitragupta asks the user to paste, which is
    # exactly where a user is most likely to have one lying around in text.
    (re.compile(r"\bntn_[A-Za-z0-9]{20,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\blin_api_[A-Za-z0-9]{20,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}\b"), "Bearer [REDACTED_TOKEN]"),
    (re.compile(r"\bey[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{6,}\b"),
     "[REDACTED_JWT]"),
    # 13-19 digit card-like / account numbers (allow spaces or dashes)
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[REDACTED_ACCOUNT]"),
    # AWS Access Key ID (AKIA...)
    (re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b"), "[REDACTED_KEY]"),
    # Private keys
    (re.compile(r"-----BEGIN\s+(?:[A-Z\s]+)?PRIVATE\s+KEY-----[\s\S]*?-----END\s+(?:[A-Z\s]+)?PRIVATE\s+KEY-----", re.I),
     "[REDACTED_PRIVATE_KEY]"),
    # Database connection strings with credentials (postgres://user:pass@host)
    (re.compile(r"\b(?:postgres|postgresql|mysql|mongodb(?:\+srv)?|redis)://[^\s:@/]+:[^\s:@/]+@[^\s/]+", re.I),
     "[REDACTED_DB_URL]"),
    # generic "token/secret/api key is ..." disclosures & AWS secret keys
    (re.compile(r"\b(?:aws_secret_access_key|aws_access_key_id|secret[\s_-]?access[\s_-]?key|api[\s_-]?key|secret[\s_-]?key|secret|access[\s_-]?token|client[\s_-]?secret)\b"
                r"\s*(?:is|:|=)\s*\S+", re.I), "[REDACTED_SECRET]"),
]

# If a chunk is *dominated* by a credential context we drop it entirely rather
# than persist a redacted husk.
_HARD_DROP = re.compile(
    r"\b(reset your password|verify your account|confirm your email address|"
    r"your login code|do not share this code)\b", re.I)


def redact(text: str) -> str:
    """Return `text` with secrets masked."""
    if not text:
        return text
    out = text
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    return out


def is_sensitive(text: str) -> bool:
    """True if the text is a credential/notification we should not curate at all."""
    return bool(_HARD_DROP.search(text or ""))

