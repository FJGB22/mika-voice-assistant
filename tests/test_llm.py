from types import SimpleNamespace

import pytest

from mika.llm import ask, timings, to_gemini_input


def test_to_gemini_input_maps_user_turn():
    history = [{"role": "user", "content": "Hello"}]

    result = to_gemini_input(history)

    assert result == [{"role": "user", "parts": [{"text": "Hello"}]}]


def test_to_gemini_input_maps_assistant_turn():
    history = [{"role": "assistant", "content": "Hi there!"}]

    result = to_gemini_input(history)

    assert result == [{"role": "model", "parts": [{"text": "Hi there!"}]}]


def test_to_gemini_input_rejects_unknown_role():
    history = [{"role": "alien", "content": "olleH"}]

    with pytest.raises(ValueError):
        to_gemini_input(history)


# --- ask: a fake client stands in for Gemini, so no network or key is needed ---


def chunk(text):
    # A streamed GenerateContentResponse; ask() only reads .text, which can be None.
    return SimpleNamespace(text=text)


class FakeClient:
    """Same shape as genai.Client for what ask() uses: models.generate_content_stream."""

    def __init__(self, chunks):
        self.calls = []

        def generate_content_stream(**kwargs):
            self.calls.append(kwargs)
            return iter(chunks)

        self.models = SimpleNamespace(generate_content_stream=generate_content_stream)


def test_ask_joins_the_text_and_skips_empty_chunks():
    client = FakeClient([chunk("Hello "), chunk(None), chunk("there."), chunk("")])

    assert ask([{"role": "user", "content": "Hi"}], client) == "Hello there."


def test_ask_sends_the_whole_history_in_geminis_format():
    history = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello."},
        {"role": "user", "content": "How are you?"},
    ]
    client = FakeClient([chunk("Fine.")])

    ask(history, client)

    # Gemini keeps no state between calls: every question carries the conversation.
    assert client.calls[0]["contents"] == to_gemini_input(history)


def test_ask_turns_function_calling_off():
    client = FakeClient([chunk("Hi.")])

    ask([{"role": "user", "content": "Hi"}], client)

    assert client.calls[0]["config"].automatic_function_calling.disable is True


def test_ask_prints_its_timings(capsys):
    client = FakeClient([chunk("Hi.")])

    ask([{"role": "user", "content": "Hi"}], client)

    assert "Gemini time: first event" in capsys.readouterr().out


def test_timings_names_a_missing_moment():
    # A reply with no text at all still prints a line instead of crashing on None.
    assert timings(0.5, None, 1.0) == "first event 0.50 s, first text none, done 1.00 s"
