import hashlib
from pathlib import Path

from code_review_bot.repo.manager import REVIEW_SOURCE_REF, REVIEW_TARGET_REF

_MAX_SKILL_MD_BYTES = 512_000
_MAX_DESCRIPTION_CHARS = 3000


def strip_openclaw_frontmatter(text: str) -> str:
    """Return SKILL.md body text, dropping any YAML frontmatter block."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text.strip()
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return text.strip()
    return "\n".join(lines[end + 1 :]).strip()


def content_version(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:8]


class FilesystemMarkdownSkill:
    """Review skill backed by a SKILL.md file on disk.

    The skill directory path is passed to the agent; the agent reads SKILL.md
    and any referenced files itself. The prompt contract is injected here, not
    the SKILL.md body, to keep the platform prompt small and let the agent
    load skill content progressively.
    """

    def __init__(self, skill_dir: Path, skill_key: str) -> None:
        self.skill_dir = skill_dir.resolve()
        self.name = skill_key
        self._reference_paths = _collect_reference_paths(self.skill_dir)
        skill_file = self.skill_dir / "SKILL.md"
        if not skill_file.is_file():
            raise ValueError(f"SKILL.md not found: {skill_file}")
        raw_bytes = skill_file.read_bytes()
        if len(raw_bytes) > _MAX_SKILL_MD_BYTES:
            raise ValueError(f"SKILL.md exceeds {_MAX_SKILL_MD_BYTES} bytes: {skill_file}")
        raw = raw_bytes.decode("utf-8")
        body = strip_openclaw_frontmatter(raw)
        if not body:
            raise ValueError(f"SKILL.md has empty instruction body: {skill_file}")
        self.version = content_version(body)

    @property
    def additional_directories(self) -> list[str]:
        return [str(self.skill_dir)]

    def build_prompt(self, context: object) -> str:
        reference_manifest = _format_reference_manifest(self._reference_paths)
        return build_review_prompt(context, str(self.skill_dir), reference_manifest)


class NativeKnowledgeSkill:
    """Review skill that relies on the agent's own knowledge.

    Used when REVIEW_SKILL is empty. No skill directory or URL is provided;
    the agent performs the review based on its built-in training.
    """

    name = "native"
    version = "0"

    @property
    def additional_directories(self) -> list[str]:
        return []

    def build_prompt(self, context: object) -> str:
        return build_review_prompt(context, "")


class RemoteUrlSkill:
    """Review skill referenced by a remote URL.

    No local materialisation is performed. The URL is passed verbatim to the
    coding agent, which fetches and reads the skill content on demand using its
    own tools (e.g. WebFetch, Bash curl). This means each review run incurs
    network I/O; for frequently-used skills, prefer a locally installed copy.
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self.name = _name_from_url(url)
        # Version is URL-derived, not content-derived. If the remote skill
        # content changes without a URL change, fingerprints from prior runs
        # remain valid and new findings may be incorrectly deduplicated.
        # To force a full re-review, change the URL (e.g. add ?v=2).
        self.version = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]

    @property
    def additional_directories(self) -> list[str]:
        return []

    def build_prompt(self, context: object) -> str:
        return build_review_prompt(context, self.url)


def _name_from_url(url: str) -> str:
    segment = url.rstrip("/").rsplit("/", 1)[-1]
    if segment.lower() == "skill.md":
        # URL points directly to the SKILL.md file; use the parent directory name
        segment = url.rstrip("/").rsplit("/", 2)[-2]
    return segment.removesuffix(".git") or "remote-skill"


def build_review_prompt(
    context: object,
    skill_ref: str,
    reference_manifest: str = "",
) -> str:
    """Assemble the full prompt for a code review run.

    skill_ref is an absolute local directory path, an http(s) URL, or an empty
    string (no skill — agent reviews using its own knowledge).

    context must have the following attributes:
        change_request  - ChangeRequest with title, cr_id, source_branch,
                          target_branch, description, author, state, web_url
        workspace_path  - str, absolute path to the checked-out target branch
        source_branch   - str
        target_branch   - str
        base_sha        - str
        start_sha       - str
        head_sha        - str
        previous_head_sha - str  (empty string if no previous review)
        resolved_discussions - list  (may be empty)
        output_language - str  (e.g. "english", "chinese")
    """
    cr = context.change_request  # type: ignore[attr-defined]
    output_language: str = getattr(context, "output_language", "english")
    previous_head = getattr(context, "previous_head_sha", "") or "none"

    workspace_path: str = context.workspace_path  # type: ignore[attr-defined]
    base_sha: str = context.base_sha  # type: ignore[attr-defined]
    head_sha: str = context.head_sha  # type: ignore[attr-defined]

    from code_review_bot.review.file_filter import FileFilter

    excluded: list[str] = getattr(context, "excluded_patterns", [])
    included: list[str] = getattr(context, "included_patterns", [])
    filter_section = FileFilter(excluded, included).prompt_section()

    if not skill_ref:
        skill_section = ""
    elif skill_ref.startswith(("http://", "https://")):
        skill_section = (
            "## Review skill\n"
            f"Skill URL: `{skill_ref}`\n"
            "Use your available tools to fetch and read the review methodology:\n"
            "- If the URL points to a `SKILL.md` file, fetch it directly.\n"
            "- If the URL points to a directory or repository, locate and fetch "
            "`SKILL.md` within it (e.g. try appending `/SKILL.md`, or browse the "
            "repository tree to find it).\n"
            "- SKILL.md may reference additional files. Fetch each one when the methodology "
            "instructs you to consult it — do not apply that step without reading the "
            "referenced file first. Skipping a required reference produces incomplete findings.\n"
            "Do NOT apply any output format from the skill — "
            "use the system output contract above.\n\n"
        )
    else:
        ref_section = f"\n\n{reference_manifest}" if reference_manifest else ""
        skill_section = (
            "## Review skill\n"
            f"Skill directory (local): `{skill_ref}`\n"
            "1. Read `SKILL.md` in that directory for the review methodology.\n"
            "2. SKILL.md may instruct you to consult files in the `references/` subdirectory. "
            "Treat each such instruction as a required step — do not apply that part of the "
            "methodology without first reading the referenced file. "
            "Skipping a required reference produces incomplete findings.\n"
            "3. Do NOT apply any output format from SKILL.md — "
            "use the system output contract above.\n"
            f"{ref_section}\n\n"
        )

    return (
        "# System output contract\n"
        "You are performing a read-only code review of a merge request. "
        "Reply with JSON only, matching schema SkillResult with fields summary (string) "
        "and findings (array). This output contract overrides any report, Markdown, table, "
        "or heading format requested by the review methodology below. "
        f"Write summary and every finding description and reason in {output_language}. "
        "Keep file paths, identifiers, API names, and anchor_text exact and untranslated.\n\n"
        "summary must be concise (<= 200 characters), describe only the overall "
        "risk and suggested handling, and must not repeat individual inline findings.\n\n"
        "Each finding must be suitable for an inline diff comment:\n"
        "- severity must be one of critical, high, medium, low.\n"
        "- file_path must be a real path in the repository.\n"
        "- line_range should point to a changed line in the MR diff.\n"
        "- anchor_text should be copied verbatim from that line, <= 80 chars.\n"
        "- description must be self-contained because it is published as the inline comment.\n"
        "- reason must be one short sentence explaining the evidence.\n"
        "- confidence must be an integer from 0 to 100.\n\n"
        "# Task\n"
        f"Review the merge request **!{cr.cr_id} {cr.title}**.\n\n"
        "## Local workspace\n"
        f"- Local clone (target branch checked out): `{workspace_path}`\n"
        f"- source_branch: `{context.source_branch}`\n"  # type: ignore[attr-defined]
        f"- target_branch: `{context.target_branch}`\n"  # type: ignore[attr-defined]
        f"- source ref: `{REVIEW_SOURCE_REF}`\n"
        f"- target ref: `{REVIEW_TARGET_REF}`\n"
        f"- base_sha: `{base_sha}`\n"
        f"- start_sha: `{context.start_sha}`\n"  # type: ignore[attr-defined]
        f"- head_sha: `{head_sha}`\n"
        f"- previous_reviewed_head: `{previous_head}`\n\n"
        "The working tree intentionally contains the target branch so your native project "
        "instructions and configuration are discovered from trusted target content. "
        "The source branch is available only through its Git ref.\n"
        "Use these read-only commands to review it:\n"
        f"- full diff: `git -C {workspace_path} diff "
        f"{REVIEW_TARGET_REF}...{REVIEW_SOURCE_REF}`\n"
        f"- source file: `git -C {workspace_path} show {REVIEW_SOURCE_REF}:<path>`\n"
        "Do not checkout, switch, reset, or otherwise place the source ref in the working tree.\n\n"
        "**IMPORTANT — read-only**: Do NOT modify any files, commit, push, or publish "
        "comments. Only read files and run read-only git commands.\n\n"
        f"{filter_section}"
        f"{skill_section}"
        "## MR metadata\n"
        f"- title: {cr.title}\n"
        f"- author: {cr.author or '(none)'}\n"
        f"- source → target: `{cr.source_branch}` → `{cr.target_branch}`\n"
        f"- state: {cr.state}\n"
        f"- web_url: {cr.web_url or '(none)'}\n\n"
        "## MR description\n"
        "<mr_description>\n"
        f"{_truncate_description(cr.description or '(none)')}"
        "\n</mr_description>\n"
        f"{_format_inline_threads_section(getattr(context, 'inline_threads', []))}"
    )


def _truncate_description(text: str) -> str:
    if len(text) <= _MAX_DESCRIPTION_CHARS:
        return text
    return text[:_MAX_DESCRIPTION_CHARS] + f"\n[truncated: original length {len(text)} chars]"


def _collect_reference_paths(skill_dir: Path) -> list[Path]:
    references_dir = skill_dir / "references"
    if not references_dir.is_dir():
        return []
    paths: list[Path] = []
    for path in sorted(references_dir.glob("*.md")):
        resolved = path.resolve()
        if resolved.is_file() and resolved.is_relative_to(skill_dir):
            paths.append(resolved)
    return paths


def _format_inline_threads_section(threads: list[object]) -> str:
    if not threads:
        return ""

    lines: list[str] = [
        "\n<inline_threads>",
        "## Existing inline review comments",
        "",
        "Apply these rules when deciding whether to re-report an issue:",
        "- **Explicit no-action**: if any reply clearly states the issue will not be fixed, "
        "is intentional, or requires no change — do not report it as a finding; "
        "briefly note it in the overall summary instead.",
        "- **Resolved but incomplete fix**: if a thread is platform-resolved but you find "
        "the issue still exists in the code — still report it as a finding, unless a reply "
        "explicitly states no action is needed.",
        "- **Open threads**: apply normal review judgment.",
        "",
    ]

    for t in threads:
        file_path = getattr(t, "file_path", "")
        line_range = getattr(t, "line_range", "")
        description = getattr(t, "description", "")
        replies: list[str] = getattr(t, "replies", [])
        is_resolved: bool = getattr(t, "is_resolved", False)
        status = "resolved" if is_resolved else "open"
        lines.append(f"- `{file_path}:{line_range}` *({status})*: {description}")
        if replies:
            joined = "; ".join(f'"{r}"' for r in replies)
            lines.append(f"  Replies: {joined}")
        else:
            lines.append("  *(no replies)*")

    lines.append("</inline_threads>")
    return "\n".join(lines)


def _format_reference_manifest(paths: list[Path]) -> str:
    if not paths:
        return ""
    lines = [
        "# Local skill references",
        "The files below belong to the selected review skill, not to the reviewed repository.",
        "Read each file when SKILL.md instructs you to consult it — "
        "do not apply the corresponding methodology step without reading it first.",
    ]
    lines.extend(f"- {path.name}: {path}" for path in paths)
    return "\n".join(lines)
