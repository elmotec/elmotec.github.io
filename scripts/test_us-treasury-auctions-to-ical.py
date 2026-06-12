# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "click",
#     "icalendar",
#     "pytest",
#     "pytest-cov",
#     "requests",
# ]
# ///

import importlib.util
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest
import requests
from icalendar import Calendar


SCRIPT_PATH = Path(__file__).with_name("us-treasury-auctions-to-ical.py")


@pytest.fixture(scope="module")
def treasury_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("treasury_auctions", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def security() -> dict[str, str]:
    return {
        "announcementDate": "2026-06-11T00:00:00",
        "auctionDate": "2026-06-18T00:00:00",
        "closingTimeCompetitive": "11:30 AM",
        "closingTimeNoncompetitive": "11:00 AM",
        "cusip": "912797TEST",
        "issueDate": "2026-06-23T00:00:00",
        "maturityDate": "2026-07-21T00:00:00",
        "offeringAmount": "75000000000",
        "securityTerm": "4-Week",
        "securityType": "Bill",
    }


def test_create_auction_event_has_required_fields(
    treasury_module: ModuleType,
    security: dict[str, str],
) -> None:
    event = treasury_module.create_auction_event(security)

    assert event["uid"] == "912797TEST-auction@treasurydirect.gov"
    assert event.decoded("dtstart") == date(2026, 6, 18)
    assert event.decoded("dtstamp").utcoffset() == timedelta(0)
    assert event["summary"] == "4-Week Bill Auction"

    serialized = event.to_ical().decode()
    assert "DTSTAMP:" in serialized
    assert "DTSTAMP:" + event.decoded("dtstamp").strftime("%Y%m%dT%H%M%SZ") in serialized
    assert "Competitive Closing: 11:30 AM" in event["description"]
    assert "Non-Competitive Closing: 11:00 AM" in event["description"]
    assert "Issue Date: 2026-06-23T00:00:00" in event["description"]
    assert "Maturity Date: 2026-07-21T00:00:00" in event["description"]


def test_create_announcement_event_has_required_fields(
    treasury_module: ModuleType,
    security: dict[str, str],
) -> None:
    event = treasury_module.create_announcement_event(security)

    assert event["uid"] == "912797TEST-announcement@treasurydirect.gov"
    assert event.decoded("dtstart") == date(2026, 6, 11)
    assert event.decoded("dtstamp").utcoffset() == timedelta(0)
    assert event["summary"] == "4-Week Bill Auction Announced"
    assert "Auction Date: 2026-06-18T00:00:00" in event["description"]
    assert "Maturity Date: 2026-07-21" in event["description"]


@pytest.mark.parametrize(
    ("event_types", "expected_events"),
    [
        (["auction"], 1),
        (["announcement"], 1),
        (["announcement", "auction"], 2),
        ([], 0),
    ],
)
def test_generate_calendar_selects_requested_event_types(
    treasury_module: ModuleType,
    security: dict[str, str],
    event_types: list[str],
    expected_events: int,
) -> None:
    calendar = treasury_module.generate_calendar([security], event_types)

    assert calendar["version"] == "2.0"
    assert calendar["prodid"] == "-//Treasury Auction Calendar//elmotec.github.io//"
    assert len(calendar.walk("VEVENT")) == expected_events


def test_filter_securities_removes_old_auctions(treasury_module: ModuleType) -> None:
    today = datetime.now().date()
    securities = [
        {"auctionDate": f"{today - timedelta(days=8):%Y-%m-%d}T00:00:00"},
        {"auctionDate": f"{today + timedelta(days=1):%Y-%m-%d}T00:00:00"},
    ]

    assert treasury_module.filter_securities(securities, days_back=7) == [securities[1]]


def test_filter_securities_includes_entire_boundary_day(
    treasury_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AfternoonDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 6, 11, 15, 30, tzinfo=tz)

    monkeypatch.setattr(treasury_module, "datetime", AfternoonDateTime)
    boundary_security = {"auctionDate": "2026-06-04T00:00:00"}

    assert treasury_module.filter_securities(
        [boundary_security],
        days_back=7,
    ) == [boundary_security]


def test_fetch_treasury_data_returns_response_json(
    treasury_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    security: dict[str, str],
) -> None:
    response = Mock()
    response.json.return_value = [security]
    monkeypatch.setattr(treasury_module.requests, "get", Mock(return_value=response))

    assert treasury_module.fetch_treasury_data() == [security]
    treasury_module.requests.get.assert_called_once_with(
        treasury_module.API_URL,
        timeout=treasury_module.REQUEST_TIMEOUT,
    )
    response.raise_for_status.assert_called_once_with()


def test_fetch_treasury_data_exits_on_request_error(
    treasury_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        treasury_module.requests,
        "get",
        Mock(side_effect=requests.RequestException("network unavailable")),
    )

    with pytest.raises(SystemExit) as exc_info:
        treasury_module.fetch_treasury_data()

    assert exc_info.value.code == 1


def test_save_calendar_writes_valid_ical(
    treasury_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    security: dict[str, str],
) -> None:
    output_dir = tmp_path / "output"
    output_file = output_dir / "treasury-auctions.ics"
    monkeypatch.setattr(treasury_module, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(treasury_module, "OUTPUT_FILE", output_file)
    calendar = treasury_module.generate_calendar([security], ["auction"])

    treasury_module.save_calendar(calendar)

    parsed = Calendar.from_ical(output_file.read_bytes())
    events = parsed.walk("VEVENT")
    assert len(events) == 1
    assert events[0]["uid"] == "912797TEST-auction@treasurydirect.gov"


@pytest.mark.parametrize(
    ("diff", "expected"),
    [
        ("+BEGIN:VEVENT\n", True),
        ("+++ b/calendar.ics\n+BEGIN:VEVENT\n", True),
        ("--- a/calendar.ics\n+++ b/calendar.ics\n-old event\n", True),
        ("", False),
    ],
)
def test_has_changes_to_commit(
    treasury_module: ModuleType,
    diff: str,
    expected: bool,
) -> None:
    assert treasury_module.has_changes_to_commit(diff) is expected


def test_commit_and_push_exits_when_git_add_fails(
    treasury_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git_add_failure = subprocess.CompletedProcess(
        args=["git", "add"],
        returncode=1,
        stdout="",
        stderr="cannot update index",
    )
    run_git_command = Mock(return_value=git_add_failure)
    monkeypatch.setattr(treasury_module, "run_git_command", run_git_command)

    with pytest.raises(SystemExit) as exc_info:
        treasury_module.commit_and_push()

    assert exc_info.value.code == 1
    run_git_command.assert_called_once_with([
        "git",
        "add",
        str(treasury_module.OUTPUT_FILE),
    ])


if __name__ == "__main__":
    raise SystemExit(
        pytest.main([
            str(Path(__file__)),
            f"--cov={SCRIPT_PATH.parent}",
            "--cov-branch",
            "--cov-report=term-missing",
        ])
    )
