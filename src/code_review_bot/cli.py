"""Entry point: parse CLI arguments and dispatch review runs."""

import argparse
import asyncio
import logging

from code_review_bot.config import Settings, get_settings
from code_review_bot.logging_config import configure_logging
from code_review_bot.review.orchestrator import ReviewOrchestrator

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="code-review-bot",
        description="Run AI-powered code review on a merge/pull request.",
    )
    parser.add_argument(
        "--cr-id",
        dest="cr_id",
        required=True,
        help="Change-request ID (GitLab MR IID or pull request number).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Log at DEBUG level.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Write review output to a Markdown file instead of posting to the platform.",
    )
    parser.add_argument(
        "--debug-output-dir",
        default="",
        dest="debug_output_dir",
        help=(
            "Directory for debug Markdown output "
            "(overrides the derived <LOG_DIR>/debug-reports from .env)."
        ),
    )
    return parser


async def _run_review(cr_id: str, settings: Settings, debug_output_dir: str = "") -> None:
    orchestrator = ReviewOrchestrator.from_settings(settings, debug_output_dir=debug_output_dir)
    try:
        outcome = await orchestrator.review_change_request(cr_id)
        if outcome.report_path:
            logger.info("Report: %s", outcome.report_path)
    finally:
        await orchestrator.adapter.aclose()


def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()
    log_level = (
        logging.DEBUG
        if args.verbose
        else getattr(logging, settings.log_level.upper(), logging.INFO)
    )
    configure_logging(log_level)

    debug_dir = args.debug_output_dir or settings.debug_review_output_dir if args.debug else ""
    asyncio.run(_run_review(cr_id=args.cr_id, settings=settings, debug_output_dir=debug_dir))
