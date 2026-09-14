# 설정 정제 기록 — 츠카츠키 리오

채택 대상은 한국어 Discord RP에서 안정적으로 사용할 수 있는 리오 관련 설정입니다. 공식 자료,
커뮤니티 위키, 비공식 미러는 출처 성격을 구분하고, 일본 서버 선행 내용·유출·검증되지 않은
팬 해석은 공식 사실로 승인하지 않습니다.

## 현재 반영한 내용

- 리오의 기본 프로필: 밀레니엄 사이언스 스쿨 3학년, 세미나의 전 학생회장
- 나이, 생일, 키, 취미, 외형, 고유무기 Planner
- 합리주의적 성격, 통제 성향, Big Sister 별명에 대한 절제된 반응
- 메인 스토리 Vol.2에서 아리스의 위험성을 둘러싼 판단과 히마리와의 대립
- 토키와 아리스에 대한 책임, 사건 이후의 후회와 개선 의지

런타임에는 `src/rio_bot/prompts/rio.md`와 승인된 `src/rio_bot/data/lore.jsonl`만 들어갑니다.
이 문서와 `sources.json`은 조사 기록이며 모델에 자동 주입되지 않습니다.

## 정제 기준

프로필·사건·관계·장비·인지 범위처럼 질문에 따라 필요한 사실은 lore에 두고, 매 답변에 항상
들어가는 캐릭터 prompt에는 말투와 대응 원칙만 둡니다. 구체적인 관계나 사건은 `lore_reference`가
제공될 때만 활용합니다.

자료를 승인할 때는 다음을 확인합니다.

- 한국 서버 또는 공식 공개 범위에서 확인 가능한지
- 공식 사실, 장면 관찰, 편집자의 해석, 팬덤 반응이 섞여 있지 않은지
- 리오가 세계 안에서 직접 알 수 있는 사실인지, 관객만 아는 정보인지
- 원작 사건을 Discord 사용자와의 공동 추억으로 바꾸고 있지 않은지

## 자동 정제 파이프라인

`rio-lore` CLI는 원자료를 로컬 검수 대기열로 분해하고, 사람이 승인한 짧은 항목만
`src/rio_bot/data/lore.jsonl`에 넣습니다. 원문 전체나 커뮤니티 문서 복사본은 저장소에 배포하지
않습니다.

```bash
rio-lore ingest-file --file data/source.txt --title "리오 조사 자료" \
  --url "원문 URL" --source-type community_wiki --lane canon \
  --locator "리오 문단"

rio-lore extract --all
rio-lore list
rio-lore approve canon.some-fact --confidence crosschecked --confirm-kr-release
rio-lore validate
```

여러 URL을 처리하려면 `source_manifest.example.jsonl`을 `data/lore/sources.jsonl`로 복사한 뒤 실제
주소와 lane을 적습니다. `fetch-manifest`는 목록에 명시한 페이지만 가져오며, 링크를 따라가 사이트
전체를 순회하지 않습니다.

## 런타임 검색

봇은 매 응답마다 별도 검색 API나 임베딩 API를 호출하지 않습니다. 현재 메시지와 승인된 항목의
`subjects`, `keywords`, `summary`를 로컬에서 비교하고 관련도가 높은 항목만 참고 JSON에 넣습니다.

```dotenv
LORE_MAX_ITEMS=6
LORE_MAX_CHARS=3200
COMMUNITY_LORE=true
```
