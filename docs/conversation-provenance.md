# Rio 대화 provenance 경계

## 런타임 경로

`runtime_entry → web_bot → bot → RequestAssembler → provider` 순서로 처리한다.
`web_bot`은 답글 원문과 명시적으로 언급된 대상의 요청 범위 문맥을 수집하고, `RecentMessages`는
현재 채널의 짧은 대화를 보관한다. 장기 요약·structured memory는 별도 저장 경로이며, structured
memory는 기본적으로 shadow/write-only 평가 경계에 있다.

## 모델 입력 보존 규칙

최근 채널 turn은 모델 직전에 다음 provenance를 함께 직렬화한다.

- `message_id`, `author_id`, `author_name`, `speaker_type`
- `channel_id`, `timestamp`, `content`
- `reply_to`, `reference`, `is_current_turn`

`speaker_type`은 현재 요청자, 다른 participant, Rio assistant를 구분한다. `BOT_ADMIN_IDS`의
`husband_admin` 관계성은 현재 요청자의 인가 결과만으로 계산하며, participant·표시명·DM 여부·과거
발화에는 전파하지 않는다. 답글/인용은 직접 원문과 출처를 별도 turn으로 유지한다.

## source priority

“누가/무엇을/아까 말했나”처럼 최근 화자·발화 귀속을 묻는 요청은 `channel_recent_messages`의
Discord provenance가 최우선이다. 이때 장기 요약, 개인 최근 대화, cross-channel public memory는
모델 입력에서 제외해 서로 다른 화자의 발화를 semantic memory가 덮어쓰지 못하게 한다.

화자 오귀속 정정은 사과 → 잘못된 귀속 철회 → 확인 가능한 발화별 재정리 순서로 답한다. 기술 답변은
전제·조건·예외를 점검하며, 실제 tool/API 결과가 없으면 일정·설정·외부 상태를 확인하거나 변경했다고
말하지 않는다.
