from typing import Protocol

from code_review_bot.agent.json_extract import complete_json_with_retries
from code_review_bot.agent.protocol import CodingAgent
from code_review_bot.review.models import ReviewTaskContext
from code_review_bot.skill.protocol import SkillResult


class PromptBuildingSkill(Protocol):
    name: str
    version: str

    def build_prompt(self, context: ReviewTaskContext) -> str: ...


class CodingAgentReviewRunner:
    """Executes a review skill by dispatching to a CodingAgent and parsing its JSON output."""

    def __init__(self, agent: CodingAgent, max_json_retries: int = 1) -> None:
        self.agent = agent
        self.max_json_retries = max_json_retries

    async def review(self, skill: PromptBuildingSkill, context: ReviewTaskContext) -> SkillResult:
        prompt = skill.build_prompt(context)

        async def run(current_prompt: str) -> str:
            result = await self.agent.run_once(
                current_prompt,
                agent="plan",
                additional_directories=getattr(skill, "additional_directories", []),
            )
            return result.text

        return await complete_json_with_retries(
            prompt,
            SkillResult,
            run,
            max_retries=self.max_json_retries,
        )
