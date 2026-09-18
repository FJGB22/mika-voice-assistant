import pytest

from mika.llm import to_gemini_input


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
