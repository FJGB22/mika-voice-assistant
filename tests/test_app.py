import pytest

from mika.app import load_env, to_gemini_input


def test_load_env_reads_pairs(tmp_path):
    env_file = tmp_path / "test.env"
    env_file.write_text("A=one\nb=two\n", encoding="utf-8")

    result = load_env(str(env_file))

    assert result == {"A": "one", "b": "two"}


def test_load_env_skips_comments_and_empty_lines(tmp_path):
    env_file = tmp_path / "test.env"
    env_file.write_text(
        """
        # This is a comment
        A=one

        b=two
        """,
        encoding="utf-8",
    )
    result = load_env(str(env_file))

    assert result == {"A": "one", "b": "two"}


def test_load_env_keeps_equals_inside_value(tmp_path):
    env_file = tmp_path / "test.env"
    env_file.write_text("A=B=C\n", encoding="utf-8")

    result = load_env(str(env_file))

    assert result == {"A": "B=C"}


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
