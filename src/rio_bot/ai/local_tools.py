"""Run explicitly registered functions during a single stateless answer."""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

MAX_TOOL_ROUNDS = 3
MAX_CALLS = 3


@dataclass(frozen=True)
class LocalTool:
    schema: dict
    run: Callable[[dict], Awaitable[dict]]
    validate: Callable[[], Awaitable[bool]]


class LocalToolAccessChanged(ValueError):
    """Previously retrieved content is no longer eligible to use."""


def _field(item, key, default=None):
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def function_calls(response):
    return [item for item in getattr(response, "output", ()) or ()
            if _field(item, "type") == "function_call"]


def _replay_items(response, calls):
    if hasattr(response, "_rio_gemini_steps"):
        return [{"type": "rio_gemini_steps", "steps": response._rio_gemini_steps}]
    items = []
    for item in response.output:
        if isinstance(item, dict):
            items.append(item)
        elif hasattr(item, "model_dump"):
            items.append(item.model_dump(mode="json", exclude_none=True))
        elif item in calls:
            items.append({key: _field(item, key) for key in ("type", "name", "call_id", "arguments")})
    return items


async def _run_call(registry, call):
    name = _field(call, "name")
    if not isinstance(name, str) or name not in registry:
        return {"status": "unknown_tool"}
    raw = _field(call, "arguments", "")
    if not isinstance(raw, str) or len(raw) > 4096:
        return {"status": "invalid_arguments"}
    try:
        arguments = json.loads(raw)
    except (ValueError, RecursionError):
        return {"status": "invalid_arguments"}
    if not isinstance(arguments, dict):
        return {"status": "invalid_arguments"}
    return await registry[name].run(arguments)


async def request_with_local_tools(send, request, tools: tuple[LocalTool, ...]):
    if not tools:
        return await send(**request)

    registry = {tool.schema["name"]: tool for tool in tools}
    schemas = [tool.schema for tool in tools]
    tool_request = {**request, "tools": schemas, "tool_choice": "auto",
                    "parallel_tool_calls": False}
    transcript = list(request["input"])
    remaining = MAX_CALLS
    used = {}
    response = await send(**tool_request)

    for round_number in range(MAX_TOOL_ROUNDS):
        calls = function_calls(response)
        if not calls:
            return response
        call_ids = [_field(call, "call_id") or _field(call, "id") for call in calls]
        if len(calls) > MAX_CALLS or not all(isinstance(key, str) and key for key in call_ids):
            raise ValueError("Invalid model tool calls")
        if len(set(call_ids)) != len(call_ids):
            raise ValueError("Duplicate model tool call IDs")
        transcript.extend(_replay_items(response, calls))

        for call, call_id in zip(calls, call_ids, strict=True):
            name = _field(call, "name")
            result = {"status": "call_budget"}
            if remaining:
                remaining -= 1
                result = await _run_call(registry, call)
                if isinstance(name, str) and name in registry:
                    used[name] = registry[name]
            transcript.append({
                "type": "function_call_output", "call_id": call_id,
                "output": json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            })

        for tool in used.values():
            if not await tool.validate():
                raise LocalToolAccessChanged
        finalize = not remaining or round_number + 1 == MAX_TOOL_ROUNDS
        response = await send(**{**(request if finalize else tool_request), "input": transcript.copy()})
        for tool in used.values():
            if not await tool.validate():
                raise LocalToolAccessChanged
        if finalize:
            if function_calls(response):
                raise ValueError("Model returned a tool call without registered tools")
            return response

    raise AssertionError("Tool loop did not finalize")
