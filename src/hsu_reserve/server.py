"""MCP server (stdio) exposing Hansung University 상상베이스 / 코딩라운지 reservations."""

from __future__ import annotations

import logging
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer

from .client import HsuError
from .service import ReservationService

INSTRUCTIONS = """\
한성대학교 공간 예약 도구 (상상베이스: 상상관 B2 IB101~IB108·세미나실 IB111 / 코딩라운지: 공학관 A동 세미나실 101~113호).
- 시간은 모두 KST. 날짜는 YYYY-MM-DD 또는 오늘/내일/모레. 현재 시각이 필요하면 list_spaces 의 now 를 보세요.
- "자리 있어?" → check_availability. 비어있지 않으면 응답의 alternatives 를 그대로 제안하세요.
- "예약해줘" → reserve. status 가 reserved(검증 완료)일 때만 예약됐다고 말하세요.
  unavailable/rule_violation/needs_input/failed/submitted_unverified 는 이유와 대안을 사용자에게 알리세요.
- 상상베이스는 전체 이용자 '이름/학번' 목록과 인원수가 필수(그룹스터디룸 최소 3인, IB111 최소 6인·용도 필수). 모르면 사용자에게 물어보세요. 임의로 만들지 마세요.
- 하루 최대: 상상베이스 3시간, 코딩라운지 6시간.
"""

mcp = MCPServer("hsu-reserve", instructions=INSTRUCTIONS)
logging.getLogger("httpx").setLevel(logging.WARNING)
_service: ReservationService | None = None


def service() -> ReservationService:
    global _service
    if _service is None:
        _service = ReservationService()
    return _service


def _run(fn, *args: Any, **kwargs: Any) -> dict:
    try:
        return fn(*args, **kwargs)
    except HsuError as exc:
        return {"status": "error", "error": str(exc)}


@mcp.tool()
def list_spaces() -> dict:
    """예약 가능한 장소/공간 목록과 규칙(운영시간, 요일, 예약가능기간, 최소인원, 필수 입력항목, 이용안내), 현재 시각(KST)."""
    return _run(service().list_spaces)


@mcp.tool()
def check_availability(
    date: str,
    start: str | None = None,
    end: str | None = None,
    place: str | None = None,
    room: str | None = None,
) -> dict:
    """빈 자리 조회.

    date: '2026-11-20' / '오늘' / '내일'.
    start, end: 'HH:MM' (end 생략 시 1시간). 둘 다 생략하면 그날 공간별 빈 시간표를 반환.
    place: '상상베이스' | '코딩라운지' | 생략(둘 다).
    room: 'IB105', '세미나실 101호', '101' 등 (생략 시 전체 공간).
    해당 시간에 빈 곳이 없으면 alternatives(같은 시간 다른 공간, 같은 공간 다른 시간)를 함께 반환.
    """
    return _run(service().check_availability, date, start, end, place, room)


@mcp.tool()
def reserve(
    place: str,
    room: str,
    date: str,
    start: str,
    end: str,
    members: list[str] | None = None,
    headcount: int | None = None,
    purpose: str | None = None,
    extra_fields: dict[str, str] | None = None,
) -> dict:
    """공간 예약 후 '나의 신청내역'과 예약현황을 다시 조회해 검증까지 수행.

    place: '상상베이스' | '코딩라운지'. room: 'IB105', '세미나실 101호' 등.
    date: 'YYYY-MM-DD'/'내일'. start/end: 'HH:MM' (1시간 단위, end 는 종료 시각).
    members: 전체 이용자 ['이름/학번', ...] (상상베이스 필수). headcount: 총 인원 (생략 시 members 수).
    purpose: 용도 (상상베이스 세미나실 IB111 필수). extra_fields: 그 외 신청서 항목 {항목명: 값}.
    status: reserved | unavailable(+alternatives) | rule_violation | needs_input | failed | submitted_unverified.
    """
    return _run(service().reserve, place, room, date, start, end, members, headcount, purpose, extra_fields)


@mcp.tool()
def my_reservations(place: str | None = None, include_past: bool = False) -> dict:
    """내 예약 목록. 기본은 오늘 이후의 승인대기/승인 건만. include_past=True 면 지난/취소 건 포함."""
    return _run(service().my_reservations, place, include_past)


@mcp.tool()
def cancel_reservation(place: str, reservation_id: int) -> dict:
    """내 예약 취소 후 목록을 다시 조회해 취소 상태를 검증. reservation_id 는 my_reservations 의 id."""
    return _run(service().cancel, place, reservation_id)


def main() -> None:
    """No args: stdio MCP server (what AI clients launch). `setup` / `uninstall`: installer steps."""
    command = sys.argv[1] if len(sys.argv) > 1 else None
    if command in ("setup", "uninstall"):
        from . import installer

        try:
            getattr(installer, command)()
        except KeyboardInterrupt:
            raise SystemExit("\n중단되었습니다.") from None
        return
    if command is not None:
        raise SystemExit("사용법: hsu-reserve-mcp [setup|uninstall]  (인자 없이 실행하면 MCP 서버)")
    mcp.run()


if __name__ == "__main__":
    main()
