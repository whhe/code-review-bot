import logging
from pathlib import Path
from typing import cast

from code_review_bot.agent.factory import build_coding_agent
from code_review_bot.config import Settings
from code_review_bot.logging_config import (
    ReviewLogSession,
    attach_review_session_logging,
    detach_review_session_logging,
)
from code_review_bot.platforms.models import ChangeRequest
from code_review_bot.platforms.protocol import PlatformAdapter, ReviewBodyApprovalAdapter
from code_review_bot.repo.manager import RepoManager
from code_review_bot.review.context import BotMetadata, extract_metadata
from code_review_bot.review.file_filter import FileFilter
from code_review_bot.review.models import ReviewOutcome, ReviewTaskContext
from code_review_bot.review.publish.debug import DebugMarkdownPublisher
from code_review_bot.review.publish.platform import PlatformPublisher
from code_review_bot.review.publish.protocol import ReviewPublisher
from code_review_bot.review.runner import CodingAgentReviewRunner
from code_review_bot.skill.loader import load_skill
from code_review_bot.skill.protocol import Finding, RuntimeMetadata

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
        self._platform_publish = isinstance(publisher, PlatformPublisher)
        self._refresh_inline_threads = not isinstance(publisher, DebugMarkdownPublisher)

    @classmethod
    def from_settings(cls, settings: Settings, debug_output_dir: str = "") -> "ReviewOrchestrator":
        from code_review_bot.platforms.factory import build_platform_adapter

        adapter = build_platform_adapter(settings)
        repo_manager = RepoManager(
            clone_url=settings.git_repo_url,
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
            skill = load_skill(self.skill_path)
            previous_metadata = extract_metadata(
                notes,
                skill_name=skill.name,
                skill_version=skill.version,
            )

            source_sha = cr.diff_refs.get("head_sha") or cr.head_sha
            target_sha = cr.diff_refs.get("start_sha") or cr.diff_refs.get("base_sha", "")
            review_workspace = await self.repo_manager.make_review_workspace(
                cr.source_branch,
                cr.target_branch,
                head_repo_url=cr.head_repo_url,
                source_sha=source_sha,
                target_sha=target_sha,
            )
            logger.info("Local review workspace ready at %s", review_workspace)

            previous_unlocated_findings = _matching_unlocated_findings(
                previous_metadata,
                skill.name,
                skill.version,
            )
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
                previous_unlocated_findings=previous_unlocated_findings,
            )

            logger.info("Running skill name=%s version=%s", skill.name, skill.version)
            agent = build_coding_agent(self.settings, review_workspace / "source")
            runner = CodingAgentReviewRunner(
                agent,
                agent_type=self.settings.acp_agent_type,
                configured_model=self.settings.review_model_name,
            )
            result = await runner.review(skill, task_context)
            if self._refresh_inline_threads:
                latest_notes = await self.adapter.list_notes(resolved_ref, cr_id)
                latest_threads = await self.adapter.list_inline_threads(resolved_ref, cr_id)
                latest_cr = await self.adapter.fetch_change_request(resolved_ref, cr_id)
                _ensure_unchanged_review_revision(cr, latest_cr)
                prompt_context_changed = _review_prompt_context_changed(cr, latest_cr)
                cr = latest_cr
                latest_metadata = extract_metadata(
                    latest_notes,
                    skill_name=skill.name,
                    skill_version=skill.version,
                )
                latest_unlocated_findings = _matching_unlocated_findings(
                    latest_metadata,
                    skill.name,
                    skill.version,
                )
                if (
                    prompt_context_changed
                    or latest_threads != task_context.inline_threads
                    or latest_unlocated_findings != task_context.previous_unlocated_findings
                ):
                    logger.info(
                        "Prior review context changed during review; rerunning with latest state"
                    )
                    task_context = task_context.model_copy(
                        update={
                            "change_request": cr,
                            "source_branch": cr.source_branch,
                            "target_branch": cr.target_branch,
                            "inline_threads": latest_threads,
                            "previous_head_sha": (
                                latest_metadata.head_sha if latest_metadata is not None else ""
                            ),
                            "previous_unlocated_findings": latest_unlocated_findings,
                        }
                    )
                    stale_runtime = result.runtime
                    result = await runner.review(skill, task_context)
                    combined_runtime = _combine_runtime_metadata(stale_runtime, result.runtime)
                    if combined_runtime is not None:
                        result = result.with_runtime(combined_runtime)
                    final_notes = await self.adapter.list_notes(resolved_ref, cr_id)
                    final_threads = await self.adapter.list_inline_threads(resolved_ref, cr_id)
                    final_cr = await self.adapter.fetch_change_request(resolved_ref, cr_id)
                    _ensure_unchanged_review_revision(cr, final_cr)
                    prompt_context_changed = _review_prompt_context_changed(cr, final_cr)
                    cr = final_cr
                    final_metadata = extract_metadata(
                        final_notes,
                        skill_name=skill.name,
                        skill_version=skill.version,
                    )
                    final_unlocated_findings = _matching_unlocated_findings(
                        final_metadata,
                        skill.name,
                        skill.version,
                    )
                    if (
                        prompt_context_changed
                        or final_threads != task_context.inline_threads
                        or final_unlocated_findings != task_context.previous_unlocated_findings
                    ):
                        raise RuntimeError(
                            "Prior review context changed during review; refusing to publish "
                            "stale findings"
                        )
                    notes = final_notes
                    previous_metadata = final_metadata
                else:
                    notes = latest_notes
                    previous_metadata = latest_metadata

            file_filter = FileFilter(task_context.excluded_patterns, task_context.included_patterns)
            result.findings, file_excluded = file_filter.filter_findings(result.findings)
            if file_excluded:
                logger.info("File filter excluded %s findings", file_excluded)

            logger.info("Skill done findings=%s", len(result.findings))

            consolidate_github_review = (
                self._platform_publish
                and self.adapter.platform_name == "github"
                and isinstance(self.adapter, ReviewBodyApprovalAdapter)
                and self.settings.auto_approve_on_clean_review
                and cr.is_open
                and not cr.draft
            )
            if self._platform_publish:
                platform_publisher = cast(PlatformPublisher, self.publisher)
                publish_result = result
                approval_count = self._approval_finding_count(publish_result.findings)
                try:
                    outcome = await platform_publisher.publish(
                        cr,
                        publish_result,
                        skill.name,
                        skill.version,
                        existing_notes=notes,
                        publish_summary=not consolidate_github_review,
                    )
                except Exception:
                    if approval_count:
                        await self._maybe_update_approval(cr, resolved_ref, approval_count)
                    raise
            else:
                publish_result = result
                outcome = await self.publisher.publish(
                    cr,
                    result,
                    skill.name,
                    skill.version,
                    existing_notes=notes,
                )
                approval_count = self._approval_finding_count(publish_result.findings)
            approved = await self._maybe_update_approval(
                cr,
                resolved_ref,
                approval_count,
                review_body=outcome.review_body if consolidate_github_review else "",
            )
            if consolidate_github_review and approved is None:
                await self.adapter.publish_summary(resolved_ref, cr.cr_id, outcome.review_body)
            outcome = outcome.model_copy(update={"approved": approved})
            logger.info(
                "Published summary=%r published=%s inline_comments=%s approved=%s",
                (outcome.summary or "")[:120],
                outcome.published,
                outcome.inline_comments,
                outcome.approved,
            )
            return outcome
        finally:
            if review_workspace is not None:
                self.repo_manager.cleanup_review_workspace(review_workspace)
            detach_review_session_logging(log_session)

    def _approval_finding_count(self, findings: list[Finding]) -> int:
        count = len(findings)
        if not self.settings.auto_approve_ignore_low_severity:
            return count
        non_low_count = sum(finding.severity != "low" for finding in findings)
        if non_low_count != count:
            logger.info(
                "Approval check: ignoring %s low-severity findings, effective_count=%s",
                count - non_low_count,
                non_low_count,
            )
        return non_low_count

    async def _resolve_project_ref(self) -> str:
        if self._resolved_project_ref is not None:
            return self._resolved_project_ref
        if not self.bound_project_path:
            raise ValueError("GIT_REPO_URL must contain a project path (e.g. group/project.git)")
        self._resolved_project_ref = await self.adapter.resolve_project_ref(self.bound_project_path)
        return self._resolved_project_ref

    async def _maybe_update_approval(
        self,
        cr: ChangeRequest,
        project_ref: str,
        new_findings_count: int,
        review_body: str = "",
    ) -> bool | None:
        if not self.settings.auto_approve_on_clean_review:
            return None
        if not self._platform_publish:
            return None
        if not cr.is_open or cr.draft:
            return None

        head_sha = cr.diff_refs.get("head_sha", cr.head_sha)
        try:
            if new_findings_count == 0:
                if not head_sha:
                    logger.warning(
                        "Skipping approval: missing head_sha project_ref=%s cr_id=%s",
                        project_ref,
                        cr.cr_id,
                    )
                    return None
                if review_body and isinstance(self.adapter, ReviewBodyApprovalAdapter):
                    await self.adapter.approve_change_request_with_body(
                        project_ref, cr.cr_id, head_sha, body=review_body
                    )
                else:
                    await self.adapter.approve_change_request(project_ref, cr.cr_id, head_sha)
                logger.info(
                    "Approved change request project_ref=%s cr_id=%s head_sha=%s",
                    project_ref,
                    cr.cr_id,
                    head_sha[:12] + "…" if len(head_sha) > 12 else head_sha,
                )
                return True
            if review_body and isinstance(self.adapter, ReviewBodyApprovalAdapter):
                await self.adapter.revoke_change_request_approval_with_body(
                    project_ref, cr.cr_id, head_sha, body=review_body
                )
            else:
                await self.adapter.revoke_change_request_approval(project_ref, cr.cr_id, head_sha)
            logger.info(
                "Revoked approval project_ref=%s cr_id=%s new_findings=%s",
                project_ref,
                cr.cr_id,
                new_findings_count,
            )
            return False
        except Exception:
            logger.warning(
                "Failed to update approval state project_ref=%s cr_id=%s",
                project_ref,
                cr.cr_id,
                exc_info=True,
            )
            return None


def _ensure_unchanged_review_revision(original: ChangeRequest, latest: ChangeRequest) -> None:
    if original.head_sha != latest.head_sha or original.diff_refs != latest.diff_refs:
        raise RuntimeError(
            "Change request revision changed during review; refusing to publish stale findings"
        )


def _review_prompt_context_changed(original: ChangeRequest, latest: ChangeRequest) -> bool:
    return any(
        getattr(original, field) != getattr(latest, field)
        for field in (
            "title",
            "description",
            "author",
            "source_branch",
            "target_branch",
            "state",
            "web_url",
        )
    )


def _combine_runtime_metadata(
    first: RuntimeMetadata | None,
    second: RuntimeMetadata | None,
) -> RuntimeMetadata | None:
    if first is None and second is None:
        return None

    def combine_label(attribute: str) -> str | None:
        values: list[str] = []
        for runtime in (first, second):
            if runtime is None:
                continue
            value = getattr(runtime, attribute)
            if isinstance(value, str) and value:
                values.append(value)
        unique = list(dict.fromkeys(values))
        if len(unique) == 1:
            return unique[0]
        if unique:
            return f"multiple {attribute}s used: {', '.join(unique)}"
        return None

    def combine_usage(attribute: str) -> int | None:
        if first is None or second is None:
            return None
        first_value = getattr(first, attribute)
        second_value = getattr(second, attribute)
        if first_value is None or second_value is None:
            return None
        return first_value + second_value

    return RuntimeMetadata(
        agent_type=combine_label("agent_type"),
        model=combine_label("model"),
        input_tokens=combine_usage("input_tokens"),
        output_tokens=combine_usage("output_tokens"),
        total_tokens=combine_usage("total_tokens"),
    )


def _matching_unlocated_findings(
    metadata: BotMetadata | None,
    skill_name: str,
    skill_version: str,
) -> list[Finding]:
    if metadata is None or metadata.skill != skill_name or metadata.version != skill_version:
        return []
    return list(metadata.unlocated_findings)
