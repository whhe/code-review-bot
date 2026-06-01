import pytest

from code_review_bot.agent.protocol import AgentRunResult
from code_review_bot.platforms.models import ChangeRequest
from code_review_bot.review.models import ReviewTaskContext
from code_review_bot.review.runner import CodingAgentReviewRunner


class FakeSkill:
    name = "default"
    version = "1"
    additional_directories = ["/tmp/skills/code-review"]

    def build_prompt(self, context: ReviewTaskContext) -> str:
        return "review prompt"


class FakeAgent:
    def __init__(self) -> None:
        self.agent = ""
        self.prompts: list[str] = []
        self.additional_directories: list[str] | None = None

    async def run_once(
        self,
        prompt: str,
        *,
        agent: str = "plan",
        system: str | None = None,
        files: list[str] | None = None,
        additional_directories: list[str] | None = None,
    ) -> AgentRunResult:
        self.agent = agent
        self.prompts.append(prompt)
        self.additional_directories = additional_directories
        return AgentRunResult(text='{"summary":"ok","findings":[]}', parts=[], usage={})


def make_task_context() -> ReviewTaskContext:
    return ReviewTaskContext(
        change_request=ChangeRequest(
            project_ref="1",
            cr_id="5",
            title="Fix bug",
            description="",
            author="alice",
            source_branch="feature",
            target_branch="main",
            state="opened",
            draft=False,
            web_url="",
            head_sha="head",
        ),
        workspace_path="/tmp/review/source",
        source_branch="feature",
        target_branch="main",
        base_sha="base",
        start_sha="start",
        head_sha="head",
    )


@pytest.mark.asyncio
async def test_coding_agent_review_runner_uses_plan_agent_and_parses_json() -> None:
    agent = FakeAgent()

    result = await CodingAgentReviewRunner(agent).review(FakeSkill(), make_task_context())

    assert result.summary == "ok"
    assert agent.agent == "plan"
    assert agent.prompts == ["review prompt"]
    assert agent.additional_directories == ["/tmp/skills/code-review"]


@pytest.mark.asyncio
async def test_coding_agent_review_runner_retries_on_bad_json() -> None:
    call_count = 0

    class RetryAgent:
        async def run_once(self, prompt: str, **kwargs: object) -> AgentRunResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return AgentRunResult(text="not json", parts=[], usage={})
            return AgentRunResult(text='{"summary":"retry ok","findings":[]}', parts=[], usage={})

    result = await CodingAgentReviewRunner(RetryAgent(), max_json_retries=1).review(
        FakeSkill(), make_task_context()
    )

    assert result.summary == "retry ok"
    assert call_count == 2
