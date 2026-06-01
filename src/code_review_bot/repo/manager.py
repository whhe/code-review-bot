import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def _scrub_clone_url(url: str) -> str:
    """Replace embedded credentials in a git URL with a placeholder."""
    if "://" not in url or "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    _, host_path = rest.rsplit("@", 1)
    return f"{scheme}://<credentials>@{host_path}"


class RepoManager:
    """Manages temporary git clones for individual review runs.

    Each review gets an isolated workspace cloned from the remote over HTTPS
    with the platform token injected into the URL. The workspace is deleted
    after the review completes, regardless of outcome.
    """

    def __init__(
        self,
        *,
        clone_url: str,
        token: str = "",
        review_base_dir: str | Path | None = None,
        clone_depth: int = 0,
    ) -> None:
        self.clone_url = clone_url
        self.token = token
        self.review_base_dir = Path(review_base_dir) if review_base_dir else None
        self.clone_depth = clone_depth

    def _inject_token(self, url: str) -> str:
        """Inject the stored token into an HTTPS URL using oauth2 basic auth."""
        if not self.token or "://" not in url:
            return url
        scheme, rest = url.split("://", 1)
        if "@" in rest:
            rest = rest.split("@", 1)[1]
        return f"{scheme}://oauth2:{self.token}@{rest}"

    async def make_review_workspace(
        self, source_branch: str, target_branch: str, *, head_repo_url: str = ""
    ) -> Path:
        return await asyncio.to_thread(
            self._make_review_workspace_sync, source_branch, target_branch, head_repo_url
        )

    def _make_review_workspace_sync(
        self, source_branch: str, target_branch: str, head_repo_url: str = ""
    ) -> Path:
        if self.review_base_dir:
            self.review_base_dir.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="code-review-", dir=self.review_base_dir))
        source_dir = root / "source"
        clone_source = self._inject_token(head_repo_url) if head_repo_url else self.clone_url
        try:
            self._clone_ref(clone_source, source_branch, source_dir)
            self._fetch_ref(
                source_dir,
                self.clone_url,
                target_branch,
                f"refs/remotes/origin/{target_branch}",
            )
        except (OSError, subprocess.CalledProcessError):
            shutil.rmtree(root, ignore_errors=True)
            raise
        return root

    def cleanup_review_workspace(self, root: Path) -> None:
        shutil.rmtree(root, ignore_errors=True)

    def _clone_ref(self, url: str, branch: str, destination: Path) -> None:
        command = ["git", "clone"]
        if self.clone_depth > 0:
            command.extend(["--depth", str(self.clone_depth)])
        command.extend(["--branch", branch, "--single-branch", url, str(destination)])
        self._run(command)

    def _fetch_ref(
        self, repo: Path, remote_url: str, ref: str, destination_ref: str | None = None
    ) -> None:
        refspec = f"{ref}:{destination_ref}" if destination_ref else ref
        command = ["git", "-C", str(repo), "fetch"]
        if self.clone_depth > 0:
            command.extend(["--depth", str(self.clone_depth)])
        command.extend([remote_url, refspec])
        self._run(command)

    def _run(
        self,
        command: list[str],
        *,
        stdout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = "/bin/true"
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
