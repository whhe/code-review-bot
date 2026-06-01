import pytest

from code_review_bot.agent.json_extract import complete_json_with_retries, extract_json
from code_review_bot.skill.protocol import SkillResult


def test_extract_json_from_plain_json() -> None:
    text = '{"summary":"ok","findings":[]}'
    assert extract_json(text) == '{"summary":"ok","findings":[]}'


def test_extract_json_from_fenced_block() -> None:
    text = '```json\n{"summary":"ok","findings":[]}\n```'
    result = extract_json(text)
    assert '"summary":"ok"' in result


def test_extract_json_from_surrounding_text() -> None:
    text = 'Here is the result:\n{"summary":"ok","findings":[]}\nDone.'
    result = extract_json(text)
    assert '"summary":"ok"' in result


def test_extract_json_raises_when_no_json() -> None:
    with pytest.raises(ValueError, match="no JSON object found"):
        extract_json("no json here at all")


@pytest.mark.asyncio
async def test_complete_json_retries_on_bad_output() -> None:
    calls = 0

    async def transport(prompt: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            return "not json"
        return '```json\n{"summary":"retry ok","findings":[]}\n```'

    result = await complete_json_with_retries("review", SkillResult, transport, max_retries=1)

    assert calls == 2
    assert result.summary == "retry ok"


@pytest.mark.asyncio
async def test_complete_json_raises_after_max_retries_exceeded() -> None:
    async def transport(prompt: str) -> str:
        return "not json at all"

    with pytest.raises(ValueError, match="coding agent did not return valid structured JSON"):
        await complete_json_with_retries("review", SkillResult, transport, max_retries=0)


@pytest.mark.asyncio
async def test_complete_json_raises_on_schema_validation_failure() -> None:
    async def transport(prompt: str) -> str:
        return (
            '{"summary":"ok","findings":[{"severity":"INVALID_LEVEL","description":"x",'
            '"file_path":"a.py","line_range":"1","reason":"r","confidence":50}]}'
        )

    with pytest.raises(ValueError, match="coding agent did not return valid structured JSON"):
        await complete_json_with_retries("review", SkillResult, transport, max_retries=0)


@pytest.mark.asyncio
async def test_complete_json_succeeds_on_first_attempt() -> None:
    async def transport(prompt: str) -> str:
        return '{"summary":"first ok","findings":[]}'

    result = await complete_json_with_retries("review", SkillResult, transport, max_retries=1)

    assert result.summary == "first ok"


@pytest.mark.asyncio
async def test_complete_json_repairs_unescaped_quotes(caplog: object) -> None:
    """Agents occasionally emit JSON with unescaped quotes; json-repair should recover."""

    # JSON with unescaped inner quotes in a string value — stdlib json.loads raises,
    # json-repair should fix it and allow the parse to succeed.
    BAD_JSON = '{"summary":"a "quoted" value","findings":[]}'

    async def transport(prompt: str) -> str:
        return BAD_JSON

    with pytest.raises(Exception):
        import json

        json.loads(BAD_JSON)  # Confirm stdlib really does reject this

    result = await complete_json_with_retries("review", SkillResult, transport, max_retries=0)
    assert result.summary is not None  # json-repair recovered something
