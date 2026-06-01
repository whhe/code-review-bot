import logging
from pathlib import Path

from code_review_bot.agent.factory import build_coding_agent
from code_review_bot.config import Settings
from code_review_bot.logging_config import (
    ReviewLogSession,
    attach_review_session_logging,
    detach_review_session_logging,
)
from code_review_bot.platforms.protocol import PlatformAdapter
from code_review_bot.repo.manager import RepoManager
from code_review_bot.review.context import (
    compute_fingerprint,
    extract_metadata,
)
from code_review_bot.review.file_filter import FileFilter
from code_review_bot.review.models import ReviewOutcome, ReviewTaskContext
from code_review_bot.review.publish.debug import DebugMarkdownPublisher
from code_review_bot.review.publish.platform import PlatformPublisher
from code_review_bot.review.publish.protocol import ReviewPublisher
from code_review_bot.review.runner import CodingAgentReviewRunner
from code_review_bot.skill.loader import load_skill

logger = logging.getLogger(__name__)


class ReviewOrchestrator:
    """Coordinates a full review cycle: fetch CR, clone repo, run skill, publish results."""

    def __init__(
        self,
        adapter: PlatformAdapter,
        publisher: ReviewPublisher,
        skill_path: str,
        repo_manager: RepoManager,
        settings: Settings,
        review_session_log_dir: str = "logs/sessions",
        bound_project_path: str = "",
    ) -> None:
        self.adapter = adapter
        self.publisher = publisher
        self.skill_path = skill_path
        self.review_session_log_dir = review_session_log_dir
        self.repo_manager = repo_manager
        self.bound_project_path = bound_project_path
        self.settings = settings
        self._resolved_project_ref: str | None = None

    @classmethod
    def from_settings(cls, settings: Settings, debug_output_dir: str = "") -> "ReviewOrchestrator":
        from code_review_bot.platforms.factory import build_platform_adapter

        adapter = build_platform_adapter(settings)
        repo_manager = RepoManager(
            clone_url=settings.repo_clone_url,
            token=settings.git_repo_token,
            review_base_dir=settings.clone_base_dir,
            clone_depth=settings.clone_depth,
        )
        publisher: ReviewPublisher
        if debug_output_dir:
            publisher = DebugMarkdownPublisher(output_dir=debug_output_dir)
        else:
            publisher = PlatformPublisher(adapter)
        return cls(
            adapter=adapter,
            publisher=publisher,
            skill_path=settings.review_skill,
            review_session_log_dir=settings.review_session_log_dir,
            repo_manager=repo_manager,
            bound_project_path=settings.git_project_path,
            settings=settings,
        )

    async def review_change_request(self, cr_id: str) -> ReviewOutcome:
        resolved_ref = await self._resolve_project_ref()
        log_session: ReviewLogSession | None = attach_review_session_logging(
            resolved_ref,
            cr_id,
            relative_log_dir=self.review_session_log_dir,
        )
        review_workspace: Path | None = None
        try:
            logger.info("Start review project_ref=%s cr_id=%s", resolved_ref, cr_id)
            cr = await self.adapter.fetch_change_request(resolved_ref, cr_id)
            notes = await self.adapter.list_notes(resolved_ref, cr_id)
            inline_threads = await self.adapter.list_inline_threads(resolved_ref, cr_id)

            sha_display = cr.head_sha[:12] + "…" if len(cr.head_sha) > 12 else cr.head_sha
            logger.info(
                "Loaded CR title=%r head_sha=%s notes=%s inline_threads=%s",
                cr.title,
                sha_display or "(none)",
                len(notes),
                len(inline_threads),
            )
            previous_metadata = extract_metadata(notes)

            review_workspace = await self.repo_manager.make_review_workspace(
                cr.source_branch, cr.target_branch, head_repo_url=cr.head_repo_url
            )
            logger.info("Local review workspace ready at %s", review_workspace)

            skill = load_skill(self.skill_path)
            task_context = ReviewTaskContext(
                change_request=cr,
                workspace_path=str(review_workspace / "source"),
                source_branch=cr.source_branch,
                target_branch=cr.target_branch,
                base_sha=cr.diff_refs.get("base_sha", ""),
                start_sha=cr.diff_refs.get("start_sha", ""),
                head_sha=cr.diff_refs.get("head_sha", cr.head_sha),
                previous_head_sha=previous_metadata.head_sha if previous_metadata else "",
                output_language=self.settings.output_language,
                excluded_patterns=self.settings.review_exclude,
                included_patterns=self.settings.review_include,
                inline_threads=inline_threads,
            )

            logger.info("Running skill name=%s version=%s", skill.name, skill.version)
            agent = build_coding_agent(self.settings, review_workspace / "source")
            result = await CodingAgentReviewRunner(agent).review(skill, task_context)

            file_filter = FileFilter(task_context.excluded_patterns, task_context.included_patterns)
            result.findings, file_excluded = file_filter.filter_findings(result.findings)
            if file_excluded:
                logger.info("File filter excluded %s findings", file_excluded)

            skill_raw_findings = len(result.findings)
            existing_fingerprints = previous_metadata.fingerprints if previous_metadata else set()
            new_findings = []
            fingerprints = set(existing_fingerprints)
            for finding in result.findings:
                fingerprint = compute_fingerprint(skill.name, skill.version, finding)
                if fingerprint in existing_fingerprints:
                    continue
                fingerprints.add(fingerprint)
                new_findings.append(finding)
            result.findings = new_findings

            logger.info(
                "Skill done raw_findings=%s after_fingerprint_filter=%s",
                skill_raw_findings,
                len(new_findings),
            )

            outcome = await self.publisher.publish(
                cr,
                result,
                skill.name,
                skill.version,
                sorted(fingerprints),
                existing_notes=notes,
            )
            logger.info(
                "Published summary=%r published=%s inline_comments=%s",
                (outcome.summary or "")[:120],
                outcome.published,
                outcome.inline_comments,
            )
            return outcome
        finally:
            if review_workspace is not None:
                self.repo_manager.cleanup_review_workspace(review_workspace)
            detach_review_session_logging(log_session)

    async def _resolve_project_ref(self) -> str:
        if self._resolved_project_ref is not None:
            return self._resolved_project_ref
        if not self.bound_project_path:
            raise ValueError("GIT_REPO_URL must contain a project path (e.g. group/project.git)")
        self._resolved_project_ref = await self.adapter.resolve_project_ref(self.bound_project_path)
        return self._resolved_project_ref
