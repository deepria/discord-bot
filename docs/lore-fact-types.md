# Lore fact types

`rio-lore ingest-file`은 일반 원문과 **fact-type이 표시된 curated 원문**을 모두 받을 수 있습니다.

## Curated block format

다음 태그를 줄 하나에 단독으로 적으면 `ingest-file`이 LLM 추출 전에 블록을 분리하고,
그 태그를 `declared_fact_type`으로 `raw.jsonl`에 보존합니다.

| Source tag | Stored `fact_type` | Runtime handling |
|---|---|---|
| `[FACT_DIRECT]` | `fact_direct` | `world_fact` |
| `[FACT_VISUAL]` | `fact_visual` | `world_fact` |
| `[FACT_REPORTED]` | `fact_reported` | `world_fact`; summary must preserve who said/reported it |
| `[INFERENCE]` | `inference` | `interpretation` |
| `[UNKNOWN]` | `unknown` | `interpretation` + positive-fact guard |
| `[ADAPTATION]` | `adaptation` | reference-only, automatically `suppressed` |
| `[FANDOM]` | `fandom` | reference-only, automatically `suppressed` |

Example:

```text
[FACT_VISUAL]
id: rio.wings.aerial_movement
statement: 리오는 특정 공식 컷신에서 날개를 펼친 채 공중 기동한다.

[INFERENCE]
id: rio.wings.speed
statement: 해당 추격 연출상 매우 빠른 공중 이동이 가능한 것으로 보인다.

[UNKNOWN]
id: kivotos.wings.universal_flight
statement: 날개가 있는 학생 모두가 비행할 수 있다는 보편 규칙은 확인되지 않았다.
```

명시된 태그는 **강제 분류**입니다. Structured Output 모델이 다른 `fact_type`을 내더라도 파이프라인이
`declared_fact_type`으로 덮어씁니다. 따라서 `[INFERENCE]`나 `[UNKNOWN]`이 실수로 `world_fact`로
승격되지 않습니다.

## Untagged sources

나무위키처럼 태그가 없는 `community_wiki` 원문도 계속 사용할 수 있습니다. 추출 모델이 각 atomic
claim을 위 `fact_type` 중 하나로 분류하며, `~로 보인다`, `추측된다`, `유력하다`, `팬덤에서는` 같은
표현은 직접 사실로 자동 승격하지 않도록 extraction policy가 구성되어 있습니다.

태그 없는 자료는 curated 자료보다 분류 확실성이 낮으므로 review 단계는 그대로 유지합니다.

## Review behavior

`rio-lore list`는 일반 승인 후보 수와 `suppressed reference-only` 수를 따로 표시합니다.
`adaptation`/`fandom`인 canon-source claim은 자동으로 `suppressed`되어 runtime lore에 들어가지 않습니다.
필요하다면 근거를 확인한 뒤 다음처럼 fact type을 재분류해야 승인 후보로 돌아옵니다.

```bash
uv run rio-lore edit canon.some-id --fact-type fact_direct
```

`inference`와 `unknown`은 승인할 수 있지만 runtime에서 `world_fact`가 아니라 `interpretation`으로
전달됩니다. `unknown`에는 `do_not_assert_positive_fact` guard도 함께 붙습니다.

기존에 이미 검수되어 `lore.jsonl`에 들어간 레코드는 `fact_type` 필드가 없어도 이전 동작과의
호환성을 위해 `fact_direct`로 간주합니다. 즉 이 변경은 기존 runtime lore를 자동 재분류하지 않습니다.
