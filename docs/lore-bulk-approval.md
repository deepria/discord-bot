# Lore bulk approval

`rio-lore approve-all`은 review queue의 `candidate`를 범위 지정 후 한 번에 승인합니다.
실수로 전체 queue를 승인하지 않도록 `--source-type`, `--id-prefix`, `--title` 중 하나 이상을
반드시 지정해야 합니다.

먼저 dry run을 권장합니다.

```bash
uv run rio-lore approve-all \
  --source-type curated_research \
  --confirm-kr-release \
  --dry-run
```

출력에는 승인 예정 수, fact type별 confidence, suppressed/non-candidate 수,
웹 검증 충돌 수, runtime ID 중복 수가 표시됩니다. `--dry-run`은 파일을 변경하지 않습니다.

검토가 끝났다면 같은 범위에 `--yes`를 사용합니다.

```bash
uv run rio-lore approve-all \
  --source-type curated_research \
  --confirm-kr-release \
  --yes
```

추가 필터도 사용할 수 있습니다.

```bash
uv run rio-lore approve-all \
  --id-prefix canon.rio.bond. \
  --fact-type fact_direct \
  --fact-type fact_reported \
  --confirm-kr-release \
  --dry-run
```

## 안전 규칙

- `candidate` 상태만 승인합니다. `suppressed`, `accepted`, `rejected`는 자동 제외합니다.
- canon의 `adaptation`/`fandom`처럼 reference-only인 항목은 일괄 승인하지 않습니다.
- 이미 runtime에 같은 ID가 있으면 해당 candidate는 제외합니다.
- 미래의 웹 검증 단계에서 `verification.status=conflict`인 candidate는 자동 제외합니다.
- canon을 실제 승인할 때는 기존 단건 승인과 마찬가지로 `--confirm-kr-release`가 필요합니다.
- 모든 승인 대상을 먼저 validation한 뒤 runtime/review 파일을 각각 한 번만 씁니다.
  두 번째 파일 쓰기가 실패하면 원래 snapshot으로 복구를 시도합니다.

## confidence 기본값

`--confidence`를 주지 않으면 보수적으로 자동 결정합니다.

- 공식 1차 자료(`official_game`, `official_site`, `official_video`, `official_profile`)의
  직접 사실: `verified`
- 공식 자료를 전사한 2차 자료(`official_secondary`, `game_data_mirror`,
  `official_data_mirror`): `official_secondary`
- `inference`, `unknown`, `curated_research`, 기타 자료: `crosschecked`
- community meme: `crosschecked`

필요하면 전체 승인 대상에 같은 confidence를 강제로 지정할 수 있습니다.

```bash
uv run rio-lore approve-all \
  --source-type curated_research \
  --confidence crosschecked \
  --confirm-kr-release \
  --yes
```
