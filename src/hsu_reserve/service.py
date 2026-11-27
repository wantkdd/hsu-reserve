"""High-level operations used by the MCP tools: availability, alternatives, reserve+verify, cancel+verify."""

from __future__ import annotations

import re
import threading
from datetime import date, datetime, timedelta

from .client import (
    KST,
    PLACES,
    WEEKDAYS_KO,
    DayAvailability,
    HsuClient,
    HsuError,
    MyReservation,
    Place,
    Room,
    hours_between,
    normalize_time,
    resolve_place,
)

ACTIVE_STATUSES = ("승인대기", "승인")


def parse_day(text: str, today: date | None = None) -> date:
    today = today or datetime.now(KST).date()
    t = text.strip().replace(" ", "")
    rel = {"오늘": 0, "today": 0, "내일": 1, "tomorrow": 1, "모레": 2, "글피": 3}
    if t.lower() in rel:
        return today + timedelta(days=rel[t.lower()])
    m = re.fullmatch(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", t)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(?:(\d{4})년)?(\d{1,2})(?:월|[-./])(\d{1,2})일?", t)
    if m:
        d = date(int(m[1]) if m[1] else today.year, int(m[2]), int(m[3]))
        if not m[1] and d < today - timedelta(days=30):
            d = d.replace(year=d.year + 1)
        return d
    raise HsuError(f"날짜 형식 오류: {text!r} (예: 2026-11-20, 오늘, 내일)")


def room_capacity(place: Place, room: Room) -> tuple[int | None, int | None]:
    """(min_people, max_people) from each place's posted usage guide."""
    if place.key == "sangsang_base":
        return (6, None) if "IB111" in room.name else (3, None)
    if place.key == "coding_lounge":
        m = re.search(r"(\d{3})", room.name)
        if m:
            return (None, 12) if int(m[1]) <= 105 else (None, 8)
    return (None, None)


def windows(hours: tuple[str, ...] | list[str], slot_minutes: int) -> list[str]:
    """Merge consecutive slot starts into "HH:MM-HH:MM" ranges (hyphen: a pair of "~" renders as strikethrough)."""
    out: list[str] = []
    start = prev = None
    step = timedelta(minutes=slot_minutes)
    for h in hours:
        t = datetime.strptime(h, "%H:%M")
        if prev is not None and t == prev + step:
            prev = t
            continue
        if start is not None:
            out.append(f"{start:%H:%M}-{prev + step:%H:%M}")
        start = prev = t
    if start is not None:
        out.append(f"{start:%H:%M}-{prev + step:%H:%M}")
    return out


class ReservationService:
    def __init__(self, client: HsuClient | None = None):
        self.client = client or HsuClient()
        self._lock = threading.Lock()

    # ---------- helpers ----------

    def _places(self, place: str | None) -> list[Place]:
        return list(PLACES.values()) if not place or place in ("all", "전체") else [resolve_place(place)]

    @staticmethod
    def _hour_status(av: DayAvailability, hours: list[str]) -> str | None:
        """None if every requested hour is bookable, else a short reason."""
        if av.closed_reason:
            return av.closed_reason
        taken = [h for h in hours if h in av.taken_hours]
        if taken:
            return f"이미 예약됨({', '.join(taken)})"
        not_yet = [h for h in hours if h in av.not_yet_hours]
        if not_yet:
            return f"지난 시간이거나 신청 마감 시간 이내({', '.join(not_yet)})"
        missing = [h for h in hours if h not in av.open_hours]
        if missing:
            return f"운영 시간 외({', '.join(missing)})"
        return None

    def _alternatives(
        self,
        day: date,
        hours: list[str],
        avs: dict[str, dict[str, DayAvailability]],
        targets: list[tuple[str, str]],
        limit: int = 6,
    ) -> dict:
        """Alternatives for a failed request.

        same_time_other_rooms: every room (all places in `avs`) open for the exact hours, minus targets.
        other_times: same-length free blocks in the target (place_key, room_name) rooms, nearest start first.
        other_days: only when both above are empty — same hours in target rooms on the next 7 days.
        """
        n = len(hours)
        want = datetime.strptime(hours[0], "%H:%M")
        same_time = [
            f"{PLACES[pk].name} {name}"
            for pk, by_room in avs.items()
            for name, av in by_room.items()
            if (pk, name) not in targets and self._hour_status(av, hours) is None
        ]
        candidates: list[tuple[float, str]] = []
        for pk, name in targets:
            av = avs[pk][name]
            step = timedelta(minutes=av.slot_minutes)
            opens = [datetime.strptime(h, "%H:%M") for h in av.open_hours]
            for i in range(len(opens) - n + 1):
                if opens[i + n - 1] - opens[i] == step * (n - 1):  # n consecutive slots
                    s = opens[i]
                    candidates.append(
                        (abs((s - want).total_seconds()), f"{PLACES[pk].name} {name} {s:%H:%M}-{s + step * n:%H:%M}")
                    )
        candidates.sort()
        result = {"same_time_other_rooms": same_time, "other_times": [c for _, c in candidates[:limit]]}
        if not same_time and not candidates:
            result["other_days"] = self._other_days(day, hours, targets, limit)
        return result

    def _other_days(self, day: date, hours: list[str], targets: list[tuple[str, str]], limit: int) -> list[str]:
        found: list[str] = []
        rooms = {(p.key, r.name): r for p in PLACES.values() for r in self.client.rooms(p)}
        for offset in range(1, 8):
            d = day + timedelta(days=offset)
            for pk, name in targets:
                av = self.client.day_availability(PLACES[pk], rooms[(pk, name)], d)
                if self._hour_status(av, hours) is None:
                    found.append(f"{d}({WEEKDAYS_KO[d.weekday()]}) {PLACES[pk].name} {name}")
            if len(found) >= limit:
                break
        return found[:limit]

    def _collect(self, places: list[Place], day: date) -> dict[str, dict[str, DayAvailability]]:
        out: dict[str, dict[str, DayAvailability]] = {}
        for p in places:
            out[p.key] = {r.name: self.client.day_availability(p, r, day) for r in self.client.rooms(p)}
        return out

    # ---------- public operations ----------

    def list_spaces(self) -> dict:
        with self._lock:
            now = datetime.now(KST)
            result = {"now": now.strftime("%Y-%m-%d %H:%M (%a) KST"), "places": []}
            for p in PLACES.values():
                rooms = []
                for r in self.client.rooms(p):
                    s = self.client.room_rules(p, r)
                    mn, mx = room_capacity(p, r)
                    rooms.append(
                        {
                            "room": r.name,
                            "hours": f"{s.hours[0]}-{s.slot_end(s.hours[-1])}" if s.hours else None,
                            "slot_minutes": s.slot_minutes,
                            "weekdays": ",".join(s.allowed_weekdays),
                            "bookable_period": f"{s.period_start}/{s.period_end}",
                            "book_at_least_hours_before": s.lead_hours,
                            "min_people": mn,
                            "max_people": mx,
                        }
                    )
                guide = self.client.room_setup(p, self.client.rooms(p)[0]).guide if rooms else ""
                result["places"].append(
                    {
                        "place": p.name,
                        "key": p.key,
                        "max_hours_per_day": p.max_hours_per_day,
                        "note": p.note,
                        "guide": guide,
                        "rooms": rooms,
                    }
                )
            return result

    def check_availability(
        self,
        day: str,
        start: str | None = None,
        end: str | None = None,
        place: str | None = None,
        room: str | None = None,
    ) -> dict:
        with self._lock:
            d = parse_day(day)
            places = self._places(place)
            avs = self._collect(places, d)
            targets = self._targets(places, room)
            header = f"{d} ({WEEKDAYS_KO[d.weekday()]})"

            if start is None:
                schedule = [
                    {
                        "place": PLACES[pk].name,
                        "room": name,
                        "free": windows(avs[pk][name].open_hours, avs[pk][name].slot_minutes),
                        "taken": windows(avs[pk][name].taken_hours, avs[pk][name].slot_minutes),
                        "closed_reason": avs[pk][name].closed_reason,
                    }
                    for pk, name in targets
                ]
                return {"date": header, "mode": "day_schedule", "rooms": schedule}

            slot = avs[targets[0][0]][targets[0][1]].slot_minutes
            s = normalize_time(start)
            hours = hours_between(s, normalize_time(end) if end else av_end(s, slot), slot)
            available, unavailable = [], []
            for pk, name in targets:
                reason = self._hour_status(avs[pk][name], hours)
                entry = {"place": PLACES[pk].name, "room": name}
                if reason:
                    unavailable.append({**entry, "reason": reason})
                else:
                    available.append(entry)
            result = {
                "date": header,
                "time": f"{hours[0]}-{av_end(hours[-1], slot)}",
                "available": available,
                "unavailable": unavailable,
            }
            if not available:
                others = [q for q in PLACES.values() if q not in places]
                all_avs = {**avs, **self._collect(others, d)} if others else avs
                result["alternatives"] = self._alternatives(d, hours, all_avs, targets)
            return result

    def _targets(self, places: list[Place], room: str | None) -> list[tuple[str, str]]:
        """(place_key, room_name) pairs to inspect; a room filter may match in any of the given places."""
        if not room:
            return [(p.key, r.name) for p in places for r in self.client.rooms(p)]
        found, errors = [], []
        for p in places:
            try:
                found.append((p.key, self.client.find_room(p, room).name))
            except HsuError as exc:
                errors.append(str(exc))
        if not found:
            raise HsuError(" / ".join(errors))
        return found

    def my_reservations(self, place: str | None = None, include_past: bool = False) -> dict:
        with self._lock:
            today = datetime.now(KST).date()
            rows = []
            for p in self._places(place):
                for r in self.client.my_reservations(p, pages=3 if include_past else 1):
                    if not include_past and (r.day < today or r.status not in ACTIVE_STATUSES):
                        continue
                    rows.append(_res_dict(r))
            rows.sort(key=lambda r: (r["date"], r["time"]))
            return {"reservations": rows}

    def reserve(
        self,
        place: str,
        room: str,
        day: str,
        start: str,
        end: str,
        members: list[str] | None = None,
        headcount: int | None = None,
        purpose: str | None = None,
        extra_fields: dict[str, str] | None = None,
    ) -> dict:
        with self._lock:
            p = resolve_place(place)
            r = self.client.find_room(p, room)
            d = parse_day(day)
            setup = self.client.room_setup(p, r)
            hours = hours_between(normalize_time(start), normalize_time(end), setup.slot_minutes)
            label = (
                f"{p.name} {r.name} {d}({WEEKDAYS_KO[d.weekday()]}) {hours[0]}-{av_end(hours[-1], setup.slot_minutes)}"
            )

            # 1) availability — authoritative per-room check right before submitting
            av = self.client.day_availability(p, r, d, authoritative=True)
            reason = self._hour_status(av, hours)
            if reason:
                avs = self._collect(list(PLACES.values()), d)
                return {
                    "status": "unavailable",
                    "request": label,
                    "reason": reason,
                    "alternatives": self._alternatives(d, hours, avs, [(p.key, r.name)]),
                }

            # 2) daily limit (posted rule; violating bookings get cancelled by staff)
            mine_today = [x for x in self.client.my_reservations(p) if x.day == d and x.status in ACTIVE_STATUSES]
            already = sum(len(x.hours) for x in mine_today) * setup.slot_minutes / 60
            total = already + len(hours) * setup.slot_minutes / 60
            if total > p.max_hours_per_day:
                return {
                    "status": "rule_violation",
                    "request": label,
                    "reason": f"{p.name} 하루 최대 이용시간은 {p.max_hours_per_day}시간입니다. 그날 이미 {already:g}시간 예약되어 있어 합계 {total:g}시간이 됩니다.",
                    "existing": [_res_dict(x) for x in mine_today],
                }

            # 3) people count rules + required form fields
            mn, mx = room_capacity(p, r)
            people = headcount if headcount is not None else (len(members) if members else None)
            if people is not None and mn is not None and people < mn:
                return {
                    "status": "rule_violation",
                    "request": label,
                    "reason": f"{r.name} 최소 이용 인원은 {mn}명입니다 (입력: {people}명).",
                }
            if people is not None and mx is not None and people > mx:
                return {
                    "status": "rule_violation",
                    "request": label,
                    "reason": f"{r.name} 최대 수용 인원은 {mx}명입니다 (입력: {people}명).",
                }

            values: dict[int, str] = {}
            missing: list[str] = []
            extra = extra_fields or {}
            for it in setup.items:
                v: str | None = extra.get(it.name)
                if v is None:
                    if "성명" in it.name or "학번" in it.name:
                        v = ", ".join(members) if members else None
                    elif "인원" in it.name:
                        v = str(people) if people is not None else None
                    elif "용도" in it.name or "목적" in it.name:
                        v = purpose
                if v:
                    values[it.index] = v
                elif it.required:
                    missing.append(it.name + (f" (예: {it.hint})" if it.hint else ""))
            if missing:
                return {
                    "status": "needs_input",
                    "request": label,
                    "missing_fields": missing,
                    "hint": "members=['이름/학번', ...], headcount=숫자, purpose='용도' 로 다시 호출하세요.",
                }

            # 4) submit
            form = self.client.build_form(p, setup, d, hours, values)
            ok, message = self.client.submit(p, form)
            if not ok:
                return {"status": "failed", "request": label, "server_message": message}

            # 5) verify: my list has an active row for exactly this room/date/hours, and the slots read as taken
            mine = self.client.my_reservations(p)
            match = next(
                (
                    x
                    for x in mine
                    if x.room == r.name and x.day == d and set(hours) <= set(x.hours) and x.status in ACTIVE_STATUSES
                ),
                None,
            )
            taken_now = set(self.client.taken_hours(p, r, d))
            verified = match is not None and set(hours) <= taken_now
            result = {
                "status": "reserved" if verified else "submitted_unverified",
                "request": label,
                "server_message": message,
                "verified": verified,
                "verification": {
                    "found_in_my_reservations": match is not None,
                    "slots_marked_taken": sorted(set(hours) & taken_now),
                },
            }
            if match is not None:
                result["reservation"] = _res_dict(match)
                if match.status == "승인대기":
                    result["note"] = "관리자 승인 대기 상태입니다. 승인 후 이용 가능합니다."
            if p.key == "coding_lounge":
                result["reminder"] = "이용 시 공학관 A동 1층 코딩라운지 안내데스크 방문, 신분증 지참."
            return result

    def cancel(self, place: str, reservation_id: int) -> dict:
        with self._lock:
            p = resolve_place(place)
            pages = 1
            target = next((x for x in self.client.my_reservations(p) if x.id == reservation_id), None)
            if target is None:
                pages = 3
                target = next((x for x in self.client.my_reservations(p, pages=pages) if x.id == reservation_id), None)
            if target is None:
                return {
                    "status": "not_found",
                    "reason": f"{p.name} 내 신청내역(최근 3페이지)에 #{reservation_id} 가 없습니다.",
                }
            if not target.cancellable:
                return {"status": "not_cancellable", "reservation": _res_dict(target)}
            ok, message = self.client.cancel(p, reservation_id)
            after = next((x for x in self.client.my_reservations(p, pages=pages) if x.id == reservation_id), None)
            r = self.client.find_room(p, target.room)
            still_taken = sorted(set(target.hours) & set(self.client.taken_hours(p, r, target.day)))
            verified = ok and after is not None and after.status not in ACTIVE_STATUSES
            return {
                "status": "cancelled" if verified else "cancel_unverified",
                "server_message": message,
                "reservation": _res_dict(after or target),
                "slots_still_taken": still_taken,
            }


def av_end(start: str, minutes: int) -> str:
    return (datetime.strptime(start, "%H:%M") + timedelta(minutes=minutes)).strftime("%H:%M")


def _res_dict(r: MyReservation) -> dict:
    return {
        "id": r.id,
        "place": r.place,
        "room": r.room,
        "date": r.day.isoformat(),
        "weekday": WEEKDAYS_KO[r.day.weekday()],
        "time": ", ".join(windows(r.hours, 60)),
        "slots": list(r.hours),
        "status": r.status,
        "cancellable": r.cancellable,
    }
