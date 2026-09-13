# Discord slash command interface

운영 중 사용하는 관리·설정 명령은 Discord native slash command로 통일합니다.
`히나야 /메모`, `히나야 /기억`, `히나야 /이모지 ...` 같은 prefix+slash 메시지 명령은
production entrypoint에서 더 이상 해석하지 않습니다. 일반 대화 호출은 기존처럼 `히나야`, 멘션,
답장 핑을 사용합니다.

## 일반 사용자 기억 명령

| 명령 | 기능 |
| --- | --- |
| `/memory show` | 현재 채널의 내 장기 요약과 개인 메모 확인 |
| `/memory note text:<내용>` | 같은 서버의 내 응답에 사용할 개인 메모 교체 |
| `/memory note-clear` | 개인 메모 삭제 |
| `/memory clear confirm:true` | 해당 서버 또는 DM에서의 내 대화 기록·자동 요약·개인 메모 삭제 |
| `/memory server-show` | 현재 서버 공통 메모 확인 |
| `/memory server-note text:<내용>` | 서버 공통 메모 교체. Discord `Manage Server` 권한 필요 |
| `/memory server-clear` | 서버 공통 메모 삭제. Discord `Manage Server` 권한 필요 |

서버에서 `/memory clear`를 실행하면 서버 단기 문맥도 초기화될 수 있으므로 봇 관리자 또는
Discord `Manage Server` 권한이 필요합니다. DM에서는 본인이 직접 실행할 수 있습니다.

장기 기억 최종 모드에서 쓰기가 꺼져 있으면 `/memory note`와 `/memory server-note`는 새 데이터를
저장하지 않습니다.

## 봇 관리자 기억/로그 설정

다음 명령은 앱 소유자 또는 `BOT_ADMIN_IDS` 사용자만 실행할 수 있습니다.

| 명령 | 기능 |
| --- | --- |
| `/memory mode` | 전역/서버/채널 장기 기억 읽기·쓰기 모드 설정 |
| `/memory chatlog` | 전역/서버/채널 최근 채널 로그 읽기 설정 |
| `/memory status` | 현재 채널의 최종 기억·로그 설정 확인 |
| `/memory overview` | 전체 서버/채널의 직접 설정과 상속 결과 확인 |

## 이모지

`/emoji` 그룹 전체는 앱 소유자 또는 `BOT_ADMIN_IDS` 사용자 전용입니다.

| 명령 | 기능 |
| --- | --- |
| `/emoji add` | 기존 서버 이모지 `source` 또는 이미지 `image` 중 하나를 지정해 등록 |
| `/emoji list` | 등록된 모델용 별칭·미리보기·사용 상황 확인 |
| `/emoji edit` | 등록된 별칭의 사용 상황 수정 |
| `/emoji remove` | 모델 사용 목록에서 제외. 원본 이모지는 삭제하지 않음 |

`/emoji add`의 `source`에는 커스텀 이모지 markup 또는 숫자 ID를 넣을 수 있습니다. `image`는
256 KiB 이하 PNG/GIF/JPEG/WebP를 받습니다. `source`와 `image`를 동시에 지정할 수 없습니다.

## 동적 prompt / knowledge

기존 `/instruction ...`, `/knowledge ...` 명령도 그대로 Discord slash command로 유지합니다.
두 그룹은 앱 소유자 또는 `BOT_ADMIN_IDS` 사용자 전용입니다.

## 도움말

`/help`는 현재 slash command 구조와 일반 대화 호출 방법을 간단히 보여줍니다.
