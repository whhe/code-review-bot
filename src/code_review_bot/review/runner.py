from typing import Protocol

from code_review_bot.agent.json_extract import complete_json_with_retries
from code_review_bot.agent.protocol import CodingAgent
from code_review_bot.review.models import ReviewTaskContext
from code_review_bot.skill.protocol import RuntimeMetadata, SkillResult


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
        model_candidates: list[str] = []
        token_keys = ("input_tokens", "output_tokens", "total_tokens")
        token_totals = {key: 0 for key in token_keys}
        token_available = {key: True for key in token_keys}
        token_seen = {key: False for key in token_keys}

        def collect_runtime_metadata(result: object) -> None:
            model = getattr(result, "model", None)
            if isinstance(model, str) and model.strip():
                model_candidates.append(model.strip())
            usage = getattr(result, "usage", {})
            usage_map = usage if isinstance(usage, dict) else {}
            for key in token_keys:
                value = usage_map.get(key)
                if isinstance(value, int):
                    token_totals[key] += value
                    token_seen[key] = True
                    continue
                token_available[key] = False

        async def run(current_prompt: str) -> str:
            result = await self.agent.run_once(
                current_prompt,
                agent="plan",
                additional_directories=getattr(skill, "additional_directories", []),
            )
            collect_runtime_metadata(result)
            return result.text

        parsed = await complete_json_with_retries(
            prompt,
            SkillResult,
            run,
            max_retries=self.max_json_retries,
        )
        model_value: str | None
        unique_models: list[str] = []
        for value in model_candidates:
            if value not in unique_models:
                unique_models.append(value)
        if not unique_models:
            model_value = None
        elif len(unique_models) == 1:
            model_value = unique_models[0]
        else:
            model_value = f"multiple models used: {', '.join(unique_models)}"
        runtime = RuntimeMetadata(
            model=model_value,
            input_tokens=(
                token_totals["input_tokens"]
                if token_seen["input_tokens"] and token_available["input_tokens"]
                else None
            ),
            output_tokens=(
                token_totals["output_tokens"]
                if token_seen["output_tokens"] and token_available["output_tokens"]
                else None
            ),
            total_tokens=(
                token_totals["total_tokens"]
                if token_seen["total_tokens"] and token_available["total_tokens"]
                else None
            ),
        )
        if (
            runtime.model is None
            and runtime.input_tokens is None
            and runtime.output_tokens is None
            and runtime.total_tokens is None
        ):
            return parsed
        return parsed.with_runtime(runtime)
