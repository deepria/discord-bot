from rio_bot.ai.egress_policy import apply_context_policy


def test_strict_policy_keeps_only_direct_bot_interaction_context():
    context = {
        "server_note": "server-wide note",
        "channel_recent_messages": [
            {"role": "user", "user_id": "200", "content": "ordinary chatter"},
            {"role": "user", "user_id": "200", "content": "direct call", "direct_trigger": True},
            {"role": "assistant", "content": "rio answer"},
            {
                "role": "user",
                "user_id": "100",
                "content": "explicit own reply",
                "context_kind": "replied_message",
            },
            {
                "role": "user",
                "user_id": "200",
                "content": "third-party reply",
                "context_kind": "replied_message",
            },
        ],
        "public_server_context": [
            {"user_id": "100", "summary": "caller public context"},
            {"user_id": "200", "summary": "other user context"},
        ],
    }

    filtered = apply_context_policy(context, 100, "bot_interactions_only")

    assert filtered["server_note"] == ""
    assert [row["content"] for row in filtered["channel_recent_messages"]] == [
        "direct call",
        "rio answer",
        "explicit own reply",
    ]
    assert filtered["public_server_context"] == [
        {"user_id": "100", "summary": "caller public context"}
    ]
    assert context["server_note"] == "server-wide note"


def test_full_policy_leaves_context_untouched():
    context = {
        "server_note": "server-wide note",
        "channel_recent_messages": [{"role": "user", "content": "ordinary chatter"}],
        "public_server_context": [{"user_id": "200", "summary": "other user context"}],
    }

    assert apply_context_policy(context, 100, "full") is context
