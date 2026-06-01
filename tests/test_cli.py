import pytest

from code_review_bot.cli import build_parser


def test_requires_cr_id() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_accepts_cr_id() -> None:
    args = build_parser().parse_args(["--cr-id", "42"])
    assert args.cr_id == "42"


def test_rejects_unknown_flag() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--project", "12", "--cr-id", "5"])


def test_verbose_flag() -> None:
    args = build_parser().parse_args(["--cr-id", "5", "--verbose"])
    assert args.verbose is True


def test_debug_flag() -> None:
    args = build_parser().parse_args(["--cr-id", "5", "--debug"])
    assert args.debug is True


def test_debug_output_dir() -> None:
    args = build_parser().parse_args(["--cr-id", "5", "--debug-output-dir", "/tmp/out"])
    assert args.debug_output_dir == "/tmp/out"
