# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "icalendar",
#     "requests",
# ]
# ///

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from icalendar import Calendar, Event


API_URL = "https://www.treasurydirect.gov/TA_WS/securities/announced?format=json"
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_FILE = OUTPUT_DIR / "treasury-auctions.ics"
REQUEST_TIMEOUT = 30


def fetch_treasury_data() -> list[dict[str, Any]]:
    """Fetch announced Treasury securities from TreasuryDirect API."""
    try:
        response = requests.get(API_URL, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"Error fetching Treasury data: {e}", file=sys.stderr)
        sys.exit(1)


def parse_date(date_str: str) -> datetime:
    """Parse ISO date string to datetime object."""
    return datetime.fromisoformat(date_str.replace("T00:00:00", ""))


def create_announcement_event(security: dict[str, Any]) -> Event:
    """Create calendar event for auction announcement."""
    event = Event()
    
    announcement_date = parse_date(security["announcementDate"])
    event.add("uid", f"{security['cusip']}-announcement@treasurydirect.gov")
    event.add("dtstamp", datetime.now())
    event.add("dtstart", announcement_date.date())
    
    summary = f"{security['securityTerm']} {security['securityType']} Auction Announced"
    event.add("summary", summary)
    
    description = (
        f"Auction Date: {security['auctionDate']}\n"
        f"CUSIP: {security['cusip']}\n"
        f"Offering Amount: ${security.get('offeringAmount', 'TBD')}"
    )
    event.add("description", description)
    
    event.add("categories", ["Treasury", "Announcement", security["securityType"]])
    
    return event


def create_auction_event(security: dict[str, Any]) -> Event:
    """Create calendar event for the auction itself."""
    event = Event()
    
    auction_date = parse_date(security["auctionDate"])
    event.add("uid", f"{security['cusip']}-auction@treasurydirect.gov")
    event.add("dtstamp", datetime.now())
    event.add("dtstart", auction_date.date())
    
    summary = f"{security['securityTerm']} {security['securityType']} Auction"
    event.add("summary", summary)
    
    description_parts = [
        f"CUSIP: {security['cusip']}",
        f"Security Term: {security['securityTerm']}",
        f"Offering Amount: ${security.get('offeringAmount', 'TBD')}",
    ]
    
    if closing_time := security.get("closingTimeCompetitive"):
        description_parts.append(f"Competitive Closing: {closing_time}")
    if closing_time := security.get("closingTimeNoncompetitive"):
        description_parts.append(f"Non-Competitive Closing: {closing_time}")
    if issue_date := security.get("issueDate"):
        description_parts.append(f"Issue Date: {issue_date}")
    if maturity_date := security.get("maturityDate"):
        description_parts.append(f"Maturity Date: {maturity_date}")
    
    event.add("description", "\n".join(description_parts))
    event.add("categories", ["Treasury", "Auction", security["securityType"]])
    
    return event


def generate_calendar(securities: list[dict[str, Any]]) -> Calendar:
    """Generate iCalendar object from Treasury securities data."""
    calendar = Calendar()
    calendar.add("prodid", "-//Treasury Auction Calendar//elmotec.github.io//")
    calendar.add("version", "2.0")
    
    for security in securities:
        calendar.add_component(create_announcement_event(security))
        calendar.add_component(create_auction_event(security))
    
    return calendar


def save_calendar(calendar: Calendar) -> None:
    """Save calendar to .ics file."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_bytes(calendar.to_ical())
    print(f"Calendar saved to {OUTPUT_FILE}")


def run_git_command(command: list[str]) -> subprocess.CompletedProcess:
    """Execute git command and return result."""
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )


def commit_and_push() -> None:
    """Commit calendar file to git and push to remote."""
    run_git_command(["git", "add", str(OUTPUT_FILE)])
    
    result = run_git_command(["git", "diff", "--cached", "--quiet", str(OUTPUT_FILE)])
    
    if result.returncode == 0:
        print("No changes to commit")
        return
    
    result = run_git_command([
        "git", "commit", "-m", "chore: update Treasury auction calendar"
    ])
    if result.returncode != 0:
        print(f"Error committing changes: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    
    print("Changes committed")
    
    result = run_git_command(["git", "push", "origin", "main"])
    if result.returncode != 0:
        print(f"Error pushing changes: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    
    print("Changes pushed to remote")


def main() -> None:
    """Main execution flow."""
    print("Fetching Treasury auction data...")
    securities = fetch_treasury_data()
    print(f"Found {len(securities)} announced securities")
    
    print("Generating calendar...")
    calendar = generate_calendar(securities)
    
    save_calendar(calendar)
    
    print("Committing and pushing changes...")
    commit_and_push()
    
    print("Done!")


if __name__ == "__main__":
    main()
