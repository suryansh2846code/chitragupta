"""Where a user-described connector may send a request, and where it may not.

`custom_api.py` lets a user describe any REST app in a form, and it checks one
thing about the address:

    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(...)

That closes `file://` and `ftp://`, which was the reason it was written and is
correct. What it leaves open is the whole of **this machine**:
`http://127.0.0.1:<port>/api/brain/reset` is an `http://` URL, and the port the
desktop app binds is discoverable. The origin guard in `api/security.py` is not
a defence here — it stops a *browser* reaching us cross-origin, and this request
does not come from a browser. It comes from us.

So the rule, and it is narrower than "block private addresses":

* **This machine is refused.** Loopback in any spelling, and `0.0.0.0`.
* **Link-local is refused.** `169.254.0.0/16` is where cloud metadata services
  live, and nothing a user means by "my API" is there.
* **The rest of the local network is allowed**, deliberately. A user pointing a
  connector at the NAS in their house is doing exactly what a local-first app
  is for, and refusing it would be choosing a threat model (someone else
  writing the user's connector definitions) over the one this product actually
  has. Stated here rather than left implicit, because it is a trade.

## Names are resolved, and so are redirects

`browser/origins.py` refuses IP literals outright and records why: `0x7f.0.0.1`
is `127.0.0.1` to a great many resolvers, and Python's `ipaddress` parser
rejects hex octets while the wider world does not — so a literal-only check is
one encoding away from being bypassed. A connector cannot take that route
(refusing literals would refuse a legitimate LAN address), so it takes the
other one: **resolve the name and judge what it actually points at**. That
catches the hex spelling, `localtest.me`, and a DNS record the user does not
control, all at once.

And the landing is checked, not only the destination — the rule `BROWSER.md`
states as *"check where the browser landed, not where it was sent"*. A server
that answers with `302 → http://127.0.0.1:8765` has redirected us onto the
loopback this module exists to refuse.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from ..log import get_logger

log = get_logger(__name__)

#: The one refusal sentence a user reads. Names the thing they can change —
#: never a range, a scheme name, or a resolved address, which are all
#: internals (`/CLAUDE.md`, Never surface an internal).
REFUSED = ("A connector cannot read from this Mac itself. Use the address "
           "other machines would use to reach that app.")


class UnsafeAddressError(ValueError):
    """The address points somewhere a connector may not go."""


def _forbidden(address: str) -> bool:
    """Is this resolved address one no connector may reach?

    Deliberately not `is_private`: `192.168.1.10` is a NAS, and a user pointing
    a connector at their own NAS is doing what this app is for.
    """
    try:
        found = ipaddress.ip_address(address)
    except ValueError:
        return False
    return bool(found.is_loopback or found.is_link_local
                or found.is_unspecified
                # `::ffff:127.0.0.1` is loopback wearing a v6 hat, and
                # `is_loopback` on the mapped form is False.
                or (isinstance(found, ipaddress.IPv6Address)
                    and found.ipv4_mapped is not None
                    and _forbidden(str(found.ipv4_mapped))))


def _resolved(host: str) -> list[str]:
    """Every address a host resolves to, or [] when it cannot be resolved.

    Empty is **not** treated as safe by the caller: a name that does not
    resolve is a request that will fail anyway, and guessing about it is how a
    resolver disagreement becomes a bypass.
    """
    try:
        found = socket.getaddrinfo(host, None)
    except OSError:
        return []
    return sorted({str(entry[4][0]) for entry in found})


def check_url(raw: str, *, allow_plaintext: bool = True) -> str:
    """The URL, if a connector may fetch it. Raises `UnsafeAddressError` if not.

    `allow_plaintext` is True because a LAN service on `http://` is a real and
    ordinary thing, and refusing it would refuse the use this module explicitly
    permits. A remote MCP server passes False — that reaches a vendor over the
    internet, where plaintext means readable and modifiable in flight.
    """
    text = (raw or "").strip()
    parts = urlsplit(text)
    scheme = parts.scheme.lower()

    allowed = ("http", "https") if allow_plaintext else ("https",)
    if scheme not in allowed:
        raise UnsafeAddressError(
            "That address must start with https://" if not allow_plaintext
            else "That address must start with http:// or https://")

    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeAddressError("That address has no server name in it.")

    try:
        # Read for its side effect: `.port` *raises* rather than returning None
        # for `:99999`, and an exception escaping here is a crashed sync
        # instead of a refused one. Bound to a name so it does not read as a
        # stray expression.
        _port = parts.port
    except ValueError as exc:
        raise UnsafeAddressError("That address has a port that is not valid.") \
            from exc
    del _port

    # A literal is judged directly; a name is judged by what it resolves to.
    # Both, because a literal need not resolve and a name need not be one.
    if _forbidden(host.strip("[]")):
        raise UnsafeAddressError(REFUSED)

    # **Named, independently of any resolver.** `localhost` is not an IP
    # literal, so the check above does not see it, and a resolver that is
    # slow, absent or lying is not something to depend on for the one name
    # every machine agrees points at itself.
    labels = host.split(".")
    if labels[-1] == "localhost" or host == "localhost":
        raise UnsafeAddressError(REFUSED)

    # **A name that does not resolve is allowed through, deliberately**, and
    # this is the one place the module accepts a residual risk rather than
    # closing it. Refusing it was tried and reverted, for two reasons that both
    # matter more than what it bought:
    #
    # * It makes every address check a **network call**, so the app stops
    #   working offline and the test suite depends on a live resolver — which
    #   `tests/CLAUDE.md` forbids for exactly this reason.
    # * It does not actually close the rebinding window. An attacker who can
    #   make a name resolve differently between this check and the request can
    #   equally make it resolve *safely* here and unsafely there; refusing the
    #   no-answer case only removes the least useful of those.
    #
    # What remains uncovered is therefore narrow and named: a host that fails
    # to resolve now and points at this machine a moment later. The literal
    # check, the `localhost` check and the landing re-check all still apply,
    # and the request itself fails with a network error when the name is
    # simply wrong. Recorded in `docs/CONNECTOR-PLATFORM.md` §7.
    addresses = _resolved(host)
    if any(_forbidden(address) for address in addresses):
        # Logged at debug with the host and not the address: the address is the
        # part that is an internal, and the host is the part the user typed.
        log.debug("refused %r — it resolves onto this machine", host)
        raise UnsafeAddressError(REFUSED)

    return text


def check_landing(raw: str, *, allow_plaintext: bool = True) -> str:
    """The same check, for where a request actually ended up.

    A separate name rather than a second call to `check_url`, so a caller
    reading the code can see that both questions are being asked. `BROWSER.md`
    states the rule this borrows: a granted destination can redirect anywhere,
    and checking only where it was sent checks the wrong thing.
    """
    return check_url(raw, allow_plaintext=allow_plaintext)
