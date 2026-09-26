"""A connector a user described in a form cannot be pointed at this Mac.

`custom_api.py` checked one thing about the address it was given:

    if not url.lower().startswith(("http://", "https://")):

That closes `file://` and `ftp://` and is correct. What it leaves open is the
whole of this machine — `http://127.0.0.1:<port>/api/brain/reset` is an
`http://` URL, and the port the desktop app binds is discoverable. The origin
guard in `api/security.py` does not help: it stops a *browser* reaching us
cross-origin, and this request does not come from a browser. It comes from us.

The line drawn is narrower than "block private addresses", and the narrowness
is deliberate: a user pointing a connector at the NAS in their house is doing
exactly what a local-first app is for.
"""
from __future__ import annotations

import pytest

from chitragupta.connectors.outbound import UnsafeAddressError, check_url


@pytest.fixture(autouse=True)
def _no_real_dns(monkeypatch):
    """Resolve names from a table, so these tests never touch a resolver.

    A test that depends on what `example.com` resolves to today is a test that
    fails on an aeroplane.
    """
    import socket

    table = {
        "api.example.com": ["93.184.216.34"],
        "nas.local": ["192.168.1.40"],
        # The shape that makes a literal-only check useless: a name the user
        # does not control, pointing at loopback.
        "localtest.me": ["127.0.0.1"],
        "metadata.example": ["169.254.169.254"],
    }

    def fake(host, *a, **kw):
        import ipaddress

        try:
            # The real `getaddrinfo` resolves a literal to itself. Modelled,
            # because otherwise every literal looks unresolvable and the
            # "refuse what we cannot look up" rule swallows the LAN case this
            # module deliberately permits.
            ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            pass
        else:
            return [(2, 1, 6, "", (host.strip("[]"), 0))]
        found = table.get(host)
        if found is None:
            raise OSError(f"cannot resolve {host}")
        return [(2, 1, 6, "", (address, 0)) for address in found]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


# ── what is allowed ───────────────────────────────────────────────────────


def test_an_ordinary_https_api_is_allowed():
    assert check_url("https://api.example.com/v1/items")


def test_plaintext_on_the_local_network_is_allowed():
    """A NAS in the user's house is what a local-first app is for. Refusing it
    would be choosing a threat model this product does not have."""
    assert check_url("http://nas.local:8080/api")


def test_a_private_address_literal_is_allowed():
    assert check_url("http://192.168.1.40:8080/api")


# ── what is refused ───────────────────────────────────────────────────────


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8765/api/brain/reset",
    "http://localhost:8765/api",
    "http://[::1]:8765/api",
    "http://0.0.0.0:8765/api",
])
def test_this_machine_is_refused(url):
    with pytest.raises(UnsafeAddressError):
        check_url(url)


def test_link_local_is_refused():
    """169.254.0.0/16 is where cloud metadata services live, and nothing a
    user means by "my API" is there."""
    with pytest.raises(UnsafeAddressError):
        check_url("http://169.254.169.254/latest/meta-data/")


def test_a_name_that_resolves_onto_this_machine_is_refused():
    """The reason names are resolved rather than only literals checked.
    `browser/origins.py` records the same class of bypass: `0x7f.0.0.1` is
    127.0.0.1 to a great many resolvers while Python's parser rejects it."""
    with pytest.raises(UnsafeAddressError):
        check_url("http://localtest.me/api")


def test_a_name_that_resolves_to_link_local_is_refused():
    with pytest.raises(UnsafeAddressError):
        check_url("http://metadata.example/latest/")


def test_an_ipv6_mapped_loopback_is_refused():
    """`::ffff:127.0.0.1` is loopback wearing a v6 hat, and `is_loopback` on
    the mapped form is False."""
    with pytest.raises(UnsafeAddressError):
        check_url("http://[::ffff:127.0.0.1]:8765/api")


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/x",
    "gopher://example.com/",
    "",
    "not a url",
])
def test_only_http_addresses_are_fetched(url):
    """`urllib` supports the others, which would let a mistaken or hostile
    config read local files."""
    with pytest.raises(UnsafeAddressError):
        check_url(url)


def test_plaintext_is_refused_where_it_reaches_the_internet():
    """A remote MCP server is a vendor across the network, where plaintext
    means readable and modifiable in flight."""
    with pytest.raises(UnsafeAddressError):
        check_url("http://api.example.com/mcp", allow_plaintext=False)

    assert check_url("https://api.example.com/mcp", allow_plaintext=False)


def test_a_name_that_cannot_be_looked_up_is_allowed_through():
    """The one residual risk this module accepts on purpose.

    Refusing it was tried and reverted: it turns every address check into a
    network call — so the app stops working offline and the suite depends on a
    live resolver — and it does not close the rebinding window anyway, since an
    attacker who can change what a name resolves to can equally make it resolve
    *safely* at check time. The request fails with a proper network error when
    the name is simply wrong. `docs/CONNECTOR-PLATFORM.md` §7.
    """
    assert check_url("https://nowhere.invalid/api")


def test_localhost_is_refused_without_asking_a_resolver():
    """The one name every machine agrees points at itself. A resolver that is
    slow, absent or lying is not something to depend on for it."""
    with pytest.raises(UnsafeAddressError):
        check_url("http://localhost:8765/api")
    with pytest.raises(UnsafeAddressError):
        check_url("http://anything.localhost:8765/api")


def test_an_address_with_no_host_is_refused():
    with pytest.raises(UnsafeAddressError):
        check_url("https:///just/a/path")


def test_an_impossible_port_is_a_refusal_not_a_crash():
    """`.port` raises rather than returning None, and an exception escaping
    here is a crashed sync instead of a refused one."""
    with pytest.raises(UnsafeAddressError):
        check_url("https://api.example.com:99999/x")


# ── the refusal is something a person can act on ──────────────────────────


def test_the_refusal_names_what_the_user_can_change():
    with pytest.raises(UnsafeAddressError) as caught:
        check_url("http://127.0.0.1:8765/api")

    message = str(caught.value)
    assert "127.0.0.1" not in message, "never surface an internal"
    assert "Mac" in message


# ── the connector uses it ─────────────────────────────────────────────────


def test_a_custom_app_cannot_be_pointed_at_this_machine():
    """The check is applied where the URL is built, so every page of a
    paginated read is checked and not only the first."""
    from chitragupta.connectors.custom_api import CustomAPIConnector

    connector = CustomAPIConnector({
        "id": "sneaky", "name": "Sneaky", "base_url": "http://127.0.0.1:8765",
        "endpoint": "/api/brain/reset", "auth_type": "none"})

    with pytest.raises(UnsafeAddressError):
        connector._url()


def test_a_page_marker_cannot_redirect_a_custom_app_onto_this_machine():
    """The cursor comes back from the provider, so it is exactly the field an
    attacker-controlled response would use."""
    from chitragupta.connectors.custom_api import CustomAPIConnector

    connector = CustomAPIConnector({
        "id": "app", "name": "App", "base_url": "https://api.example.com",
        "endpoint": "/items", "auth_type": "none"})

    with pytest.raises(UnsafeAddressError):
        connector._url("http://127.0.0.1:8765/api/brain/reset")


def test_a_custom_app_sync_reports_a_refusal_rather_than_crashing():
    from chitragupta.connectors.custom_api import CustomAPIConnector

    connector = CustomAPIConnector({
        "id": "sneaky", "name": "Sneaky", "base_url": "http://127.0.0.1:8765",
        "endpoint": "/api", "auth_type": "none"})

    result = connector.sync()

    assert result.errors
    assert result.added == 0
