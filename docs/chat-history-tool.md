# On-demand Discord history

`get_chat_history` is a read-only local function for answer requests with memory reads and
chat logging enabled. A normal answer can complete without a Discord history call. Requests
using the existing web-search or verified local-place flow keep their original tools, so old
Discord content from this function cannot feed a web search. The answer model uses the tool only
when recent context lacks evidence for a past statement or decision.

The scope is bound to the incoming message. The tool covers the current 1:1 DM, guild text or
announcement channel, or public/private thread. It returns human-authored text and excludes
bot and webhook messages. Server reads check both requester and bot for channel view/history
permission. Private threads also check membership or manage-thread permission. Direct capture
and strict external-context policy require the existing explicit call evidence. A DM can return
only messages from its requester.

Optional filters are a literal case-insensitive substring, up to five known author IDs, and
a time window with an inclusive start, exclusive end and explicit timezone. The current message
and newer messages are excluded. A partial result can contain a request-local cursor. Continue
only with the same filters. A page can be empty or partial even when older matching messages exist.

Discord is the source of message content. The tool never returns deleted Discord messages from
local copies. Rows predating installation must still have an eligible same-conversation turn
in storage because older resets were not timestamped. Newer rows respect persisted user, channel,
guild and global reset cutoffs as well as the existing personal memory revision. Permissions,
policy and retained/reset boundaries are checked again before handing data to the model and
after the model responds. Raw tool results stay in this request. The existing turn store can
retain the user's new message, but the history-derived answer is excluded from memory and the
recent-channel buffer.

A request permits three function calls, 100 Discord messages inspected per lookup, 10 returned
rows per lookup, 1,500 characters per row, 4,500 per lookup and 9,000 per answer. Lookup processing
uses a four-second per-call and nine-second combined budget. Revalidation of a response has a
separate two-second limit per check. Only registered calls run and the final model continuation
omits tools. The `history.lookup` event holds status, counts and duration.

OpenAI and OpenRouter use Responses function items. Gemini replays the original Interactions
steps with the corresponding function result while keeping storage disabled. The test suite
uses mock model and Discord responses for provider round trips, ordinary answers without a
lookup, scope, permissions, deleted messages, resets and pagination.

- [Gemini stateless function calling](https://ai.google.dev/gemini-api/docs/function-calling)
- [OpenAI Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)
- [OpenRouter Responses API](https://openrouter.ai/docs/api/api-reference/responses/create-responses)
