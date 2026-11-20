# hsu-reserve

한성대학교 **상상베이스**(상상관 B2: IB101~IB108, 세미나실 IB111)와 **코딩라운지**(공학관 A동: 세미나실 101~113호)를 AI에게 말로 조회·예약하는 도구(MCP 서버 + 스킬).

- "내일 3시에 자리 있어?" → 빈 공간을 알려줌. 다 찼으면 같은 시간 다른 공간 / 같은 공간 다른 시간 / 다른 날을 대안으로 제안
- "금요일 2~4시 IB105 예약해줘" → 규칙(인원·하루 최대 시간·요일·기간) 확인 → 신청 → **나의 신청내역과 시간표를 다시 조회해서 검증**
- "내 예약 보여줘" / "그거 취소해줘"

## 설치 (원클릭)

1. 이 저장소를 zip으로 받아서 압축 해제
2. 실행
   - **macOS**: `install-mac.command` 더블클릭
     - "확인되지 않은 개발자" 경고가 뜨면 파일을 **우클릭 → 열기**
   - **Windows**: `install-windows.bat` 더블클릭
     - "Windows의 PC 보호" 창이 뜨면 **추가 정보 → 실행**
3. 학번/비밀번호 입력 → 학교 사이트에 실제로 로그인해서 확인
4. 설치된 AI 앱에 자동 등록 → **AI 앱을 완전히 종료했다가 다시 실행**

설치 프로그램이 하는 일:
- [uv](https://docs.astral.sh/uv/)(파이썬 실행 도구)가 없으면 설치. 파이썬이 따로 없어도 됨
- 이 폴더의 프로그램을 `hsu-reserve-mcp` 명령으로 설치
- 학교 계정을 **OS 보안 저장소**(macOS 키체인 / Windows 자격 증명 관리자)에 저장
- 찾은 앱에 등록 (기존 설정 파일은 `*.hsu-backup`으로 백업)

| 앱 | 등록 위치 |
|---|---|
| Claude Desktop (무료 플랜 가능) | `claude_desktop_config.json` |
| Claude Code | `claude mcp add -s user` + 스킬 `~/.claude/skills/hsu-reserve` |
| Codex | `codex mcp add` + 스킬 `~/.codex/skills/hsu-reserve`, 조회 도구 3개 자동 승인 |
| Cursor | `~/.cursor/mcp.json` |
| Gemini CLI | `~/.gemini/settings.json` |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` |
| VS Code (Copilot) | 사용자 `mcp.json` |

AI 앱을 나중에 설치했다면 설치 파일을 다시 실행하면 됩니다. 비밀번호를 바꿨을 때도 다시 실행하세요.
제거: `uninstall-mac.command` / `uninstall-windows.bat` (앱 등록, 스킬, 저장된 계정까지 삭제).

ChatGPT·Gemini 웹/모바일 앱은 이 방식(내 컴퓨터에서 실행)을 지원하지 않습니다.

### 보안

- 비밀번호는 내 컴퓨터의 OS 보안 저장소에만 저장되고, `https://www.hansung.ac.kr` 로그인에만 사용됩니다.
- 사람이 요청할 때 한 건씩 조회·예약합니다. 빈자리를 계속 감시하다 자동으로 잡는 기능은 없습니다.

## 수동 설정 (다른 MCP 앱)

```bash
uv tool install .            # 이 폴더에서
hsu-reserve-mcp setup        # 계정 저장 + 앱 자동 등록
```

MCP 설정에는 `hsu-reserve-mcp`의 절대 경로를 인자 없이 명령으로 등록합니다(`uv tool dir --bin`로 위치 확인).
계정은 환경변수 `HSU_ID` / `HSU_PW`로 넘겨도 되고, 그러면 키체인보다 우선합니다.

## 도구

| 도구 | 설명 |
|---|---|
| `list_spaces` | 공간 목록과 규칙(운영시간, 요일, 예약 가능 기간, 신청 마감, 인원, 필수 입력 항목, 이용 안내), 현재 시각 |
| `check_availability(date, start?, end?, place?, room?)` | 시간을 주면 그 시간에 빈 공간과 대안을, 생략하면 공간별 빈 시간표를 반환 |
| `reserve(place, room, date, start, end, members?, headcount?, purpose?, extra_fields?)` | 예약 후 검증. `status`: `reserved` / `unavailable` / `rule_violation` / `needs_input` / `failed` / `submitted_unverified` |
| `my_reservations(place?, include_past?)` | 내 예약(기본: 앞으로 남은 승인대기·승인 건) |
| `cancel_reservation(place, reservation_id)` | 취소 후 검증 |

## 개발

```bash
uv sync
HSU_ID=... HSU_PW=... uv run hsu-reserve-mcp      # stdio MCP 서버
```

사이트(K2Web `resve` 모듈)의 브라우저 JS가 보내는 요청을 그대로 재현합니다 (`src/hsu_reserve/client.py`).

| 단계 | 요청 |
|---|---|
| 로그인 | `POST /hnuLogin/{site}/loginProcess.do` |
| 공간 목록 | `POST /resve/{site}/{fnct}/{group}/getChildrenClList.do` (XHR 헤더 필요) |
| 규칙·항목 | `artclSetupList.do`, `resveSetup.do`, `itemList.do` |
| 예약된 시간 | `resveList.do` (`resveSpceSeq`, `resveDeStr`) |
| 신청 | `artclRegist.do`: XHR 검증 요청(실제로 등록되지 않음) → 일반 POST → `/message/message.do?message=예약이(가) 완료 되었습니다.` |
| 신청내역 | `artclView.do` (`page`, 최신순) |
| 취소 | `GET /{id}/artclCancle.do` |

| 장소 | site | fnct |
|---|---|---|
| 상상베이스 | `onestop` | 16 |
| 코딩라운지 | `cncschool` | 7 |

하루 최대 시간(상상베이스 3시간, 코딩라운지 6시간)과 인원 규칙(`service.room_capacity`)은 각 공간의 이용 안내 문구에서 가져와 코드에 넣어 두었습니다. 학교가 안내를 바꾸면 이 부분도 고쳐야 합니다.
