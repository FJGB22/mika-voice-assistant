from mika.config import load_env


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
