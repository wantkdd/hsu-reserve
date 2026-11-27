# hsu-reserve

한성대학교 **상상베이스**와 **코딩라운지** 공간을 AI 에이전트에게 말로 조회하고 예약합니다.

[설치부터 사용까지 시연 영상](https://github.com/wantkdd/hsu-reserve/releases/download/v0.2.0/hsu-reserve-demo.mp4)

> "내일 3시에 코딩라운지 빈 방 있어?"
> "102호 오늘 12시부터 14시까지 예약해줘"
> "내 예약 보여줘" / "그거 취소해줘"

## 설치
먼저 사용할 AI 앱을 설치하고 로그인해 두세요. 웹 ChatGPT·Gemini가 아닌, 로컬 MCP를 지원하는 PC 앱이 필요합니다.

**파일로 설치**: [ZIP 다운로드](https://github.com/wantkdd/hsu-reserve/archive/refs/heads/master.zip) → 압축 해제 → Mac은 `install-mac.command`, Windows는 `install-windows.bat` 실행.

Mac에서 보안 경고로 열리지 않으면 아래 터미널 명령을 사용하세요.


**macOS**: 터미널에 붙여넣고 엔터

```sh
curl -LsSf https://raw.githubusercontent.com/wantkdd/hsu-reserve/master/install.sh | sh
```

**Windows**: PowerShell에 붙여넣고 엔터

```powershell
irm https://raw.githubusercontent.com/wantkdd/hsu-reserve/master/install.ps1 | iex
```

1. 한성대 학번과 비밀번호를 입력합니다.
2. 설치된 AI 앱에 자동으로 연결됩니다.
3. AI 앱을 완전히 종료했다가 다시 실행하면 끝입니다.

지원 앱: Claude Desktop, Claude Code, Codex, Cursor, Gemini CLI, Windsurf, VS Code
검증: macOS의 Claude Code·Codex에서 확인했습니다. Windows와 나머지 앱은 실제 사용 검증 전입니다.

비밀번호는 내 컴퓨터의 키체인(Windows는 자격 증명 관리자)에만 저장되고, 한성대 홈페이지 로그인에만 사용됩니다.

## 업데이트

설치 명령을 다시 실행하면 됩니다. 비밀번호를 바꿨을 때도 같습니다.

## 삭제

ZIP을 압축 해제한 폴더에서 Mac은 `uninstall-mac.command`, Windows는 `uninstall-windows.bat`를 실행하세요.

## 주의

- 코딩라운지는 예약 후 관리자 승인이 필요하고, 이용할 때 공학관 A동 1층 안내데스크에 신분증을 지참해야 합니다.
- 공간별 인원·시간 규칙은 학교 안내를 따릅니다. 규칙에 맞지 않으면 AI가 예약 전에 알려줍니다.
