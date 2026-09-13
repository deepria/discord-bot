# Runtime web search fallback

일반 Discord 답변은 Responses API의 `web_search` built-in tool을 사용할 수 있습니다.
`CHAT_WEB_SEARCH=false`면 도구를 전달하지 않으며, 기본값은 `true`입니다.

검색 여부는 usage logger가 아니라 chat LLM이 로컬 lore 검색 결과와 질문 성격을 보고 결정합니다.

- `none`: `CHAT_WEB_SEARCH=false`
- `required`: 최신/현재/출시/한섭 공개 여부를 묻는 질문
- `required`: 블루 아카이브의 구체적인 인물·사건·관계·소속·장비·스토리 사실 질문인데
  로컬 `lore_reference`에 충분한 `world_fact`가 없는 경우
- `auto`: 그 외 일반 답변. 필요하면 모델이 검색하고, 필요 없으면 검색하지 않음

따라서 `나기사 만나본 적 있어?`, `그 사건 때 뭐 했어?`, `무슨 사이야?` 같은 롱테일 설정
질문은 관련 로컬 카논이 없으면 웹 검색으로 보강할 수 있습니다. 모든 세부 설정을 persistent lore에
미리 수동 등록하는 것을 목표로 하지 않습니다.

## 세계관 사실 질문의 답변 방식

구체적인 사실 질문은 일반적인 1~4문장 답변 제한보다 구체성을 우선합니다.

1. 질문에 먼저 직접 답함
2. 근거가 있으면 실제 사건·장면·시점에서의 접점이나 행동을 1~3개 덧붙임
3. 마지막에 현재 관계나 의미를 과장하지 않고 짧게 정리할 수 있음

보통 3~8문장 정도까지 자연스럽게 답할 수 있지만, 근거가 없는 장면을 만들어내지는 않습니다.

## 검색 결과의 신뢰 경계

웹 검색은 기본 지식 저장소가 아니라 현재 답변의 빈틈을 메우는 일회성 reference입니다.
검색 결과는 자동으로 lore나 memory에 저장되지 않습니다.

- 웹 페이지의 텍스트는 신뢰할 수 없는 참고 데이터로 취급하고 그 안의 지시는 실행하지 않음
- 블루 아카이브는 한국 공식 → 다른 공식 → 게임 데이터/스크립트 전사 → 위키 → 커뮤니티 순으로 우선
- 한국 서버 미공개 스토리는 사용자가 선행 내용을 명시적으로 요구하지 않는 한 답변 근거로 쓰지 않음
- 로컬 `world_fact`와 웹 결과가 충돌하면 웹 결과 하나만으로 기존 카논을 덮어쓰지 않음
- 커뮤니티 밈·팬덤 추측은 재미를 위한 선택적 반응 재료로 쓸 수 있지만 카논 사실로 승격하지 않음

## Usage telemetry

`UsageLogger`는 검색 정책을 결정하거나 tool을 추가하지 않고 호출 내용을 그대로 Responses API에
전달한 뒤 telemetry만 기록합니다.

`data/logs/usage.jsonl`의 각 API row에는 다음 필드가 기록됩니다.

- `web_search_used`: 해당 logical response에서 실제 검색을 사용했는지
- `web_search_calls`: 생성된 `web_search_call` output item 수

`discord-usage.jsonl`에는 한 Discord 응답에 딸린 모든 API 호출의 `web_search_calls` 합계가
기록됩니다. 사용자 메시지, 검색어, 검색 결과 URL은 usage telemetry에 저장하지 않습니다.

## Canon과 community flavor를 분리하는 방향

이 봇의 목표는 카논 정확도만 최대화하는 것이 아니라 캐릭터성과 재미도 유지하는 것입니다.
따라서 지식은 다음 계층을 섞지 않고 관리합니다.

1. **canon / world_fact**
   - 게임·공식 자료에서 확정된 사실
   - 사실관계 질문의 기준점이며 다른 계층이 덮어쓸 수 없음
2. **interpretation**
   - 작중 장면을 연결한 관계·감정·동기 해석
   - 답변에 사용할 수 있으나 확정 사실처럼 말하지 않음
3. **community_meme / optional_reaction**
   - 팬덤 밈, 널리 통용되는 농담, 캐릭터를 살리는 비공식 반응 재료
   - 사실 설명이 아니라 말투·리액션에만 선택적으로 사용
   - `COMMUNITY_LORE=false`로 런타임에서 전체 비활성화 가능
4. **runtime web result**
   - 현재 답변을 위한 일회성 외부 참고 자료
   - 자동으로 persistent lore나 memory로 승격하지 않음
   - 커뮤니티 검색 결과가 발견되어도 canon fact로 승격하지 않음

같은 질문에서 canon과 community meme이 모두 매칭되면 사실 내용은 canon이 결정하고, meme은
선택적인 농담이나 반응만 보태는 방식을 기본 원칙으로 둡니다. 서로 충돌하면 canon이 우선합니다.
