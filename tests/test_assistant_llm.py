import json

import httpx
import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from alfa_bot.assistant.core_schemas import IntentResult as Decision
from alfa_bot.assistant.llm import DeepSeekLLM, LLMError
from alfa_bot.config import Settings


@pytest.mark.asyncio
async def test_deepseek_json_retry_uses_only_configured_endpoint_and_model():
    calls = []

    def handler(request):
        calls.append(request)
        content = "   " if len(calls) == 1 else '{"intent":"out_of_scope"}'
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": content},
                    }
                ]
            },
        )

    settings = Settings(_env_file=None, deepseek_api_key="test-key", llm_retries=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = DeepSeekLLM(settings, client)
        content = await llm.generate(
            [SystemMessage(content="JSON"), HumanMessage(content="hi")], schema=Decision
        )
    assert Decision.model_validate_json(content).intent == "out_of_scope"
    assert len(calls) == 2
    assert str(calls[0].url) == "https://api.deepseek.com/chat/completions"
    body = json.loads(calls[0].content)
    assert body["model"] == settings.deepseek_model
    assert body["response_format"] == {"type": "json_object"}
    assert body["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_provider_auth_error_does_not_expose_response_or_key():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"error": "PRIVATE provider detail"})

    settings = Settings(_env_file=None, deepseek_api_key="secret-key")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = DeepSeekLLM(settings, client)
        with pytest.raises(LLMError) as error:
            await llm.generate([HumanMessage(content="hi")], schema=Decision)
    assert len(calls) == 1
    assert str(error.value) == "DeepSeek HTTP 401"


@pytest.mark.asyncio
async def test_truncated_json_is_not_accepted_as_answer():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": '{"intent":"out_of_scope"}'},
                    }
                ]
            },
        )

    settings = Settings(_env_file=None, deepseek_api_key="test", llm_retries=0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMError, match="invalid JSON"):
            await DeepSeekLLM(settings, client).generate(
                [HumanMessage(content="hi")], schema=Decision
            )
