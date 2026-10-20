from types import SimpleNamespace

import pytest

from mika.llm import ask, timings, to_gemini_input


def test_to_gemini_input_maps_user_turn():
    history = [{"role": "user", "content": "Hello"}]

    result = to_gemini_input(history)

    assert result == [
        {
            "type": "user_input",
            "content": [{"type": "text", "text": "Hello"}],
        }
    ]


def test_to_gemini_input_maps_assistant_turn():
    history = [{"role": "assistant", "content": "Hi there!"}]

    result = to_gemini_input(history)

    assert result == [
        {
            "type": "model_output",
            "content": [{"type": "text", "text": "Hi there!"}],
        }
    ]


def test_to_gemini_input_rejects_unknown_role():
    history = [{"role": "alien", "content": "olleH"}]

    with pytest.raises(ValueError):
        to_gemini_input(history)


# --- ask: a fake client stands in for Gemini, so no network or key is needed ---


def event(event_type, delta_type=None, text=None):
    delta = SimpleNamespace(type=delta_type, text=text)
    return SimpleNamespace(event_type=event_type, delta=delta)


class FakeClient:
    """Same shape as genai.Client for what ask() uses: client.interactions.create."""

    def __init__(self, events):
        self.interactions = SimpleNamespace(create=lambda **kwargs: iter(events))


def test_ask_joins_only_the_text_deltas():
    client = FakeClient(
        [
            event("interaction.start"),
            event("step.delta", "text", "Hello "),
            event("step.delta", "thought", "hmm"),
            event("step.delta", "text", "there."),
            event("interaction.complete"),
        ]
    )

    assert ask([{"role": "user", "content": "Hi"}], client) == "Hello there."


def test_ask_prints_its_timings(capsys):
    client = FakeClient([event("step.delta", "text", "Hi.")])

    ask([{"role": "user", "content": "Hi"}], client)

    assert "Gemini time: first event" in capsys.readouterr().out


def test_timings_names_a_missing_moment():
    # A reply with no text at all still prints a line instead of crashing on None.
    assert timings(0.5, None, 1.0) == "first event 0.50 s, first text none, done 1.00 s"
