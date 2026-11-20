---
name: hsu-reserve
description: 한성대학교 상상베이스(IB101~IB108, 세미나실 IB111)·코딩라운지(세미나실 101~113호) 빈자리 조회, 예약, 예약 검증, 내 예약 조회, 취소. "자리 있어?", "예약해줘", "내 예약", "취소해줘" 같은 한성대 공간 예약 요청에 사용. hsu-reserve MCP 도구가 필요함.
---

# 한성대 공간 예약

`hsu-reserve` MCP 서버 도구: `list_spaces`, `check_availability`, `reserve`, `my_reservations`, `cancel_reservation`.
모든 시간은 KST. 날짜를 모르면 `list_spaces`의 `now`로 오늘 날짜를 확인하고 "내일/금요일" 같은 표현을 YYYY-MM-DD로 바꿔서 호출.

## "이 시간에 자리 있어?"
1. `check_availability(date, start, end, place?, room?)` 호출. 장소를 말하지 않았으면 place 생략(두 곳 모두 조회).
2. `available`이 있으면 공간 목록을 짧게 알려줌.
3. 비어 있으면 `unavailable[].reason`(이미 예약/요일 불가/기간 밖/마감)을 말하고 `alternatives`를 제안:
   `same_time_other_rooms` → `other_times` → `other_days` 순서.
4. 시간을 말하지 않고 "내일 어디 비어?"라고 물으면 start를 생략해 공간별 `free` 시간표를 요약.

## "예약해줘"
1. 필요한 값: 장소, 공간, 날짜, 시작/종료 시각.
   - 상상베이스: 전체 이용자 `이름/학번` 목록(members)과 인원수가 필수. 그룹스터디룸 최소 3명, 세미나실 IB111 최소 6명 + 용도(purpose) 필수, 24시간 전까지만 신청 가능.
   - 코딩라운지: 추가 항목 없음. 101~105호 최대 12명, 106~113호 최대 8명. 평일만 가능.
   - 하루 최대: 상상베이스 3시간, 코딩라운지 6시간.
   이용자 명단을 모르면 **지어내지 말고 사용자에게 물어볼 것**.
2. 공간을 지정하지 않았으면 먼저 `check_availability`로 빈 곳을 찾아 하나를 제안하고 확인받은 뒤 예약.
3. `reserve(...)` 결과 `status`별 응답:
   - `reserved`: 검증 완료(내 신청내역에 있고 해당 시간이 예약됨으로 바뀜). 예약번호(id), 공간, 시간, 상태(승인/승인대기)를 알려줌. 승인대기면 관리자 승인 필요하다고 안내. 코딩라운지면 `reminder`도 전달.
   - `unavailable`: 이미 찬 이유와 `alternatives`를 제안하고, 원하면 대안으로 다시 예약.
   - `rule_violation`: 규칙(인원/하루 최대 시간)을 설명.
   - `needs_input`: `missing_fields`를 사용자에게 물어봄.
   - `failed`: `server_message`를 그대로 전달.
   - `submitted_unverified`: 제출은 됐지만 검증 실패. 예약됐다고 단정하지 말고 `my_reservations`로 다시 확인.
4. `status`가 `reserved`가 아니면 절대 "예약됐다"고 말하지 않음.

## 내 예약 / 취소
- `my_reservations(place?)`: 오늘 이후 승인대기·승인 건. 지난 건까지 보려면 `include_past=true`.
- 취소: 대상이 모호하면 `my_reservations`로 목록을 보여주고 확인받은 뒤 `cancel_reservation(place, reservation_id)`. `status: cancelled`일 때만 취소 완료라고 말함.
