# Turn telemetry schema

`data/logs/events.jsonl`은 schema version `1`의 content-free JSONL event stream이다. 모든 event에는
`at`, `schema_version`, `event`가 포함된다. Discord 호출로 처리되는 각 turn은 UUID `turn_id`를 한 번
생성하며, 관련 event와 `usage.jsonl`의 provider 호출 행에서 같은 값을 사용한다.

## Turn events

`turn.completed`와 `turn.failed`는 다음 필드를 사용한다.

```json
{
  "at": "2026-09-23T12:34:56+00:00",
  "schema_version": 1,
  "event": "turn.completed",
  "turn_id": "uuid",
  "operation": "answer",
  "status": "ok",
  "provider": "gemini",
  "model": "...",
  "routing": {"web": false, "tier": "primary"},
  "latency_ms": 1240,
  "tokens": {"input": 0, "output": 0, "total": 0},
  "web_search_calls": 0,
  "memory_lifecycle": "written",
  "error_type": null
}
```

- `status`: `ok` 또는 `error`
- `memory_lifecycle`: `not_requested`, `written`, `not_written`
- 모르는 token, routing, provider 값은 `null`이다. 필드의 존재 여부로 의미를 바꾸지 않는다.
- `turn.started`, 기존 호환 event(`turn_started`, `turn_completed`, `turn_failed`), AI request event에도
  가능한 경우 같은 `turn_id`를 기록한다.

## 금지 데이터

이 event stream과 usage JSONL에는 Discord 발화·답변, prompt, provider 원문 요청·응답, 이미지 bytes/URL,
첨부 파일명, API key/token/Bearer header/webhook URL, memory 본문을 기록하지 않는다. telemetry 쓰기 실패는
Discord 응답 경로를 실패시키지 않는다.
