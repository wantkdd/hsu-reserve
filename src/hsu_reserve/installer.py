"""`hsu-reserve-mcp setup` / `uninstall`: credentials in the OS keychain + registration in every local AI client found.

Called by install-mac.command / install-windows.bat after `uv tool install`.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path

from .client import PLACES, AuthError, HsuClient, delete_credentials, load_credentials, save_credentials

NAME = "hsu-reserve"
IS_WIN = sys.platform == "win32"
HOME = Path.home()
CODEX_AUTO_APPROVE = ("list_spaces", "check_availability", "my_reservations")


def _appdata() -> Path:
    return Path(os.environ.get("APPDATA", HOME / "AppData" / "Roaming"))


def server_command() -> str:
    """Absolute path of the installed `hsu-reserve-mcp` executable (clients need it; PATH may lack ~/.local/bin)."""
    candidates = [Path(sys.argv[0])]
    if IS_WIN:
        candidates.insert(0, Path(sys.argv[0]).with_suffix(".exe"))
    found = shutil.which("hsu-reserve-mcp")
    if found:
        candidates.append(Path(found))
    for c in candidates:
        if c.is_file():
            return str(c.absolute())
    raise SystemExit("hsu-reserve-mcp 실행 파일 위치를 찾지 못했습니다. 설치 스크립트로 실행해 주세요.")


# ---------------- credentials ----------------


def ask_credentials() -> None:
    try:
        current, _ = load_credentials()
    except AuthError:
        current = None
    if current and _yes(f"저장된 학번 {current} 을(를) 그대로 사용할까요? [Y/n] ", default=True):
        print("  → 기존 계정을 사용합니다.")
        return
    for _ in range(3):
        uid = input("한성대 학번(아이디): ").strip()
        pw = getpass.getpass("비밀번호 (입력해도 화면에 표시되지 않습니다): ")
        if not uid or not pw:
            print("  학번과 비밀번호를 모두 입력해 주세요.")
            continue
        print("  학교 사이트에 로그인해서 확인하는 중...")
        try:
            with HsuClient(uid, pw) as c:
                prof = c.profile(PLACES["sangsang_base"])
        except AuthError:
            print("  ✗ 로그인 실패: 학번/비밀번호를 확인해 주세요.")
            continue
        save_credentials(uid, pw)
        print(
            f"  ✓ {prof.name}님 로그인 확인. 계정은 이 컴퓨터의 {'자격 증명 관리자' if IS_WIN else '키체인'}에만 저장됩니다."
        )
        return
    raise SystemExit("로그인에 3번 실패해서 설치를 중단합니다.")


def _yes(prompt: str, default: bool) -> bool:
    ans = input(prompt).strip().lower()
    return default if not ans else ans in ("y", "yes", "ㅛ", "예", "네")


# ---------------- JSON-config clients ----------------


def _edit_json(path: Path, mutate: Callable[[dict], bool]) -> str:
    """Load → mutate → write with backup. Never clobbers a file we cannot parse (e.g. JSONC with comments)."""
    data: dict = {}
    if path.is_file() and path.read_text(encoding="utf-8").strip():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return f"건너뜀: {path} 형식을 읽지 못했습니다(주석 포함 등). 직접 추가해 주세요."
    if not mutate(data):
        return "변경 없음"
    if path.is_file():
        shutil.copy2(path, path.with_name(path.name + ".hsu-backup"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return f"등록됨 ({path})"


def _json_target(path: Path, key: str, entry: Callable[[str], dict]) -> Callable[[str | None], str]:
    def apply(cmd: str | None) -> str:
        def mutate(data: dict) -> bool:
            servers = data.setdefault(key, {})
            if cmd is None:
                return servers.pop(NAME, None) is not None
            servers[NAME] = entry(cmd)
            return True

        if cmd is None and not path.is_file():
            return "변경 없음"
        return _edit_json(path, mutate)

    return apply


def _claude_desktop_paths() -> list[Path]:
    if IS_WIN:
        paths = [_appdata() / "Claude"]
        local = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData" / "Local")) / "Packages"
        paths += [p / "LocalCache" / "Roaming" / "Claude" for p in local.glob("Claude_*")] if local.is_dir() else []
        return paths
    return [HOME / "Library" / "Application Support" / "Claude"]


def _vscode_user_dir() -> Path:
    return _appdata() / "Code" / "User" if IS_WIN else HOME / "Library" / "Application Support" / "Code" / "User"


def json_clients() -> list[tuple[str, Path, Callable[[str | None], str]]]:
    """(label, directory whose existence means 'installed', apply-function)."""
    std = lambda cmd: {"command": cmd, "args": []}  # noqa: E731
    out = [
        (
            "Claude Desktop",
            d,
            _json_target(d / "claude_desktop_config.json", "mcpServers", std),
        )
        for d in _claude_desktop_paths()
    ]
    out += [
        ("Cursor", HOME / ".cursor", _json_target(HOME / ".cursor" / "mcp.json", "mcpServers", std)),
        ("Gemini CLI", HOME / ".gemini", _json_target(HOME / ".gemini" / "settings.json", "mcpServers", std)),
        (
            "Windsurf",
            HOME / ".codeium" / "windsurf",
            _json_target(HOME / ".codeium" / "windsurf" / "mcp_config.json", "mcpServers", std),
        ),
        (
            "VS Code",
            _vscode_user_dir(),
            _json_target(_vscode_user_dir() / "mcp.json", "servers", lambda cmd: {"type": "stdio", "command": cmd}),
        ),
    ]
    return out


# ---------------- CLI clients (Claude Code, Codex) ----------------


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), capture_output=True, text=True, encoding="utf-8", errors="replace")


def _claude_dir() -> Path:
    return Path(os.environ["CLAUDE_CONFIG_DIR"]) if os.environ.get("CLAUDE_CONFIG_DIR") else HOME / ".claude"


def claude_code(cmd: str | None) -> str | None:
    exe = shutil.which("claude")
    if exe is None:
        return None
    _run(exe, "mcp", "remove", NAME, "-s", "user")
    if cmd is not None:
        r = _run(exe, "mcp", "add", NAME, "-s", "user", "--", cmd)
        if r.returncode != 0:
            return f"실패: {(r.stderr or r.stdout).strip()[:200]}"
    _skill(_claude_dir() / "skills", cmd is not None)
    return "등록됨 (MCP + 스킬)" if cmd is not None else "제거됨"


def _codex_config() -> Path:
    return Path(os.environ.get("CODEX_HOME", HOME / ".codex")) / "config.toml"


def codex(cmd: str | None) -> str | None:
    exe = shutil.which("codex")
    if exe is None:
        return None
    _run(exe, "mcp", "remove", NAME)
    cfg = _codex_config()
    if cfg.is_file():  # drop our per-tool approval tables (written below) so they never dangle
        text = cfg.read_text(encoding="utf-8")
        cleaned = re.sub(rf'\n*\[mcp_servers\.{re.escape(NAME)}\.tools\.\w+\]\napproval_mode = "approve"\n', "\n", text)
        if cleaned != text:
            cfg.write_text(cleaned, encoding="utf-8")
    if cmd is not None:
        r = _run(exe, "mcp", "add", NAME, "--", cmd)
        if r.returncode != 0:
            return f"실패: {(r.stderr or r.stdout).strip()[:200]}"
        with cfg.open("a", encoding="utf-8") as f:
            for tool in CODEX_AUTO_APPROVE:
                f.write(f'\n[mcp_servers.{NAME}.tools.{tool}]\napproval_mode = "approve"\n')
    _skill(cfg.parent / "skills", cmd is not None)
    return "등록됨 (MCP + 스킬, 조회 도구는 자동 승인)" if cmd is not None else "제거됨"


def _skill(skills_dir: Path, install: bool) -> None:
    target = skills_dir / NAME
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    if install:
        target.mkdir(parents=True)
        src = files("hsu_reserve") / "skills" / NAME / "SKILL.md"
        (target / "SKILL.md").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


# ---------------- entry points ----------------


def _register_all(cmd: str | None) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    for label, fn in (("Claude Code", claude_code), ("Codex", codex)):
        msg = fn(cmd)
        if msg is not None:
            results.append((label, msg))
    for label, marker, apply in json_clients():
        if marker.is_dir():
            results.append((label, apply(cmd)))
    return results


def setup() -> None:
    print("=== 한성대 공간 예약 MCP 설정 ===\n")
    ask_credentials()
    cmd = server_command()
    print("\nAI 앱에 등록하는 중...")
    results = _register_all(cmd)
    for label, msg in results:
        print(f"  • {label}: {msg}")
    if not results:
        print(
            "  설치된 AI 앱(Claude Desktop, Claude Code, Codex, Cursor, Gemini CLI, Windsurf, VS Code)을 찾지 못했습니다."
        )
        print(f"  앱 설치 후 이 설치 프로그램을 다시 실행하거나, MCP 설정에 다음 명령을 등록하세요:\n    {cmd}")
    print("\n완료! 열려 있는 AI 앱은 완전히 종료했다가 다시 실행해야 적용됩니다.")
    print('사용 예: "내일 오후 3시에 코딩라운지 자리 있어?", "금요일 2시~4시 IB105 예약해줘"')


def uninstall() -> None:
    print("=== 한성대 공간 예약 MCP 제거 ===\n")
    for label, msg in _register_all(None):
        print(f"  • {label}: {msg}")
    delete_credentials()
    print("  • 저장된 학교 계정 삭제됨")
