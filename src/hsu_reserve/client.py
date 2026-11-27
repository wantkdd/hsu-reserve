"""HTTP client for the Hansung University K2Web space reservation module (`/resve/...`).

Flow mirrors the site's own JS (artclRegistView.js / common.js):
  calendar     POST /resve/{site}/{fnct}/artclList.do   (public; one month, every room) → availability
  room setup   POST /resve/{site}/{fnct}/artclSetupList.do, resveSetup.do, itemList.do (public; disk-cached)
  login        POST /hnuLogin/{site}/loginProcess.do
  taken hours  POST /resve/{site}/{fnct}/resveList.do  (resveSpceSeq, resveDeStr) — authoritative pre-submit check
  submit       POST /resve/{site}/{fnct}/artclRegist.do (AJAX validation, then real form POST)
  my list      POST /resve/{site}/{fnct}/artclView.do   (page=N)
  cancel       GET  /resve/{site}/{fnct}/{id}/artclCancle.do

The site sits behind a WAF that rejects bursts, so every request is spaced (MIN_REQUEST_INTERVAL),
availability comes from one calendar page per place/month, and static room setup is cached on disk.
"""

from __future__ import annotations

import json
import os
import re
import time
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
import keyring
import keyring.errors
from bs4 import BeautifulSoup

BASE_URL = "https://www.hansung.ac.kr"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) hsu-reserve"
KST = ZoneInfo("Asia/Seoul")
WEEKDAYS_KO = "월화수목금토일"
MIN_REQUEST_INTERVAL = 0.35  # seconds between requests to the school site
CALENDAR_TTL = 60.0  # seconds an in-memory month calendar stays fresh
SETUP_TTL = 12 * 3600.0  # seconds static room setup stays cached on disk
_TIME_RANGE = re.compile(r"(\d{1,2}):(\d{2})\s*~\s*(\d{1,2}):(\d{2})")


def _check_rejected(resp: httpx.Response) -> None:
    if "<title>Request Rejected</title>" in resp.text[:300]:
        sid = re.search(r"support ID is: <?(\d+)", resp.text)
        raise HsuError(
            "학교 사이트 보안장비가 요청을 거절했습니다. 원인은 확인되지 않았습니다. 잠시 후 다시 시도하세요."
            + (f" 지원 ID: {sid[1]}" if sid else "")
        )


def _cache_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "hsu-reserve"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "hsu-reserve"


IDENTITY_NAMES = {
    "IDENTITY_00001": "교수",
    "IDENTITY_00002": "직원",
    "IDENTITY_00003": "조교",
    "IDENTITY_00004": "강사",
    "IDENTITY_00005": "학술연구원",
    "IDENTITY_00006": "기타교수",
    "IDENTITY_00007": "학사(재학생)",
    "IDENTITY_00008": "학사(졸업생)",
    "IDENTITY_00009": "대학원(재학생)",
    "IDENTITY_00010": "대학원(졸업생)",
}


class HsuError(RuntimeError):
    pass


class AuthError(HsuError):
    pass


@dataclass(frozen=True)
class Place:
    key: str
    name: str
    site_id: str
    fnct_no: int
    max_hours_per_day: int
    note: str


PLACES: dict[str, Place] = {
    "sangsang_base": Place(
        key="sangsang_base",
        name="상상베이스",
        site_id="onestop",
        fnct_no=16,
        max_hours_per_day=3,
        note="상상관 B2. 그룹스터디룸 IB101-IB108(최소 3인, 합실 5인), 세미나실 IB111(최소 6인, 관리자 승인).",
    ),
    "coding_lounge": Place(
        key="coding_lounge",
        name="코딩라운지",
        site_id="cncschool",
        fnct_no=7,
        max_hours_per_day=6,
        note="공학관 A동 1층. 세미나실 101-105호(최대 12명), 106-113호(최대 8명). 예약 후 안내데스크 방문·신분증 지참.",
    ),
}


def resolve_place(key_or_name: str) -> Place:
    norm = key_or_name.replace(" ", "").lower()
    for p in PLACES.values():
        if norm in (p.key, p.name):
            return p
    if "상상" in norm or "베이스" in norm or "base" in norm:
        return PLACES["sangsang_base"]
    if "코딩" in norm or "라운지" in norm or "coding" in norm:
        return PLACES["coding_lounge"]
    raise HsuError(f"알 수 없는 장소: {key_or_name!r}. 가능한 값: {', '.join(PLACES)} / 상상베이스, 코딩라운지")


@dataclass(frozen=True)
class Room:
    seq: int
    name: str


@dataclass(frozen=True)
class FormItem:
    index: int  # 1-based, maps to addItem{index}
    name: str
    kind: str  # TEXT | SINGLE | MULTI | ""
    required: bool
    hint: str | None
    options: tuple[str, ...]


@dataclass(frozen=True)
class RoomSetup:
    room: Room
    group_seq: int
    group_name: str
    manager: str
    manager_tel: str
    identity_codes: tuple[str, ...]
    guide: str
    hours: tuple[str, ...]  # slot start times, e.g. ("09:00", ..., "20:00")
    slot_minutes: int
    allowed_weekdays: tuple[str, ...]  # ("월", "화", ...)
    lead_hours: int  # must book at least this many hours before slot start
    period_start: date
    period_end: date
    items: tuple[FormItem, ...]
    raw_item_count: int  # site counts placeholder rows too (addItemCnt)


@dataclass(frozen=True)
class RoomRules:
    hours: tuple[str, ...]  # slot start times, e.g. ("09:00", ..., "20:00")
    slot_minutes: int
    allowed_weekdays: tuple[str, ...]  # ("월", "화", ...)
    lead_hours: int  # must book at least this many hours before slot start
    period_start: date
    period_end: date

    def slot_end(self, start: str) -> str:
        t = datetime.strptime(start, "%H:%M") + timedelta(minutes=self.slot_minutes)
        return t.strftime("%H:%M")


@dataclass(frozen=True)
class Profile:
    name: str
    student_id: str
    phone: str
    email: str


@dataclass(frozen=True)
class MyReservation:
    id: int
    place: str
    room: str
    day: date
    hours: tuple[str, ...]
    applicant: str
    applied_on: str
    status: str
    cancellable: bool


@dataclass
class DayAvailability:
    room: Room
    day: date
    open_hours: tuple[str, ...]  # bookable slot starts right now
    taken_hours: tuple[str, ...]
    closed_reason: str | None = None  # whole day unavailable (weekday / period)
    slot_minutes: int = 60
    not_yet_hours: tuple[str, ...] = field(default_factory=tuple)  # past or inside lead time


KEYRING_SERVICE = "hsu-reserve"
_KEYRING_ID_SLOT = "__student_id__"


def load_credentials() -> tuple[str, str]:
    """Env vars HSU_ID/HSU_PW win; otherwise the OS keychain entry written by `hsu-reserve-mcp setup`."""
    uid, pw = os.environ.get("HSU_ID"), os.environ.get("HSU_PW")
    if uid and pw:
        return uid, pw
    uid = keyring.get_password(KEYRING_SERVICE, _KEYRING_ID_SLOT)
    pw = keyring.get_password(KEYRING_SERVICE, uid) if uid else None
    if not uid or not pw:
        raise AuthError(
            "학교 계정이 설정되지 않았습니다. 설치 프로그램을 다시 실행하거나 `hsu-reserve-mcp setup` 을 실행하세요."
        )
    return uid, pw


def save_credentials(uid: str, pw: str) -> None:
    old = keyring.get_password(KEYRING_SERVICE, _KEYRING_ID_SLOT)
    # Keep the old account usable until both new entries have been saved.
    keyring.set_password(KEYRING_SERVICE, uid, pw)
    keyring.set_password(KEYRING_SERVICE, _KEYRING_ID_SLOT, uid)
    if old and old != uid:
        try:
            keyring.delete_password(KEYRING_SERVICE, old)
        except keyring.errors.KeyringError:
            pass  # the new account is already active; cleanup is best effort


def delete_credentials() -> None:
    old = keyring.get_password(KEYRING_SERVICE, _KEYRING_ID_SLOT)
    for slot in (old, _KEYRING_ID_SLOT):
        if slot:
            try:
                keyring.delete_password(KEYRING_SERVICE, slot)
            except keyring.errors.PasswordDeleteError:
                pass


def hours_between(start: str, end: str, slot_minutes: int = 60) -> list[str]:
    s = datetime.strptime(start, "%H:%M")
    e = datetime.strptime(end, "%H:%M")
    if e <= s:
        raise HsuError(f"종료 시간({end})이 시작 시간({start})보다 늦어야 합니다.")
    out = []
    while s < e:
        out.append(s.strftime("%H:%M"))
        s += timedelta(minutes=slot_minutes)
    if s != e:
        raise HsuError(f"{start}-{end} 는 {slot_minutes}분 단위로 나누어떨어지지 않습니다.")
    return out


def normalize_time(t: str) -> str:
    m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*(시)?\s*", t)
    if not m:
        raise HsuError(f"시간 형식 오류: {t!r} (예: 14:00)")
    return f"{int(m.group(1)):02d}:{m.group(2) or '00'}"


def _text(el) -> str:
    return el.get_text(" ", strip=True) if el is not None else ""


def _valid_static_data(endpoint: str, data: object) -> bool:
    """Treat damaged disk entries like cache misses, never like room rules."""
    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
        return False
    try:
        if endpoint == "resveSetup":
            if len(data) != 1:
                return False
            row = data[0]
            hours = [h.strip() for h in row["resveTm"].split(",") if h.strip()]
            if not hours or any(datetime.strptime(h, "%H:%M").strftime("%H:%M") != h for h in hours):
                return False
            if int(row.get("posblTerm") or 60) <= 0 or int(row.get("posblTm") or 0) < 0:
                return False
            days = [d.strip() for d in (row.get("posblDay") or "").split(",") if d.strip()]
            if any(len(d) != 1 or d not in WEEKDAYS_KO for d in days):
                return False
            start = datetime.strptime(row["posblBgnde"], "%Y%m%d").date()
            end = datetime.strptime(row["posblEndde"], "%Y%m%d").date()
            return start <= end
        if endpoint == "artclSetupList":
            if len(data) != 1 or int(data[0]["resveGroupSeq"]) <= 0:
                return False
            fields = ("groupNm", "mngr", "telno", "identityCode", "guide")
        elif endpoint == "itemList":
            fields = ("itemTy", "itemNm", "mustYn", "itemCn", "option1", "option2", "option3")
        else:
            return False
        return all(row.get(key) is None or isinstance(row[key], str) for row in data for key in fields)
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return False


class HsuClient:
    def __init__(self, user_id: str | None = None, password: str | None = None, timeout: float = 20.0):
        self._uid = user_id
        self._pw = password
        self._profiles: dict[str, Profile] = {}
        self._calendars: dict[tuple[str, int, int], tuple[float, list[Room], dict[tuple[str, date], set[str]]]] = {}
        self._last_request = 0.0
        self._disk_cache_path = _cache_dir() / "setup-v1.json"
        self._disk_cache: dict[str, dict] | None = None
        self.http = httpx.Client(
            base_url=BASE_URL,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            timeout=timeout,
            event_hooks={"request": [self._throttle]},
        )

    def _throttle(self, request: httpx.Request) -> None:
        wait = self._last_request + MIN_REQUEST_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> HsuClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------------- session ----------------

    def _url(self, place: Place, endpoint: str) -> str:
        return f"/resve/{place.site_id}/{place.fnct_no}/{endpoint}.do"

    def _login(self, place: Place) -> Profile:
        if self._uid is None or self._pw is None:
            self._uid, self._pw = load_credentials()
        site = place.site_id
        self.http.post(
            f"/hnuLogin/{site}/loginProcess.do",
            data={
                "siteId": site,
                "returnUrl": "",
                "referer": "",
                "userId": self._uid,
                "inputUserId": self._uid,
                "inputUserPwd": self._pw,
            },
        ).raise_for_status()
        profile = self._read_profile(place)
        if profile is None:
            raise AuthError("로그인 실패: 학번/비밀번호를 확인하세요.")
        self._profiles[site] = profile
        return profile

    def _read_profile(self, place: Place) -> Profile | None:
        resp = self.http.get(self._url(place, "artclRegistView"))
        resp.raise_for_status()
        _check_rejected(resp)
        soup = BeautifulSoup(resp.text, "html.parser")
        if soup.find("input", attrs={"name": "hakbun"}) is None:
            return None

        def val(name: str) -> str:
            el = soup.find("input", attrs={"name": name})
            return (el.get("value") or "") if el is not None else ""

        return Profile(name=val("userNm"), student_id=val("hakbun"), phone=val("telno"), email=val("email"))

    def profile(self, place: Place) -> Profile:
        return self._profiles.get(place.site_id) or self._login(place)

    def _post_json(self, place: Place, endpoint: str, data: dict) -> list[dict]:
        """Logged-in JSON call; re-logs in once if the session expired."""
        self.profile(place)
        url = self._url(place, endpoint)
        headers = {"X-Requested-With": "XMLHttpRequest"}
        resp = self.http.post(url, data=data, headers=headers)
        resp.raise_for_status()
        _check_rejected(resp)
        if not resp.headers.get("content-type", "").startswith("text/json"):
            self._login(place)  # session expired → HTML 알림 page instead of JSON
            resp = self.http.post(url, data=data, headers=headers)
            resp.raise_for_status()
            _check_rejected(resp)
        try:
            return resp.json()
        except ValueError:
            raise HsuError(f"{endpoint} 응답을 해석하지 못했습니다: {resp.text[:200]!r}") from None

    def _static_json(self, place: Place, endpoint: str, room: Room) -> list[dict]:
        """Public room setup, validated before use and disk-cached for SETUP_TTL."""
        if self._disk_cache is None:
            try:
                cache = json.loads(self._disk_cache_path.read_text(encoding="utf-8"))
                self._disk_cache = cache if isinstance(cache, dict) else {}
            except (OSError, ValueError):
                self._disk_cache = {}
        key = f"{place.site_id}/{endpoint}/{room.seq}"
        hit = self._disk_cache.get(key)
        if isinstance(hit, dict):
            at = hit.get("at")
            if (
                isinstance(at, (int, float))
                and 0 <= time.time() - at < SETUP_TTL
                and _valid_static_data(endpoint, hit.get("data"))
            ):
                return hit["data"]
        resp = self.http.post(
            self._url(place, endpoint),
            data={"resveSpceSeq": str(room.seq)},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        resp.raise_for_status()
        _check_rejected(resp)
        try:
            data = resp.json()
        except ValueError:
            raise HsuError(f"{endpoint} 응답을 해석하지 못했습니다: {resp.text[:200]!r}") from None
        if not _valid_static_data(endpoint, data):
            raise HsuError(f"{endpoint} 공간 설정 응답이 올바르지 않습니다.")
        self._disk_cache[key] = {"at": time.time(), "data": data}
        try:
            self._disk_cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._disk_cache_path.write_text(json.dumps(self._disk_cache, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass  # cache is an optimization only
        return data

    # ---------------- public month calendar ----------------

    def _calendar(self, place: Place, year: int, month: int) -> tuple[list[Room], dict[tuple[str, date], set[str]]]:
        """Every room's occupied/blocked slot starts for one month, from a single public page."""
        key = (place.site_id, year, month)
        hit = self._calendars.get(key)
        if hit and time.monotonic() - hit[0] < CALENDAR_TTL:
            return hit[1], hit[2]
        resp = self.http.post(
            self._url(place, "artclList"),
            data={
                "siteId": place.site_id,
                "layout": "",
                "year": str(year),
                "month": str(month),
                "kind": "",
                "resveSpceSeq": "",
            },
        )
        resp.raise_for_status()
        _check_rejected(resp)
        soup = BeautifulSoup(resp.text, "html.parser")
        title = soup.find(string=re.compile(r"\d{4}년\s*\d{1,2}월"))
        ym = re.search(r"(\d{4})년\s*(\d{1,2})월", title or "")
        if ym is None or (int(ym[1]), int(ym[2])) != (year, month):
            raise HsuError(f"{year}-{month:02d} 예약 현황 달력을 읽지 못했습니다.")
        error = f"{year}-{month:02d} 예약 현황 달력의 공간/시간 정보를 확인하지 못했습니다."
        try:
            rooms = [
                Room(seq=int(o["value"]), name=o.get_text(strip=True))
                for o in soup.select("#resveSpceSeq option")
                if o.get("value")
            ]
            names = {re.sub(r"\s+", "", room.name): room.name for room in rooms}
            if not rooms or len(names) != len(rooms) or "" in names:
                raise ValueError("missing or ambiguous rooms")
            busy: dict[tuple[str, date], set[str]] = {}
            days: set[int] = set()
            parsed_boxes = 0
            for td in soup.find_all("td"):
                span = td.find("span", recursive=False)
                if span is None or not span.get_text(strip=True).isdigit():
                    continue
                day = date(year, month, int(span.get_text(strip=True)))
                if day.day in days:
                    raise ValueError("duplicate calendar day")
                days.add(day.day)
                for box in td.find_all("div", class_="conBox"):
                    text = box.get_text(" ", strip=True)
                    matches = list(_TIME_RANGE.finditer(text))
                    if not matches or not re.fullmatch(
                        rf"{_TIME_RANGE.pattern}(?:\s*,\s*{_TIME_RANGE.pattern})*",
                        text[matches[0].start():],
                    ):
                        raise ValueError("unreadable occupied ranges")
                    m = matches[0]
                    name = text[: m.start()].strip().lstrip("-").strip()
                    room_name = names.get(re.sub(r"\s+", "", name))
                    if room_name is None:
                        raise ValueError("unknown occupied room")
                    slots = busy.setdefault((room_name, day), set())
                    # One application can contain several comma-separated, disjoint ranges.
                    for m in matches:
                        t = datetime(2000, 1, 1, int(m[1]), int(m[2]))
                        end = datetime(2000, 1, 1, int(m[3]), int(m[4]))
                        if t >= end:
                            raise ValueError("invalid occupied range")
                        # Minute precision also blocks partial overlaps with a booking slot.
                        while t < end:
                            slots.add(t.strftime("%H:%M"))
                            t += timedelta(minutes=1)
                    parsed_boxes += 1
            if days != set(range(1, monthrange(year, month)[1] + 1)):
                raise ValueError("incomplete calendar")
            if parsed_boxes != len(soup.select("div.conBox")):
                raise ValueError("unmatched occupied boxes")
        except (KeyError, TypeError, ValueError):
            raise HsuError(error) from None
        self._calendars[key] = (time.monotonic(), rooms, busy)
        return rooms, busy

    # ---------------- rooms & rules ----------------

    def rooms(self, place: Place) -> list[Room]:
        today = datetime.now(KST).date()
        return list(self._calendar(place, today.year, today.month)[0])

    def find_room(self, place: Place, room: str) -> Room:
        rooms = self.rooms(place)
        norm = re.sub(r"[\s호()]", "", room).lower()
        exact = [r for r in rooms if re.sub(r"[\s호()]", "", r.name).lower() == norm]
        if exact:
            return exact[0]
        partial = [r for r in rooms if norm and norm in re.sub(r"[\s호()]", "", r.name).lower()]
        if len(partial) == 1:
            return partial[0]
        names = ", ".join(r.name for r in rooms)
        if len(partial) > 1:
            raise HsuError(f"{room!r} 이(가) 여러 공간과 일치합니다: {', '.join(r.name for r in partial)}")
        raise HsuError(f"{place.name}에 {room!r} 공간이 없습니다. 가능한 공간: {names}")

    def room_rules(self, place: Place, room: Room) -> RoomRules:
        s = self._static_json(place, "resveSetup", room)[0]
        return RoomRules(
            hours=tuple(h.strip() for h in s["resveTm"].split(",") if h.strip()),
            slot_minutes=int(s.get("posblTerm") or 60),
            allowed_weekdays=tuple(d.strip() for d in (s.get("posblDay") or "").split(",") if d.strip()),
            lead_hours=int(s.get("posblTm") or 0),
            period_start=datetime.strptime(s["posblBgnde"], "%Y%m%d").date(),
            period_end=datetime.strptime(s["posblEndde"], "%Y%m%d").date(),
        )

    def room_setup(self, place: Place, room: Room) -> RoomSetup:
        info = self._static_json(place, "artclSetupList", room)[0]
        raw_items = self._static_json(place, "itemList", room)
        rules = self.room_rules(place, room)
        items = []
        for i, it in enumerate(raw_items, start=1):
            if not it.get("itemTy"):
                continue  # placeholder row the site returns when no extra items exist
            items.append(
                FormItem(
                    index=i,
                    name=str(it.get("itemNm") or "").strip(),
                    kind=it["itemTy"],
                    required=it.get("mustYn") == "Y",
                    hint=it.get("itemCn"),
                    options=tuple(o for o in (it.get("option1"), it.get("option2"), it.get("option3")) if o),
                )
            )
        return RoomSetup(
            room=room,
            group_seq=int(info["resveGroupSeq"]),
            group_name=info.get("groupNm") or "",
            manager=info.get("mngr") or "",
            manager_tel=info.get("telno") or "",
            identity_codes=tuple(c for c in (info.get("identityCode") or "").split(",") if c),
            guide=re.sub(
                r"\s+",
                " ",
                BeautifulSoup((info.get("guide") or "").replace("</p>", "</p>\n"), "html.parser").get_text(""),
            ).strip(),
            hours=rules.hours,
            slot_minutes=rules.slot_minutes,
            allowed_weekdays=rules.allowed_weekdays,
            lead_hours=rules.lead_hours,
            period_start=rules.period_start,
            period_end=rules.period_end,
            items=tuple(items),
            raw_item_count=len(raw_items),
        )

    # ---------------- availability ----------------

    def taken_hours(self, place: Place, room: Room, day: date) -> list[str]:
        """Authoritative per-room check (what the registration form itself uses)."""
        rows = self._post_json(place, "resveList", {"resveSpceSeq": str(room.seq), "resveDeStr": day.isoformat()})
        return sorted({r["resveTm"] for r in rows if r.get("resveTm")})

    def day_availability(
        self, place: Place, room: Room, day: date, now: datetime | None = None, authoritative: bool = False
    ) -> DayAvailability:
        """authoritative=False reads the shared month calendar (cheap); True asks resveList for this room."""
        rules = self.room_rules(place, room)
        now = now or datetime.now(KST)
        weekday = WEEKDAYS_KO[day.weekday()]
        closed = None
        if rules.allowed_weekdays and weekday not in rules.allowed_weekdays:
            closed = f"{weekday}요일은 예약 불가 (가능 요일: {','.join(rules.allowed_weekdays)})"
        elif not (rules.period_start <= day <= rules.period_end):
            closed = f"예약 가능 기간({rules.period_start}부터 {rules.period_end}까지) 밖의 날짜"
        elif day < now.date():
            closed = "지난 날짜"
        if closed:
            return DayAvailability(
                room=room, day=day, open_hours=(), taken_hours=(), closed_reason=closed, slot_minutes=rules.slot_minutes
            )

        if authoritative:
            taken = set(self.taken_hours(place, room, day))
        else:
            rooms, calendar = self._calendar(place, day.year, day.month)
            if room not in rooms:
                raise HsuError(f"{place.name} 달력에서 {room.name} 공간을 확인하지 못했습니다.")
            busy = calendar.get((room.name, day), set())
            taken = set()
            for h in rules.hours:
                slot_start = datetime.strptime(h, "%H:%M")
                if any(
                    (slot_start + timedelta(minutes=offset)).strftime("%H:%M") in busy
                    for offset in range(rules.slot_minutes)
                ):
                    taken.add(h)
        earliest = now + timedelta(hours=rules.lead_hours)
        open_, not_yet = [], []
        for h in rules.hours:
            if h in taken:
                continue
            slot_start = datetime.combine(day, datetime.strptime(h, "%H:%M").time(), tzinfo=KST)
            if slot_start < now or slot_start < earliest:
                not_yet.append(h)
            else:
                open_.append(h)
        return DayAvailability(
            room=room,
            day=day,
            open_hours=tuple(open_),
            taken_hours=tuple(sorted(taken)),
            slot_minutes=rules.slot_minutes,
            not_yet_hours=tuple(not_yet),
        )

    # ---------------- my reservations ----------------

    def my_reservations(self, place: Place) -> list[MyReservation]:
        """Read every page; application order does not bound the reservation date."""
        self.profile(place)
        out: list[MyReservation] = []
        seen: set[int] = set()
        relogged = False
        page = 1
        last_page = 1
        while page <= last_page:
            resp = self.http.post(
                self._url(place, "artclView"), data={"layout": "", "page": str(page), "prgrsCd": "", "resveSpceSeq": ""}
            )
            resp.raise_for_status()
            _check_rejected(resp)
            soup = BeautifulSoup(resp.text, "html.parser")
            if soup.find("form", attrs={"name": "actionForm"}) is None:
                if page == 1 and not relogged:  # session expired
                    self._login(place)
                    relogged = True
                    continue
                raise HsuError(f"{place.name} 내 신청내역 {page}페이지를 읽지 못했습니다.")
            # K2Web's last-page link includes the actual total, even across page groups.
            for link in soup.select("._paging a[href]"):
                match = re.search(r"page_link\s*\(\s*['\"]?(\d+)", link["href"])
                if match:
                    last_page = max(last_page, int(match[1]))
            rows = [tr for tr in soup.select("form[name=actionForm] table tr") if tr.find("td")]
            parsed = 0
            for tr in rows:
                tds = tr.find_all("td")
                if not _text(tds[0]).isdigit():
                    continue
                if len(tds) < 9:
                    raise HsuError(f"{place.name} 내 신청내역 {page}페이지의 예약 행을 해석하지 못했습니다.")
                reservation_id = int(_text(tds[0]))
                if reservation_id in seen:
                    raise HsuError(f"{place.name} 내 신청내역 페이지가 중복되어 전체 목록을 확인할 수 없습니다.")
                seen.add(reservation_id)
                parsed += 1
                out.append(
                    MyReservation(
                        id=int(_text(tds[0])),
                        place=place.name,
                        room=_text(tds[1]),
                        day=date.fromisoformat(_text(tds[2])),
                        hours=tuple(h.strip() for h in _text(tds[3]).split(",") if h.strip()),
                        applicant=_text(tds[4]),
                        applied_on=_text(tds[6]),
                        status=_text(tds[7]),
                        cancellable=tds[8].find("a", href=re.compile(r"artclCancle")) is not None,
                    )
                )
            if parsed == 0 and page < last_page:
                raise HsuError(f"{place.name} 내 신청내역 {page}페이지가 비어 있어 전체 목록을 확인할 수 없습니다.")
            page += 1
        return sorted(out, key=lambda row: row.id, reverse=True)

    # ---------------- reserve / cancel ----------------

    def build_form(
        self,
        place: Place,
        setup: RoomSetup,
        day: date,
        hours: list[str],
        item_values: dict[int, str],
        phone: str | None = None,
        email: str | None = None,
    ) -> list[tuple[str, str]]:
        prof = self.profile(place)
        identity = ",".join(IDENTITY_NAMES[c] for c in setup.identity_codes if c in IDENTITY_NAMES)
        form: list[tuple[str, str]] = [
            ("layout", ""),
            ("siteId", place.site_id),
            ("fnctNo", str(place.fnct_no)),
            ("lmttYn", "N"),
            ("groupNm", setup.group_name),
            ("spceNm", setup.room.name),
            ("selDay", WEEKDAYS_KO[day.weekday()]),
            ("posblDay", ",".join(setup.allowed_weekdays)),
            ("posblTerm", str(setup.slot_minutes)),
            ("posblBgnde", setup.period_start.strftime("%Y%m%d")),
            ("posblEndde", setup.period_end.strftime("%Y%m%d")),
            ("resveGroupSeq", str(setup.group_seq)),
            ("userNm", prof.name),
            ("hakbun", prof.student_id),
            ("addItemCnt", str(setup.raw_item_count)),
            ("group", str(setup.group_seq)),
            ("resveSpceSeq", str(setup.room.seq)),
            ("mngr", setup.manager),
            ("mngrTelno", setup.manager_tel),
            ("resveDeStr", day.isoformat()),
            ("identityCode", identity),
        ]
        form += [("resveTm", h) for h in hours]
        form += [("telno", phone or prof.phone), ("email", email if email is not None else prof.email)]
        for it in setup.items:
            if it.kind in ("TEXT", "MULTI") or it.required:
                form.append((f"addItemMustYn{it.index}", "Y" if it.required else "N"))
            if it.index in item_values:
                form.append((f"addItem{it.index}", item_values[it.index]))
        return form

    def submit(self, place: Place, form: list[tuple[str, str]]) -> tuple[bool, str]:
        """Replicates common.js jf_ajaxSubmit: AJAX validation POST (no insert), then the real form POST.

        Returns (ok, server message).
        """
        url = self._url(place, "artclRegist")
        # jQuery serializeArray → object: duplicate keys collapse to the last value (site behavior).
        resp = self.http.post(url, data=dict(form), headers={"X-Requested-With": "XMLHttpRequest"})
        resp.raise_for_status()
        _check_rejected(resp)
        if resp.text.strip():
            try:
                payload = resp.json()
            except ValueError:
                return False, f"사전 검증 응답을 해석하지 못했습니다: {resp.text.strip()[:200]}"
            errors = [
                str(v)
                for entry in (payload if isinstance(payload, list) else [payload])
                if isinstance(entry, dict)
                for v in entry.values()
            ]
            if errors:
                return False, " / ".join(errors)
        try:
            resp = self.http.post(
                url,
                content=urlencode(form).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        finally:
            # Even an ambiguous response may have changed the school state.
            self._calendars.clear()
        resp.raise_for_status()
        _check_rejected(resp)
        message = server_message(resp)
        return "완료" in message, message

    def cancel(self, place: Place, reservation_id: int) -> tuple[bool, str]:
        self.profile(place)
        try:
            resp = self.http.get(f"/resve/{place.site_id}/{place.fnct_no}/{reservation_id}/artclCancle.do")
        finally:
            self._calendars.clear()
        resp.raise_for_status()
        _check_rejected(resp)
        message = server_message(resp)
        return "취소했습니다" in message, message


def server_message(resp: httpx.Response) -> str:
    """K2Web reports results via a redirect to /message/message.do?message=..., or an inline 알림메세지 page."""
    _check_rejected(resp)
    if resp.url.path.endswith("/message/message.do") and resp.url.params.get("message"):
        return resp.url.params["message"]
    soup = BeautifulSoup(resp.text, "html.parser")
    form = soup.find("form", id="messageForm")
    if form is not None:
        return _text(form).replace("확인", "").strip()
    title = _text(soup.title)
    return f"(알 수 없는 응답: {title or resp.url})"
