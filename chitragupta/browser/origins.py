"""Which sites an agent may reach, and what it may do there.

This is the consent boundary for the most dangerous capability in the app, and
it is the one file to read before changing anything in this package.

**Why the site, and not the action.** `agents/permissions.py` gates outbound
actions on a named recipient, which works because an email has a `to` field to
compare against a list. A browser has no `to` field: *"click this button"* says
nothing about whether the button reads *Save draft* or *Transfer £4,000*. So the
unit of consent is the **origin**, granted per capability, because
`https://linkedin.com · read` is a sentence a person can actually reason about
and *"let the agent use the web"* is not.

**The check happens here, not in the model.** A page that says *"now go to
attacker.example"* produces a refusal from the tool, not a decision from a model
that has just read a stranger's text. That is the entire defence against
injection walking an agent somewhere it was never allowed, and it only works
because no code path asks the model whether an origin is allowed.

**What a grant means, exactly.** The host the user granted, plus its
subdomains — the rule cookies use, and the rule a person means when they say
"linkedin.com" about a site served from `www.linkedin.com`. It never crosses
into a different registrable name: `notlinkedin.com` is not a subdomain of
`linkedin.com`, and string-suffix matching is the classic way to believe it is.

**HTTPS only.** An agent driven onto `http://` is an agent whose session and
page content are modifiable in flight by anything on the network path, and every
site worth granting has had TLS for a decade.

Everything here is a pure decision over stored state — no browser, no network —
so it is tested as the security boundary it is rather than through a driver.
"""
from __future__ import annotations

import ipaddress
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from ..config import get_settings
from ..log import get_logger

log = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS browser_origins (
    origin     TEXT PRIMARY KEY,       -- normalised: https://host, no port
    host       TEXT NOT NULL,          -- the matchable host, lowercase, IDNA
    may_read   INTEGER NOT NULL DEFAULT 1,
    may_act    INTEGER NOT NULL DEFAULT 0,
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""

#: The only scheme a grant can be made for. See the module docstring.
SCHEME = "https"

#: What a hostname may look like, after IDNA encoding: dot-separated LDH labels
#: ending in a suffix that starts with a letter and is not all digits.
#:
#: The final-label rule is doing the security work. `0x7f.0.0.1` is 127.0.0.1 to
#: a great many resolvers and is not an IP literal to Python's parser, so the
#: numeric-address check cannot see it — but its last label is `1`, and no
#: website has ever had a numeric TLD. Punycode suffixes (`xn--p1ai`) start with
#: letters and pass.
_HOSTNAME = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}")


def _conn() -> sqlite3.Connection:
    path = get_settings().home / "agents.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


class BadOriginError(ValueError):
    """A URL that cannot be reduced to an origin we would ever allow."""


def normalise(raw: str) -> str:
    """`https://host` for anything that can safely be reduced to one.

    Every comparison in this module runs on the output of this function, so each
    line below is load-bearing:

    * **The host comes from `urlsplit().hostname`**, never from the text between
      the scheme and the first slash. `https://amazon.com@evil.test/` has a host
      of `evil.test`, and reading it any other way is a credential-shaped trap
      that has been used against real software for twenty years.
    * **IDNA encoding is the homoglyph defence.** `аmazon.com` with a Cyrillic
      `а` encodes to `xn--mazon-3ve.com`, which does not equal `amazon.com` —
      so a grant for the real site cannot be satisfied by a lookalike an
      injected link named. Encoding makes them visibly different; comparing the
      raw Unicode would make them subtly the same.
    * **The default port is dropped and any other port is refused.** `:443` is
      the same origin; `:8443` is a different service on a host the user was
      thinking of as a website.
    * **A trailing dot is stripped.** `x.com.` is `x.com` to DNS, and would
      otherwise be a second, ungranted spelling of a granted site.
    """
    text = (raw or "").strip()
    if not text:
        raise BadOriginError("no address given")
    if "://" not in text:
        # A bare host is a convenience for the grant UI, never for navigation.
        text = f"{SCHEME}://{text}"

    parts = urlsplit(text)
    if parts.scheme.lower() != SCHEME:
        raise BadOriginError(
            f"only {SCHEME} addresses can be used — {parts.scheme or 'this'} "
            "traffic can be read and changed on the way")

    host = parts.hostname or ""
    if not host:
        raise BadOriginError("that address has no site name in it")
    host = host.rstrip(".").lower()
    if not host or "." not in host:
        # Rejects `localhost` and bare labels: a grant is for a public site, and
        # loopback is where *this app* lives.
        raise BadOriginError("that is not a website address")

    # **No IP literals, of any kind.** `https://127.0.0.1` passed the dot test
    # above, which would have made every service on this machine and this
    # network grantable — a router's admin page, a NAS, a printer, a cloud
    # metadata endpoint at 169.254.169.254 — to an agent that may hold a session
    # cookie for it and is reading a page a stranger wrote. A person granting "a
    # website" always means a name, so refusing the whole class costs nothing and
    # closes the entire category rather than a list of ranges someone has to
    # remember to keep current.
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass                                   # a name, which is what we want
    else:
        raise BadOriginError("a numeric address is not a website — use its name")

    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise BadOriginError("that site name cannot be read") from exc

    # **A real hostname, not merely a string with a dot in it.** Two things got
    # through before this existed, and the second is the serious one:
    #
    # * `\\evil.com` was accepted verbatim, because nothing checked which
    #   characters a host may contain.
    # * `0x7f.0.0.1` was accepted — and that is 127.0.0.1 to a great many
    #   resolvers. The `ipaddress` check above cannot see it, because Python's
    #   parser rejects hex octets while the wider world does not, so the whole
    #   IP-literal defence was one encoding away from being bypassed.
    #
    # Requiring the last label to look like a real suffix closes both, and
    # closes the encodings nobody has thought of yet: every website has an
    # alphabetic TLD and no numeric spelling of an address does.
    if not _HOSTNAME.fullmatch(host):
        raise BadOriginError("that is not a website address")

    try:
        port = parts.port
    except ValueError as exc:
        # `.port` raises rather than returning None for `:99999`, and an
        # exception escaping here is a crashed turn instead of a refused
        # navigation — this is called with whatever a page contained.
        raise BadOriginError("that address has a port that is not valid") from exc
    if port is not None and port != 443:
        raise BadOriginError(f"only the standard {SCHEME} port is supported")

    return f"{SCHEME}://{host}"


def host_of(url: str) -> str:
    """The matchable host of a URL. Raises `BadOriginError` like `normalise`."""
    return normalise(url).split("://", 1)[1]


def covers(granted_host: str, host: str) -> bool:
    """Does a grant for `granted_host` cover `host`?

    Exactly, or as a subdomain — and the subdomain test is on **label
    boundaries**. `endswith(".example.com")` is right; `endswith("example.com")`
    is the bug, because it also accepts `notexample.com`, which an injected link
    would be delighted to be.
    """
    if not granted_host or not host:
        return False
    return host == granted_host or host.endswith("." + granted_host)


@dataclass(frozen=True)
class Grant:
    origin: str
    host: str
    may_read: bool
    may_act: bool
    note: str = ""

    def as_dict(self) -> dict:
        return {"origin": self.origin, "host": self.host,
                "may_read": self.may_read, "may_act": self.may_act,
                "note": self.note}


def _row_to_grant(row: sqlite3.Row) -> Grant:
    return Grant(origin=row["origin"], host=row["host"],
                 may_read=bool(row["may_read"]), may_act=bool(row["may_act"]),
                 note=row["note"])


def list_grants() -> list[Grant]:
    rows = _conn().execute(
        "SELECT * FROM browser_origins ORDER BY host").fetchall()
    return [_row_to_grant(r) for r in rows]


def grant(url: str, *, may_read: bool = True, may_act: bool = False,
          note: str = "") -> Grant:
    """Allow an agent to reach this site. Raises `BadOriginError` on a bad address.

    `may_act` defaults to False and no code path in this package flips it: acting
    is a separate landing with an approval card, and a grant that quietly
    included it would make the read-only ship a write-capable one.
    """
    origin = normalise(url)
    host = origin.split("://", 1)[1]
    conn = _conn()
    conn.execute(
        "INSERT INTO browser_origins (origin,host,may_read,may_act,note,created_at) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(origin) DO UPDATE SET "
        "may_read=excluded.may_read, may_act=excluded.may_act, note=excluded.note",
        (origin, host, int(may_read), int(may_act), note,
         datetime.now(UTC).isoformat()))
    conn.commit()
    log.info("browser access granted for %s (read=%s act=%s)",
             origin, may_read, may_act)
    return Grant(origin, host, may_read, may_act, note)


def revoke(url: str) -> bool:
    """Withdraw a grant. Forgiving about the spelling the caller used."""
    try:
        origin = normalise(url)
    except BadOriginError:
        return False
    conn = _conn()
    cur = conn.execute("DELETE FROM browser_origins WHERE origin=?", (origin,))
    conn.commit()
    if cur.rowcount:
        log.info("browser access revoked for %s", origin)
    return cur.rowcount > 0


def matching_grant(url: str) -> Grant | None:
    """The grant that covers this URL, or None.

    The longest matching host wins, so a specific grant is never shadowed by a
    broader one it disagrees with — `mail.example.com` beats `example.com` when
    both are granted, which is what someone who added both meant.
    """
    try:
        host = host_of(url)
    except BadOriginError:
        return None
    best: Grant | None = None
    for candidate in list_grants():
        if covers(candidate.host, host) and (
                best is None or len(candidate.host) > len(best.host)):
            best = candidate
    return best


@dataclass(frozen=True)
class Verdict:
    """Whether a URL may be reached, and what to tell the user if not."""

    allowed: bool
    #: Present whenever the refusal is something a grant would fix, so the UI can
    #: offer that exact site rather than asking the user to type it.
    grantable: str | None = None
    reason: str = ""

    def as_dict(self) -> dict:
        return {"allowed": self.allowed, "grantable": self.grantable,
                "reason": self.reason}


def may_read(url: str) -> Verdict:
    """May an agent open and read this URL right now?

    The refusal text is what a person reads in a tool result, so it names the
    site and says what would change the answer. "Permission denied" describes our
    model of the problem and not theirs.
    """
    try:
        origin = normalise(url)
    except BadOriginError as exc:
        # Deliberately not grantable: there is no version of this address we
        # would accept, so offering a button would be offering a control that
        # cannot work.
        return Verdict(False, None, str(exc))

    found = matching_grant(origin)
    if found is None:
        return Verdict(False, origin,
                       f"{origin.split('://')[1]} is not a site you have allowed "
                       "agents to read.")
    if not found.may_read:
        return Verdict(False, origin,
                       f"{found.host} is on your list but reading is turned off.")
    return Verdict(True)


def may_act(url: str) -> Verdict:
    """May an agent *change* something at this URL?

    Checked at the tool, never by the model — the same rule `may_read` follows,
    and it matters more here. A page that says "now press Confirm" is a page
    arguing for its own permission, and the answer has to come from something
    that never read it.

    Acting requires reading, and not the other way round: a site turned off for
    reading cannot be acted on either, whatever its `may_act` column says.
    """
    read = may_read(url)
    if not read.allowed:
        return read
    found = matching_grant(url)
    if found is None or not found.may_act:
        host = found.host if found else url
        return Verdict(False, None,
                       f"Changing anything on {host} needs your approval.")
    return Verdict(True)
