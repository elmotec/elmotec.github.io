# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "click",
#     "icalendar",
#     "requests",
# ]
# ///

import json
import logging
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import click
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
        logging.error(f"Error fetching Treasury data: {e}")
        sys.exit(1)


def parse_date(date_str: str) -> datetime:
    """Parse ISO date string to datetime object."""
    return datetime.fromisoformat(date_str.replace("T00:00:00", ""))


def filter_securities(
    securities: list[dict[str, Any]], days_back: int
) -> list[dict[str, Any]]:
    """Filter out securities with auction dates earlier than days_back from now."""
    cutoff_date = datetime.now() - timedelta(days=days_back)
    filtered = []
    for security in securities:
        auction_date = parse_date(security["auctionDate"])
        if auction_date >= cutoff_date:
            filtered.append(security)
    return filtered


def create_announcement_event(security: dict[str, Any]) -> Event:
    """Create calendar event for auction announcement."""
    event = Event()
    
    announcement_date = parse_date(security["announcementDate"])
    event.add("uid", f"{security['cusip']}-announcement@treasurydirect.gov")
    event.add("dtstamp", datetime.utcnow())
    event.add("dtstart", announcement_date.date())
    
    summary = f"{security['securityTerm']} {security['securityType']} Auction Announced"
    event.add("summary", summary)
    
    description_parts = [
        f"Auction Date: {security['auctionDate']}",
        f"CUSIP: {security['cusip']}",
        f"Offering Amount: ${security.get('offeringAmount', 'TBD')}",
    ]
    if maturity_date := security.get("maturityDate"):
        description_parts.append(f"Maturity Date: {maturity_date[:10]}")
    
    event.add("description", "\n".join(description_parts))
    
    event.add("categories", ["Treasury", "Announcement", security["securityType"]])
    
    return event


def create_auction_event(security: dict[str, Any]) -> Event:
    """Create calendar event for the auction itself."""
    event = Event()
    
    auction_date = parse_date(security["auctionDate"])
    announcement_date = parse_date(security["announcementDate"])
    event.add("uid", f"{security['cusip']}-auction@treasurydirect.gov")
    event.add("dtstamp", datetime.utcnow())
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


def generate_calendar(securities: list[dict[str, Any]], event_types: list[str]) -> Calendar:
    """Generate iCalendar object from Treasury securities data."""
    calendar = Calendar()
    calendar.add("prodid", "-//Treasury Auction Calendar//elmotec.github.io//")
    calendar.add("version", "2.0")
    
    for security in securities:
        if "announcement" in event_types:
            calendar.add_component(create_announcement_event(security))
        if "auction" in event_types:
            calendar.add_component(create_auction_event(security))
    
    return calendar


def save_calendar(calendar: Calendar) -> None:
    """Save calendar to .ics file."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_bytes(calendar.to_ical())
    logging.info(f"Calendar saved to {OUTPUT_FILE}")


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
        logging.info("No changes to commit")
        return
    
    result = run_git_command([
        "git", "commit", "-m", "chore: update Treasury auction calendar"
    ])
    if result.returncode != 0:
        logging.error(f"Error committing changes: {result.stderr}")
        sys.exit(1)
    
    logging.info("Changes committed")
    
    result = run_git_command(["git", "push", "origin", "main"])
    if result.returncode != 0:
        logging.error(f"Error pushing changes: {result.stderr}")
        sys.exit(1)
    
    logging.info("Changes pushed to remote")


def main(commit: bool, days_back: int, event_types: list[str]) -> None:
    """Main execution flow."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    
    logging.info("Fetching Treasury auction data...")
    securities = fetch_treasury_data()
    logging.info(f"Found {len(securities)} announced securities")
    
    logging.info(f"Filtering securities with auction dates in last {days_back} days...")
    securities = filter_securities(securities, days_back)
    logging.info(f"After filtering: {len(securities)} securities")
    
    logging.info("Generating calendar...")
    calendar = generate_calendar(securities, event_types)
    
    save_calendar(calendar)
    
    if commit:
        logging.info("Committing and pushing changes...")
        commit_and_push()
    else:
        logging.info("Skipping commit (use --commit to enable)")
    
    logging.info("Done!")


@click.command()
@click.option(
    "-c",
    "--commit",
    is_flag=True,
    default=False,
    help="Commit and push changes to git",
)
@click.option(
    "--days-back",
    type=int,
    default=7,
    help="Include auctions from the last N days (default: 7)",
)
@click.option(
    "--event-type",
    "event_types",
    type=click.Choice(["announcement", "auction"], case_sensitive=False),    
    default=["auction"],
    multiple=True,
    help="Type of events to include in calendar (default: auction)",
)
def cli(commit: bool, days_back: int, event_types: list[str]) -> None:
    """Download Treasury auction data and generate iCalendar file."""    
    main(commit, days_back, event_types)


if __name__ == "__main__":
    cli()
