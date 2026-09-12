# Lore web verification

`hina-lore verify-web`은 `extract`가 만든 canon candidate를 승인 전에 OpenAI Responses API의
built-in web search로 교차 검증합니다. 웹 검색 결과가 새 lore를 자동 생성하거나 기존 claim의
`fact_type`/`summary`를 덮어쓰지는 않습니다.

## 판정

각 candidate의 `data/lore/review.jsonl`에 `verification` 객체를 추가합니다.

- `corroborated`: 공식 자료 또는 신뢰 가능한 게임 스크립트/데이터 자료가 claim을 직접 뒷받침
- `conflict`: 신뢰 가능한 자료가 claim을 직접 반박
- `insufficient`: 검색 결과는 있으나 출처 수준이나 문맥이 부족
- `not_found`: 직접적인 지지/반박 근거를 찾지 못함

한국 서버 공개 여부는 별도로 `verification.kr_release`에 기록합니다. `confirmed`는 한국 공식
Nexon/Blue Archive URL을 실제 검색 결과에서 확인한 경우에만 유지합니다. 일본/글로벌 출시,
현재 날짜, 업데이트 주기로 한국 출시를 추정하지 않습니다.

`verify-web`은 검증 메타데이터만 저장합니다. `kr_release=confirmed`가 나와도 candidate를 자동
승인하지 않으며, 기존 `approve`/`approve-all --confirm-kr-release` 절차는 그대로 남습니다.

## 사용

한 항목만 먼저 시험할 수 있습니다.

```bash
uv run hina-lore verify-web canon.hina.some-fact
```

범위 지정도 가능합니다. 실수로 큰 비용이 발생하지 않도록 범위 지정 없이 전체 검증하는 동작은
지원하지 않고, 한 번에 실제 검색하는 기본 상한은 20개입니다.

```bash
# 대상만 확인하고 API 호출하지 않음
uv run hina-lore verify-web \
  --source-type curated_research \
  --fact-type fact_direct \
  --dry-run

# curated canon 후보 중 아직 검증하지 않은 앞 20개
uv run hina-lore verify-web \
  --source-type curated_research

# 제한 없이 해당 범위 전부
uv run hina-lore verify-web \
  --source-type curated_research \
  --limit 0
```

이미 `verification`이 있는 후보는 건너뜁니다. 다시 확인하려면 `--force`를 사용합니다.

```bash
uv run hina-lore verify-web canon.hina.some-fact --force
```

기본 모델은 `gpt-5.4-mini`이며 `LORE_VERIFY_MODEL` 또는 `--model`로 변경할 수 있습니다.

```bash
LORE_VERIFY_MODEL=gpt-5.4-mini uv run hina-lore verify-web \
  --source-type curated_research
```

## 승인과의 연동

`hina-lore list`는 각 candidate 옆에 `[web:...]` 상태, 검증 메모, 일부 출처 URL,
한국 서버 공개 검증 상태를 표시합니다.

`approve-all`은 `verification.status == conflict`인 항목을 자동으로 제외합니다. `not_found`와
`insufficient`는 자동 거짓 판정이 아니므로 계속 승인 가능 상태로 남습니다. 필요하면 사람이
근거를 보고 `edit` 또는 `reject`합니다.

웹 검증 정보는 bulk approval로 생성하는 runtime lore에는 포함하지 않습니다. 웹 출처/판정은
`data/lore/review.jsonl`의 검수 메타데이터로 보존합니다.

## 출처 원칙

검증 프롬프트의 우선순위는 다음과 같습니다.

1. 한국 공식 Blue Archive/Nexon 자료
2. 일본/글로벌 공식 Blue Archive 자료
3. 게임 스크립트나 클라이언트 데이터를 충실히 전사한 신뢰 가능한 DB/미러
4. 정리형 위키
5. 커뮤니티 위키·게시판·팬덤 자료

`fact_reported`는 "그 인물이 그 말을 했는가"를 검증하고, 대사 내용 전체를 객관적 세계관 사실로
승격하지 않습니다. `inference`는 해석의 근거를 확인하되 직접 사실로 승격하지 않습니다.
`unknown`은 단순히 검색 결과가 없다는 이유만으로 `corroborated` 처리하지 않습니다.
