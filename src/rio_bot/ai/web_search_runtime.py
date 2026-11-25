def tool_config(search_mode: str, *, native_openrouter: bool = False):
    if search_mode not in {"auto", "required"}:
        return None
    if native_openrouter:
        return [{"type": "openrouter:web_search", "parameters": {"max_uses": 2, "max_results": 5}}]
    return [{"type": "web_search", "search_context_size": "low"}]
