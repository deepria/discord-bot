# 프롬프트 인젝션 평가

일반 테스트는 API 키 없이 실행하며 권한, 데이터 격리, 입력 역할, Discord 멘션 무력화를
결정적으로 검사해요. `prompt_injection_cases.jsonl`은 직접 공격과 표시 이름·메모·요약·
DM 이력·채널 문맥·공개 기억·이모지 설명을 통한 간접 공격을 포함해요.

라이브 평가는 유료 OpenAI 요청을 명시적으로 실행할 때만 동작해요. 기본값은 18개 사례를
각 2회 실행하고, 답변 생성과 평가에 각각 한 번씩 요청하므로 최대 72회 요청해요.
비용을 줄이려면 `--repeat 1 --no-judge`를 사용해 18회 생성 결과만 저장한 뒤 직접 검토해요.

```bash
python scripts/run_prompt_injection_eval.py --repeat 1 --no-judge
python scripts/run_prompt_injection_eval.py --repeat 2
```

키는 환경의 `OPENAI_API_KEY`에서만 읽고 출력하거나 결과 파일에 저장하지 않아요.
실행 결과는 Git에서 제외된 `evals/results/`에 JSON으로 남아요. 평가용 무작위 canary를
시스템 지침에 추가하고, 원문 응답에 canary가 없는지와 실제 전달문에 Discord 멘션 문법이
없는지를 코드로 검사해요. 선택한 모델 평가기는 사례별 기대 행동을 의미 단위로 판정해요.

모델 평가는 확률적이며 평가기 자체도 틀릴 수 있어요. 실패 결과와 경계 사례는 사람이
확인하고, 재현 가치가 있으면 JSONL에 회귀 사례로 추가해요. 모델이나 프롬프트를 변경하면
같은 데이터셋으로 다시 실행해 이전 결과와 비교해요.

## 대화 품질 회귀 평가

`conversation_quality_cases.jsonl`의 Q1~Q9는 모두 합성 사례입니다. 다중 화자 귀속,
오귀속 정정, assistant 발화 구분, 표시 이름에 의한 권한 오인, 말투 연속성, 기술적
과도한 동조, 실제 도구 없는 완료 주장, 최근 자기 발화 조회를 검토합니다.

형식 검사는 API key 없이 실행하며 provider·운영 DB·Discord를 사용하지 않습니다.

```bash
rio-eval --cases evals/conversation_quality_cases.jsonl --validate-only
```

실제 모델 응답 평가는 별도 provider key가 필요하고 과금될 수 있습니다. 실행 시 기존
in-memory Store를 사용하며 결과는 `data/evals/`에 기록됩니다. 먼저 한 사례를 실행하고
확대하려면 `--limit 1`을 사용하세요. `--validate-only` 성공이나 모의 응답 테스트는
자연어 응답 품질 합격을 뜻하지 않습니다. 실제 출력은 각 `expected` 기준으로 사람이
검토하고, 다른 사용자의 기억과 충돌하는 경우는 기존 provenance 자동 테스트와 함께 평가합니다.

## Routing shadow 회귀 평가

`routing_shadow_cases.jsonl`은 일반 대화, 로어, 최신 정보, 이미지 문맥, 긴 문맥,
답장/인용, 모호한 최신성 질문을 대상으로 합니다. fixture metadata만 이용하는 결정적
오프라인 검사이므로 API key, Discord 연결, provider 호출이 필요 없고 production routing이나
web search를 바꾸지 않습니다.

```bash
uv run rio-routing-eval
uv run rio-routing-eval --output data/evals/routing-shadow.jsonl
```

출력에는 사용자 메시지·답변·이미지·기억 원문을 넣지 않습니다. 운영 shadow telemetry의
품질/비용/지연 비교는 별도로 최소 1주간 수집한 content-free metadata로 검토합니다.
