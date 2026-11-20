"""HTTP client for the Hansung University K2Web space reservation module (`/resve/...`).

Flow mirrors the site's own JS (artclRegistView.js / common.js):
  login        POST /hnuLogin/{site}/loginProcess.do
  room setup   POST /resve/{site}/{fnct}/artclSetupList.do, resveSetup.do, itemList.do
  taken hours  POST /resve/{site}/{fnct}/resveList.do  (resveSpceSeq, resveDeStr)
  submit       POST /resve/{site}/{fnct}/artclRegist.do (AJAX validation, then real form POST)
  my list      POST /resve/{site}/{fnct}/artclView.do   (page=N, newest first)
  cancel       GET  /resve/{site}/{fnct}/{id}/artclCancle.do
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
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
        note="상상관 B2. 그룹스터디룸 IB101~IB108(최소 3인, 합실 5인), 세미나실 IB111(최소 6인, 관리자 승인).",
    ),
    "coding_lounge": Place(
        key="coding_lounge",
        name="코딩라운지",
        site_id="cncschool",
        fnct_no=7,
        max_hours_per_day=6,
        note="공학관 A동 1층. 세미나실 101~105호(최대 12명), 106~113호(최대 8명). 예약 후 안내데스크 방문·신분증 지참.",
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
    delete_credentials()
    keyring.set_password(KEYRING_SERVICE, _KEYRING_ID_SLOT, uid)
    keyring.set_password(KEYRING_SERVICE, uid, pw)


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
        raise HsuError(f"{start}~{end} 는 {slot_minutes}분 단위로 나누어떨어지지 않습니다.")
    return out


def normalize_time(t: str) -> str:
    m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*(시)?\s*", t)
    if not m:
        raise HsuError(f"시간 형식 오류: {t!r} (예: 14:00)")
    return f"{int(m.group(1)):02d}:{m.group(2) or '00'}"


def _text(el) -> str:
    return el.get_text(" ", strip=True) if el is not None else ""


class HsuClient:
    def __init__(self, user_id: str | None = None, password: str | None = None, timeout: float = 20.0):
        self._uid = user_id
        self._pw = password
        self._profiles: dict[str, Profile] = {}
        self._setup_cache: dict[tuple[str, int], RoomSetup] = {}
        self._rooms_cache: dict[str, list[Room]] = {}
        self.http = httpx.Client(
            base_url=BASE_URL,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            timeout=timeout,
        )

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
        soup = BeautifulSoup(resp.text, "html.parser")
        hakbun = soup.find("input", attrs={"name": "hakbun"})
        if hakbun is None:
            return None

        def val(name: str) -> str:
            el = soup.find("input", attrs={"name": name})
            return (el.get("value") or "") if el is not None else ""

        rooms = []
        group = soup.find("select", attrs={"name": "group"})
        if group is not None:
            for opt in group.find_all("option"):
                if opt.get("value"):
                    rooms.extend(self._group_rooms(place, int(opt["value"])))
        self._rooms_cache[place.site_id] = rooms
        return Profile(name=val("userNm"), student_id=val("hakbun"), phone=val("telno"), email=val("email"))

    def _group_rooms(self, place: Place, group_seq: int) -> list[Room]:
        resp = self.http.post(
            f"/resve/{place.site_id}/{place.fnct_no}/{group_seq}/getChildrenClList.do",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        resp.raise_for_status()
        return [Room(seq=int(r["resveSpceSeq"]), name=str(r["specNm"]).strip()) for r in resp.json()]

    def profile(self, place: Place) -> Profile:
        return self._profiles.get(place.site_id) or self._login(place)

    def _post_json(self, place: Place, endpoint: str, data: dict) -> list[dict]:
        self.profile(place)
        url = self._url(place, endpoint)
        headers = {"X-Requested-With": "XMLHttpRequest"}
        resp = self.http.post(url, data=data, headers=headers)
        resp.raise_for_status()
        if not resp.headers.get("content-type", "").startswith("text/json"):
            self._login(place)  # session expired → HTML 알림 page instead of JSON
            resp = self.http.post(url, data=data, headers=headers)
            resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            raise HsuError(f"{endpoint} 응답을 해석하지 못했습니다: {resp.text[:200]!r}") from None

    # ---------------- rooms & rules ----------------

    def rooms(self, place: Place) -> list[Room]:
        self.profile(place)
        return list(self._rooms_cache.get(place.site_id, []))

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

    def room_setup(self, place: Place, room: Room) -> RoomSetup:
        key = (place.site_id, room.seq)
        if key in self._setup_cache:
            return self._setup_cache[key]
        data = {"resveSpceSeq": str(room.seq)}
        info = self._post_json(place, "artclSetupList", data)[0]
        setup = self._post_json(place, "resveSetup", data)[0]
        raw_items = self._post_json(place, "itemList", data)
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
        rs = RoomSetup(
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
            hours=tuple(h.strip() for h in setup["resveTm"].split(",") if h.strip()),
            slot_minutes=int(setup.get("posblTerm") or 60),
            allowed_weekdays=tuple(d.strip() for d in (setup.get("posblDay") or "").split(",") if d.strip()),
            lead_hours=int(setup.get("posblTm") or 0),
            period_start=datetime.strptime(setup["posblBgnde"], "%Y%m%d").date(),
            period_end=datetime.strptime(setup["posblEndde"], "%Y%m%d").date(),
            items=tuple(items),
            raw_item_count=len(raw_items),
        )
        self._setup_cache[key] = rs
        return rs

    # ---------------- availability ----------------

    def taken_hours(self, place: Place, room: Room, day: date) -> list[str]:
        rows = self._post_json(place, "resveList", {"resveSpceSeq": str(room.seq), "resveDeStr": day.isoformat()})
        return sorted({r["resveTm"] for r in rows if r.get("resveTm")})

    def day_availability(self, place: Place, room: Room, day: date, now: datetime | None = None) -> DayAvailability:
        setup = self.room_setup(place, room)
        now = now or datetime.now(KST)
        weekday = WEEKDAYS_KO[day.weekday()]
        closed = None
        if setup.allowed_weekdays and weekday not in setup.allowed_weekdays:
            closed = f"{weekday}요일은 예약 불가 (가능 요일: {','.join(setup.allowed_weekdays)})"
        elif not (setup.period_start <= day <= setup.period_end):
            closed = f"예약 가능 기간({setup.period_start}~{setup.period_end}) 밖의 날짜"
        elif day < now.date():
            closed = "지난 날짜"
        if closed:
            return DayAvailability(
                room=room, day=day, open_hours=(), taken_hours=(), closed_reason=closed, slot_minutes=setup.slot_minutes
            )

        taken = set(self.taken_hours(place, room, day))
        earliest = now + timedelta(hours=setup.lead_hours)
        open_, not_yet = [], []
        for h in setup.hours:
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
            slot_minutes=setup.slot_minutes,
            not_yet_hours=tuple(not_yet),
        )

    # ---------------- my reservations ----------------

    def my_reservations(self, place: Place, pages: int = 2) -> list[MyReservation]:
        self.profile(place)
        out: list[MyReservation] = []
        for page in range(1, pages + 1):
            resp = self.http.post(
                self._url(place, "artclView"), data={"layout": "", "page": str(page), "prgrsCd": "", "resveSpceSeq": ""}
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            if soup.find("form", attrs={"name": "actionForm"}) is None:
                if page == 1:
                    self._login(place)
                    return self.my_reservations(place, pages)
                break
            rows = [tr for tr in soup.select("form[name=actionForm] table tr") if tr.find("td")]
            parsed = 0
            for tr in rows:
                tds = tr.find_all("td")
                if len(tds) < 9 or not _text(tds[0]).isdigit():
                    continue
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
            if parsed == 0:
                break
        return out

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
        resp = self.http.post(
            url,
            content=urlencode(form).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        message = server_message(resp)
        return "완료" in message, message

    def cancel(self, place: Place, reservation_id: int) -> tuple[bool, str]:
        self.profile(place)
        resp = self.http.get(f"/resve/{place.site_id}/{place.fnct_no}/{reservation_id}/artclCancle.do")
        resp.raise_for_status()
        message = server_message(resp)
        return "취소했습니다" in message, message


def server_message(resp: httpx.Response) -> str:
    """K2Web reports results via a redirect to /message/message.do?message=..., or an inline 알림메세지 page."""
    if resp.url.path.endswith("/message/message.do") and resp.url.params.get("message"):
        return resp.url.params["message"]
    soup = BeautifulSoup(resp.text, "html.parser")
    form = soup.find("form", id="messageForm")
    if form is not None:
        return _text(form).replace("확인", "").strip()
    title = _text(soup.title)
    return f"(알 수 없는 응답: {title or resp.url})"
