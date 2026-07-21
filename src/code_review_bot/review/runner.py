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
        token_valid_counts = {key: 0 for key in token_keys}
        run_count = 0

        def collect_runtime_metadata(result: object) -> None:
            nonlocal run_count
            run_count += 1
            model = getattr(result, "model", None)
            if isinstance(model, str) and model.strip():
                model_candidates.append(model.strip())
            usage = getattr(result, "usage", {})
            usage_map = usage if isinstance(usage, dict) else {}
            for key in token_keys:
                value = usage_map.get(key)
                if isinstance(value, int):
                    token_totals[key] += value
                    token_valid_counts[key] += 1

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
        model_value: str | None = None
        unique_models = list(dict.fromkeys(model_candidates))
        if len(unique_models) == 1:
            model_value = unique_models[0]
        elif len(unique_models) > 1:
            model_value = f"multiple models used: {', '.join(unique_models)}"

        def aggregate_tokens(key: str) -> int | None:
            # Show totals only when every call reported the metric so the final
            # summary never presents partial usage as complete usage.
            if token_valid_counts[key] != run_count:
                return None
            return token_totals[key]

        runtime = RuntimeMetadata(
            model=model_value,
            input_tokens=aggregate_tokens("input_tokens"),
            output_tokens=aggregate_tokens("output_tokens"),
            total_tokens=aggregate_tokens("total_tokens"),
        )
        if not any(
            value is not None
            for value in (
                runtime.model,
                runtime.input_tokens,
                runtime.output_tokens,
                runtime.total_tokens,
            )
        ):
            return parsed
        return parsed.with_runtime(runtime)
