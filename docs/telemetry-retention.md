# Telemetry retention audit

Runtime의 `events.jsonl`, `usage.jsonl`, `discord-usage.jsonl`은 각각
`RotatingFileHandler(maxBytes=5_000_000, backupCount=3)`를 사용한다.
활성 파일을 포함하면 보통 스트림당 약 20MB이며, 한 레코드 크기에 따라 초과할 수 있다.
이 크기 제한은 trace 30일·집계 usage 90일이라는 기간 정책을 보장하지 않는다.
Provider 호출 원시 usage와 Discord turn 집계 usage는 서로 다른 스트림이다.
원시 `usage.jsonl`의 기간 목표는 아직 정해지지 않아 보고서에서 null로 표시한다.
`discord-usage.jsonl`은 turn별 집계이며, 일별 90일 집계 정책의 구현 완료를 뜻하지 않는다.

## 호스트에서 읽기 전용 점검

배포된 모듈 또는 이 브랜치의 `src`를 사용해 실제 runtime 로그 디렉터리를 명시한다.
기본 경로를 추정하거나 `.env`를 읽지 않는다. 서비스 재시작과 모델 호출은 필요 없다.

```bash
cd /opt/rio-discord-bot
.venv/bin/python -m rio_bot.tooling.retention_audit --log-dir /opt/rio-discord-bot/data/logs
```

출력에는 고정 스트림 이름, 파일 수·크기, 유효/무효·과대·미래 레코드 수,
최초·최종 시각, 관측 기간과 목표보다 오래된 레코드 수만 포함한다.
본문·사용자 ID·turn ID·키·provider 응답·임의 파일 이름은 출력하지 않는다.
활성 파일과 숫자 suffix 백업만 읽고 심볼릭 링크를 건너뛴다.
한 줄 메모리는 제한되지만 전체 파일을 순차 검사하므로 운영 부하가 낮은 때 실행한다.
읽는 동안 회전·추가가 일어날 수 있어 원자적 snapshot은 아니다.

종료 코드 0은 모든 스트림을 읽고 시각을 해석했다는 뜻이다. 1은 누락·읽기 실패 또는
해석 불가능한 레코드가 있다는 뜻이다. **어느 코드도 기간 정책 합격을 뜻하지 않는다.**
실제 자료가 적거나 트래픽이 낮으면 짧은 관측 기간만으로 조기 삭제를 입증할 수 없다.
오래된 자료 발견도 연속 수집·누락 없음·백업 복구 가능성을 보장하지 않는다.

## 별도로 확인할 호스트 근거

CT 101에서 아래 명령으로 journald의 보존/용량 설정과 실제 디스크 사용을 확인한다.
전체 서비스 환경이나 journal 본문을 수집하지 않는다.

```bash
systemd-analyze cat-config systemd/journald.conf
journalctl --disk-usage
```

`Storage`, `MaxRetentionSec`, `MaxFileSec`, `SystemMaxUse`, `RuntimeMaxUse`,
`SystemKeepFree`, `RuntimeKeepFree`와 drop-in 적용 순서를 확인한다.
journald는 JSONL 파일과 별도 저장소이며 전체 journal 설정을 Bot 전용 보존 증거로 쓰지 않는다.
추가 logrotate/backup/timer가 있으면 실제 대상·주기·삭제 조건을 확인하고,
압축·외부 아카이브는 이 도구의 검색 범위 밖이라고 기록한다.

현재 runtime에는 일별 90일 집계 저장소와 기간 기반 삭제가 없다.
최대 보관 기간인지 최소 조회 가능 기간인지, 원시 usage와 일별 집계의 필드·용량·삭제 기준을
확정한 뒤 별도 변경으로 구현한다. 이 점검 도구는 파일 삭제·보존 설정 변경을 하지 않는다.
