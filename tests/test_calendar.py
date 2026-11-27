"""Offline calendar behavior regressions using the site's reservation-box markup."""

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

import httpx

from hsu_reserve.client import KST, PLACES, HsuClient, HsuError, Room


class CalendarTests(unittest.TestCase):
    day = date(2040, 1, 2)
    now = datetime(2040, 1, 1, tzinfo=KST)
    room = Room(1, "IB101")
    place = PLACES["sangsang_base"]

    def setUp(self):
        cache = tempfile.TemporaryDirectory(prefix="hsu-calendar-test-")
        self.addCleanup(cache.cleanup)
        self.calendar_ranges = ""
        self.client = HsuClient()
        self.client.http.close()
        self.client.http = httpx.Client(
            base_url="https://offline.invalid",
            transport=httpx.MockTransport(self.respond),
        )
        self.client._disk_cache_path = Path(cache.name) / "setup.json"
        self.addCleanup(self.client.close)

    def calendar_html(self):
        # The school renders one room followed by comma-separated ranges in a box.
        box = (
            '<div class="conBox" alt="#fixture">'
            '<!-- synthetic reservation metadata -->'
            f'- IB101<br>{self.calendar_ranges}<br></div>'
        )
        cells = "".join(
            f'<td><span>{number}</span><br>{box if number == self.day.day else ""}</td>'
            for number in range(1, 32)
        )
        return (
            '<strong>2040년 1월</strong>'
            '<select id="resveSpceSeq">'
            '<option value="">전체</option><option value="1">IB101</option>'
            '</select><div class="table_1 table_m calendar_table">'
            f'<table><tbody><tr>{cells}</tr></tbody></table></div>'
        )

    def respond(self, request):
        endpoint = request.url.path.rsplit("/", 1)[-1]
        if endpoint == "artclList.do":
            return httpx.Response(200, text=self.calendar_html())
        if endpoint == "resveSetup.do":
            return httpx.Response(
                200,
                json=[{
                    "resveTm": "09:00,10:00,11:00,12:00,13:00",
                    "posblTerm": "60",
                    "posblDay": "월,화,수,목,금,토,일",
                    "posblTm": "0",
                    "posblBgnde": "20390101",
                    "posblEndde": "20410101",
                }],
            )
        raise AssertionError(f"Unexpected offline request: {request.method} {request.url.path}")

    def availability(self):
        return self.client.day_availability(self.place, self.room, self.day, now=self.now)

    def test_disjoint_ranges_are_taken_but_gap_remains_free(self):
        self.calendar_ranges = "09:00~10:00,12:00~13:00"

        result = self.availability()

        self.assertEqual(result.taken_hours, ("09:00", "12:00"))
        self.assertEqual(result.open_hours, ("10:00", "11:00", "13:00"))

    def test_malformed_secondary_range_fails_closed(self):
        for ranges in (
            "09:00~10:00,broken",
            "09:00~10:00,12:00~11:00",
            "09:00~10:00,25:00~26:00",
            "09:00~10:00,12:00~13:00 unreadable",
        ):
            with self.subTest(ranges=ranges):
                self.calendar_ranges = ranges
                with self.assertRaises(HsuError):
                    self.availability()

    def test_partial_overlap_blocks_each_intersecting_slot(self):
        self.calendar_ranges = "09:30~10:30"

        result = self.availability()

        self.assertEqual(result.taken_hours, ("09:00", "10:00"))
        self.assertEqual(result.open_hours, ("11:00", "12:00", "13:00"))


if __name__ == "__main__":
    unittest.main()
