"""Azure OpenAI, Responses API, Entra auth. Model is a setting.

Default model is GPT-5.6 Sol: a Microsoft first-party service covered by Microsoft's DPA/BAA.
Any Azure OpenAI deployment name works in MODEL.
"""
import json
import os

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

RESOURCE = os.environ.get("AZURE_OPENAI_RESOURCE", "zybit-project-resource")
DEFAULT_MODEL = os.environ.get("MODEL", "gpt-5.6-sol")
EFFORT = os.environ.get("REASONING_EFFORT", "high")

_client = None


def client() -> AzureOpenAI:
    global _client
    if _client is None:
        token = get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
        _client = AzureOpenAI(
            azure_endpoint=f"https://{RESOURCE}.openai.azure.com",
            azure_ad_token_provider=token,
            api_version="preview",
            timeout=600,
        )
    return _client


def respond(*, model, instructions, input_items, tools=None, schema=None, effort=None):
    """One model turn. Stateless: we carry the conversation ourselves (runs.state), so reasoning
    items come back encrypted and are echoed on the next turn."""
    kwargs = dict(
        model=model or DEFAULT_MODEL,
        instructions=instructions,
        input=input_items,
        reasoning={"effort": effort or EFFORT},
        store=False,
        include=["reasoning.encrypted_content"],
    )
    if tools:
        kwargs["tools"] = tools
    if schema:
        kwargs["text"] = {"format": {"type": "json_schema", "name": schema["name"], "schema": schema["schema"], "strict": True}}
    return client().responses.create(**kwargs)


def structured(*, model, instructions, text, schema, effort="medium"):
    r = respond(model=model, instructions=instructions, input_items=[{"role": "user", "content": text}], schema=schema, effort=effort)
    return json.loads(r.output_text)


def output_items(response):
    """Response output as plain dicts we can store and send back."""
    return [item.model_dump(exclude_none=True) for item in response.output]
