"""Attached images: what gets in, what gets refused, and what goes on the wire.

This is the change CLAUDE.md's "a change that spans two layers" rule is about.
The contract is:

    web/     data URL  ──▶  api/     ChatIn.images[]
                        ──▶  agents/  run_turn(images=…)
                        ──▶  models/  Message.images
                        ──▶  the provider's own wire shape

Four layers, and each one passes its own tests while being wrong about the
next. The test that matters most here is the last one: that an image handed to
the API actually reaches the provider payload. A frontend that collects images
and a provider that can serialise them are individually correct and together
useless if nothing joins them.
"""
import base64

import pytest

from chitragupta.models.base import LLMProvider, Message
from chitragupta.models.images import (
    MAX_BASE64_BYTES,
    MAX_IMAGES,
    ImageError,
    ImageInput,
    parse_data_url,
    parse_many,
    refusal_for,
)

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"payload" * 8).decode()
URL = f"data:image/png;base64,{PNG}"


# ── the door in ───────────────────────────────────────────────────────────
def test_a_data_url_becomes_an_image():
    img = parse_data_url(URL, name="shot.png")
    assert img.media_type == "image/png"
    assert img.data == PNG          # base64 only — never the data: prefix
    assert img.name == "shot.png"
    assert img.as_data_url() == URL


@pytest.mark.parametrize("bad,expect", [
    ("data:image/svg+xml;base64," + PNG, "aren't supported"),
    ("data:image/tiff;base64," + PNG, "aren't supported"),
    ("data:image/png;base64,!!!not-base64!!!", "corrupted"),
    ("https://example.com/cat.png", "isn't an image"),
    ("", "isn't an image"),
])
def test_what_cannot_come_in_says_why(bad, expect):
    with pytest.raises(ImageError) as e:
        parse_data_url(bad)
    assert expect in str(e.value)


def test_svg_is_refused_because_it_is_a_script_vector():
    """Not an oversight: SVG can carry script, and no provider takes it."""
    with pytest.raises(ImageError):
        parse_data_url("data:image/svg+xml;base64," + PNG)


def test_an_oversized_image_is_refused_before_it_is_decoded():
    """The cap has to be checked on what ARRIVED. Decoding a hostile payload to
    find out how big it is, is the thing the cap exists to prevent."""
    huge = "A" * (MAX_BASE64_BYTES + 4)
    with pytest.raises(ImageError) as e:
        parse_data_url(f"data:image/png;base64,{huge}")
    assert "too large" in str(e.value)


def test_the_whole_set_is_refused_before_any_of_it():
    with pytest.raises(ImageError) as e:
        parse_many([URL] * (MAX_IMAGES + 1))
    assert str(MAX_IMAGES) in str(e.value)


def test_no_images_is_not_an_error():
    assert parse_many([]) == []


# ── the two refusals, which have different fixes ──────────────────────────
class _CLIBackend(LLMProvider):
    name = "cursor"
    display_name = "Cursor"
    # default supports_images is False


class _ApiBackend(LLMProvider):
    name = "openai"
    display_name = "OpenAI"
    supports_images = True


def test_a_backend_that_cannot_carry_an_image_says_so():
    """Changing model will not help here, so the message must not suggest it."""
    why = refusal_for([ImageInput("image/png", PNG)], _CLIBackend(), "cursor", "auto")
    assert why and "command-line tool" in why
    assert "Switch to a provider" in why


def test_a_provider_that_can_carry_images_is_not_refused_for_transport():
    why = refusal_for([ImageInput("image/png", PNG)], _ApiBackend(), "openai", None)
    assert why is None


def test_no_images_is_never_refused():
    assert refusal_for([], _CLIBackend(), "cursor", "auto") is None


def test_an_unknown_model_is_not_treated_as_a_no(monkeypatch):
    """Discovery failing is not the same as the model saying no. Refusing on a
    failed lookup invents a limitation the user does not have."""
    import chitragupta.models.images as mod
    monkeypatch.setattr(mod, "_model_sees_images", lambda *a: None)
    assert refusal_for([ImageInput("image/png", PNG)], _ApiBackend(),
                       "openai", "who-knows") is None


def test_a_model_without_vision_is_refused_and_offered_alternatives(monkeypatch):
    import chitragupta.models.images as mod
    monkeypatch.setattr(mod, "_model_sees_images", lambda *a: False)
    monkeypatch.setattr(mod, "_vision_alternatives", lambda *a, **k: ["GPT-5.5"])
    why = refusal_for([ImageInput("image/png", PNG)], _ApiBackend(), "openai", "text-only-1")
    assert "can't read images" in why
    assert "GPT-5.5" in why, "a refusal with no way forward is a dead end"


# ── the wire ──────────────────────────────────────────────────────────────
def test_anthropic_sends_a_base64_image_block():
    from chitragupta.models.anthropic import AnthropicProvider
    img = ImageInput("image/png", PNG)
    _system, msgs, _stable = AnthropicProvider(api_key="sk-test")._to_blocks(
        [Message(role="user", content="what is this?", images=[img])])
    blocks = msgs[0]["content"]
    assert blocks[0] == {"type": "image", "source": {
        "type": "base64", "media_type": "image/png", "data": PNG}}
    assert blocks[1] == {"type": "text", "text": "what is this?"}


def test_openai_sends_an_image_url_part():
    from chitragupta.models.openai_compat import OpenAICompatProvider
    img = ImageInput("image/png", PNG)
    out = OpenAICompatProvider()._to_openai(
        [Message(role="user", content="what is this?", images=[img])])
    parts = out[0]["content"]
    assert parts[0] == {"type": "image_url", "image_url": {"url": URL}}
    assert parts[1] == {"type": "text", "text": "what is this?"}


@pytest.mark.parametrize("provider_factory", [
    lambda: __import__("chitragupta.models.openai_compat", fromlist=["x"]).OpenAICompatProvider(),
    lambda: __import__("chitragupta.models.anthropic", fromlist=["x"]).AnthropicProvider(api_key="sk-test"),
])
def test_a_text_only_turn_is_byte_for_byte_what_it_always_was(provider_factory):
    """The whole point of keeping `content` a string and adding `images`
    beside it: a turn with no image must not change shape at all."""
    p = provider_factory()
    msgs = [Message(role="user", content="hello")]
    if hasattr(p, "_to_openai"):
        assert p._to_openai(msgs) == [{"role": "user", "content": "hello"}]
    else:
        _s, out, _stable = p._to_blocks(msgs)
        assert out == [{"role": "user", "content": "hello"}]


# ── transport capability, declared rather than guessed ────────────────────
def test_the_vendor_clis_do_not_claim_to_take_images():
    """They take a prompt on argv. There is nowhere for an image to go."""
    from chitragupta.models.claude_code import ClaudeCodeProvider
    from chitragupta.models.cursor import CursorProvider
    from chitragupta.models.grok_cli import GrokCliProvider
    for cls in (ClaudeCodeProvider, CursorProvider, GrokCliProvider):
        assert cls.supports_images is False, cls.__name__


def test_anthropic_only_claims_images_when_it_has_an_api_key():
    """Without a key it delegates to the Claude CLI, which cannot take one."""
    from chitragupta.models.anthropic import AnthropicProvider
    assert AnthropicProvider(api_key="sk-test").supports_images is True
    assert AnthropicProvider(api_key="").supports_images is False


# ── the join: does an image handed to the API reach the provider? ─────────
# Everything above can pass while nothing connects the layers. This is the
# test CLAUDE.md's two-layer rule asks for: it fails if either side ships
# without the other.
@pytest.fixture()
def captured_wire(monkeypatch):
    """Run a real turn through the real route, capturing what the provider got.

    Every chat() call is recorded, not just the last: the canonical brain calls
    the same provider to learn from the conversation after the turn, so keeping
    only the most recent call captures the extractor and not the turn.
    """
    seen: dict = {"calls": []}

    class _Recorder(LLMProvider):
        name = "openai"
        display_name = "OpenAI"
        model = "gpt-vision-test"
        supports_images = True

        def is_ready(self):
            return True, ""

        def chat(self, messages, *, tools=None, temperature=0.7, max_tokens=1500):
            from chitragupta.models.base import ChatResult
            seen["calls"].append(messages)
            return ChatResult(text="I see it.")

    import chitragupta.agents.runtime as runtime
    monkeypatch.setattr(runtime, "get_provider", lambda *a, **k: _Recorder())
    monkeypatch.setattr(runtime, "resolve_usable_model",
                        lambda p, m: (m or "gpt-vision-test", None))
    return seen


def test_an_image_posted_to_the_api_reaches_the_provider(captured_wire):
    from fastapi.testclient import TestClient

    from chitragupta.api.app import app

    r = TestClient(app).post("/api/agents/inbox/chat", json={
        "message": "what is this?",
        "images": [{"data_url": URL, "name": "shot.png"}],
    })
    assert r.status_code == 200, r.text

    assert captured_wire["calls"], "the turn never reached the provider at all"
    carried = [img for messages in captured_wire["calls"]
               for m in messages if m.role == "user" for img in m.images]
    assert len(carried) == 1, (
        "the image did not survive the trip from ChatIn to Message — one of "
        "the four layers is dropping it")
    assert carried[0].media_type == "image/png"
    assert carried[0].data == PNG


def test_an_image_on_its_own_is_a_complete_question(captured_wire):
    """"What is this?" is the image. An empty message with an attachment must
    not be rejected as an empty message."""
    from fastapi.testclient import TestClient

    from chitragupta.api.app import app

    r = TestClient(app).post("/api/agents/inbox/chat", json={
        "message": "", "images": [{"data_url": URL}],
    })
    assert r.status_code == 200, r.text


def test_a_turn_with_nothing_at_all_is_still_rejected():
    from fastapi.testclient import TestClient

    from chitragupta.api.app import app

    r = TestClient(app).post("/api/agents/inbox/chat", json={"message": ""})
    assert r.status_code == 422


def test_a_bad_attachment_is_refused_at_the_boundary_with_the_reason():
    from fastapi.testclient import TestClient

    from chitragupta.api.app import app

    r = TestClient(app).post("/api/agents/inbox/chat", json={
        "message": "look", "images": [{"data_url": "data:image/svg+xml;base64," + PNG}],
    })
    assert r.status_code == 422
    assert "aren't supported" in r.text


def test_the_refusal_happens_before_the_model_is_called(monkeypatch):
    """A model we already know cannot see must not be billed for finding out."""
    called = {"n": 0}

    class _Blind(LLMProvider):
        name = "cursor"
        display_name = "Cursor"
        model = "auto"
        supports_images = False           # a CLI backend

        def is_ready(self):
            return True, ""

        def chat(self, messages, **kw):
            called["n"] += 1
            from chitragupta.models.base import ChatResult
            return ChatResult(text="should never happen")

    import chitragupta.agents.runtime as runtime
    monkeypatch.setattr(runtime, "get_provider", lambda *a, **k: _Blind())
    monkeypatch.setattr(runtime, "resolve_usable_model", lambda p, m: (m, None))

    from chitragupta.agents.runtime import run_turn
    from chitragupta.models.images import parse_data_url
    result = run_turn("inbox", "what is this?", images=[parse_data_url(URL)])

    assert called["n"] == 0, "the model was called despite a known refusal"
    assert "can't accept images" in result.reply
