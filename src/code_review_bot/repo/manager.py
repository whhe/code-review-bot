import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_GIT_CREDENTIAL_HELPER = (
    '!f() { test "$1" = get || exit 0; '
    'printf "%s\\n" "username=oauth2" "password=$CODE_REVIEW_BOT_GIT_TOKEN"; }; f'
)
REVIEW_SOURCE_REF = "refs/code-review/source"
REVIEW_TARGET_REF = "refs/code-review/target"


def _scrub_clone_url(url: str) -> str:
    """Replace embedded credentials in a git URL with a placeholder."""
    if "://" not in url or "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    _, host_path = rest.rsplit("@", 1)
    return f"{scheme}://<credentials>@{host_path}"


def _strip_clone_url_credentials(url: str) -> str:
    """Return a clone URL that is safe to persist in a workspace git config."""
    if "://" not in url or "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    _, host_path = rest.rsplit("@", 1)
    return f"{scheme}://{host_path}"


class RepoManager:
    """Manages temporary git clones for individual review runs.

    Each review gets an isolated target-branch workspace plus stable source and
    target refs. Credentials are supplied through an ephemeral Git credential
    helper. The workspace is deleted after review, regardless of outcome.
    """

    def __init__(
        self,
        *,
        clone_url: str,
        token: str = "",
        review_base_dir: str | Path | None = None,
        clone_depth: int = 0,
    ) -> None:
        self.clone_url = _strip_clone_url_credentials(clone_url)
        self.token = token
        self.review_base_dir = Path(review_base_dir) if review_base_dir else None
        self.clone_depth = clone_depth

    async def make_review_workspace(
        self,
        source_branch: str,
        target_branch: str,
        *,
        head_repo_url: str = "",
        source_sha: str = "",
        target_sha: str = "",
    ) -> Path:
        return await asyncio.to_thread(
            self._make_review_workspace_sync,
            source_branch,
            target_branch,
            head_repo_url,
            source_sha,
            target_sha,
        )

    def _make_review_workspace_sync(
        self,
        source_branch: str,
        target_branch: str,
        head_repo_url: str = "",
        source_sha: str = "",
        target_sha: str = "",
    ) -> Path:
        if self.review_base_dir:
            self.review_base_dir.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="code-review-", dir=self.review_base_dir))
        source_dir = root / "source"
        clone_source = (
            _strip_clone_url_credentials(head_repo_url) if head_repo_url else self.clone_url
        )
        try:
            self._clone_ref(clone_source, source_branch, source_dir)
            try:
                self._update_ref(source_dir, REVIEW_SOURCE_REF, source_sha or "HEAD")
            except subprocess.CalledProcessError:
                if self.clone_depth <= 0:
                    raise
                self._unshallow_ref(source_dir, clone_source, source_branch)
                self._update_ref(source_dir, REVIEW_SOURCE_REF, source_sha or "HEAD")
            self._fetch_ref(
                source_dir,
                self.clone_url,
                target_branch,
                REVIEW_TARGET_REF,
            )
            try:
                if target_sha:
                    self._update_ref(source_dir, REVIEW_TARGET_REF, target_sha)
                self._verify_merge_base(source_dir)
            except subprocess.CalledProcessError:
                if self.clone_depth <= 0:
                    raise
                self._complete_shallow_history(
                    source_dir,
                    clone_source,
                    source_branch,
                    target_branch,
                )
                self._update_ref(source_dir, REVIEW_SOURCE_REF, source_sha or "HEAD")
                if target_sha:
                    self._update_ref(source_dir, REVIEW_TARGET_REF, target_sha)
                self._verify_merge_base(source_dir)
            self._run(
                self._git_command(
                    "-C",
                    str(source_dir),
                    "checkout",
                    "--detach",
                    REVIEW_TARGET_REF,
                )
            )
        except (OSError, subprocess.CalledProcessError):
            shutil.rmtree(root, ignore_errors=True)
            raise
        return root

    def cleanup_review_workspace(self, root: Path) -> None:
        shutil.rmtree(root, ignore_errors=True)

    def _clone_ref(self, url: str, branch: str, destination: Path) -> None:
        command = self._git_command("clone", "--no-checkout")
        if self.clone_depth > 0:
            command.extend(["--depth", str(self.clone_depth)])
        command.extend(["--branch", branch, "--single-branch", url, str(destination)])
        self._run(command)

    def _fetch_ref(
        self, repo: Path, remote_url: str, ref: str, destination_ref: str | None = None
    ) -> None:
        refspec = f"{ref}:{destination_ref}" if destination_ref else ref
        command = self._git_command("-C", str(repo), "fetch")
        if self.clone_depth > 0:
            command.extend(["--depth", str(self.clone_depth)])
        command.extend([remote_url, refspec])
        self._run(command)

    def _git_command(self, *args: str) -> list[str]:
        command = ["git"]
        if self.token:
            command.extend(
                [
                    "-c",
                    "credential.helper=",
                    "-c",
                    f"credential.helper={_GIT_CREDENTIAL_HELPER}",
                ]
            )
        command.extend(args)
        return command

    def _update_ref(self, repo: Path, ref: str, revision: str) -> None:
        self._run(self._git_command("-C", str(repo), "update-ref", ref, revision))

    def _verify_merge_base(self, repo: Path) -> None:
        self._run(
            self._git_command(
                "-C",
                str(repo),
                "merge-base",
                REVIEW_TARGET_REF,
                REVIEW_SOURCE_REF,
            )
        )

    def _complete_shallow_history(
        self,
        repo: Path,
        source_url: str,
        source_branch: str,
        target_branch: str,
    ) -> None:
        for remote_url, branch in (
            (source_url, source_branch),
            (self.clone_url, target_branch),
        ):
            if not self._is_shallow(repo):
                return
            self._unshallow_ref(repo, remote_url, branch)

    def _is_shallow(self, repo: Path) -> bool:
        result = self._run(
            self._git_command("-C", str(repo), "rev-parse", "--is-shallow-repository"),
            stdout=subprocess.PIPE,
        )
        return result.stdout.strip() == "true"

    def _unshallow_ref(self, repo: Path, remote_url: str, ref: str) -> None:
        self._run(
            self._git_command(
                "-C",
                str(repo),
                "fetch",
                "--unshallow",
                remote_url,
                ref,
            )
        )

    def _run(
        self,
        command: list[str],
        *,
        stdout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = "/bin/true"
        if self.token:
            env["CODE_REVIEW_BOT_GIT_TOKEN"] = self.token
        try:
            return subprocess.run(
                command,
                check=True,
                stdout=stdout if stdout is not None else subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
            )
        except subprocess.CalledProcessError as error:
            # Scrub embedded credentials from the command before the error propagates;
            # CalledProcessError.__str__ includes the full cmd list.
            scrubbed = [_scrub_clone_url(part) for part in error.cmd]
            raise subprocess.CalledProcessError(
                error.returncode, scrubbed, output=error.output, stderr=error.stderr
            ) from None
