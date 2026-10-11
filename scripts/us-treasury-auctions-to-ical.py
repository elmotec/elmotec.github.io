# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "click",
#     "icalendar",
#     "requests",
# ]
# ///

import logging
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

import click
import requests
from icalendar import Calendar, Event


API_URL = "https://www.treasurydirect.gov/TA_WS/securities/announced?format=json"
UPCOMING_API_URL = (
    "https://www.treasurydirect.gov/TA_WS/securities/upcoming?format=json"
)
AUCTIONED_API_URL = "https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json"
SOURCE_URLS = {
    "announced": API_URL,
    "upcoming": UPCOMING_API_URL,
    "auctioned": AUCTIONED_API_URL,
}
OUTPUT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_FILE = OUTPUT_DIR / "treasury-auctions.ics"
RESULTS_FILE = OUTPUT_DIR / "treasury-auction-results.ics"
EASTERN = ZoneInfo("America/New_York")
REQUEST_TIMEOUT = 30


def fetch_treasury_data(source: str = "announced") -> list[dict[str, Any]]:
    """Fetch Treasury securities from the selected TreasuryDirect API."""
    try:
        response = requests.get(SOURCE_URLS[source], timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logging.error(f"Error fetching Treasury data: {e}")
        sys.exit(1)


def parse_date(date_str: str) -> datetime:
    """Parse ISO date string to datetime object."""
    return datetime.fromisoformat(date_str.replace("T00:00:00", ""))


def format_offering_amount(security: dict[str, Any]) -> str:
    """Format an offering amount, including tentative auctions with no amount yet."""
    amount = security.get("offeringAmount")
    return f"Offering Amount: ${amount}" if amount else "Offering Amount: TBD"


def filter_securities(
    securities: list[dict[str, Any]], days_back: int
) -> list[dict[str, Any]]:
    """Filter out securities with auction dates earlier than days_back from now."""
    cutoff_date = (datetime.now() - timedelta(days=days_back)).date()
    logging.debug("Auction date cutoff: %s", cutoff_date)
    filtered = []
    for index, security in enumerate(securities, start=1):
        logging.debug("Source entry %d: %s", index, security)
        auction_date = parse_date(security["auctionDate"]).date()
        if auction_date >= cutoff_date:
            logging.debug("Including entry %d: auction date %s", index, auction_date)
            filtered.append(security)
        else:
            logging.debug("Skipping entry %d: auction date %s", index, auction_date)
    return filtered


def create_announcement_event(security: dict[str, Any]) -> Event:
    """Create calendar event for auction announcement."""
    event = Event()

    announcement_date = parse_date(security["announcementDate"])
    event.add("uid", f"{security['cusip']}-announcement@treasurydirect.gov")
    # Do not emit DTSTAMP. It is regenerated on every run, causing the .ics
    # file to change even when Treasury auction data has not changed.
    # event.add("dtstamp", datetime.now(UTC))
    event.add("dtstart", announcement_date.date())

    summary = f"{security['securityTerm']} {security['securityType']} Auction Announced"
    event.add("summary", summary)

    description_parts = [
        f"Auction Date: {security['auctionDate']}",
        f"CUSIP: {security['cusip']}",
        format_offering_amount(security),
    ]
    if maturity_date := security.get("maturityDate"):
        description_parts.append(f"Maturity Date: {maturity_date[:10]}")

    event.add("description", "\n".join(description_parts))

    event.add("categories", ["Treasury", "Announcement", security["securityType"]])

    logging.debug(
        "Created announcement event for %s on %s: %s",
        security["cusip"],
        announcement_date.date(),
        summary,
    )
    return event


def create_auction_event(security: dict[str, Any]) -> Event:
    """Create calendar event for the auction itself."""
    event = Event()

    auction_date = parse_date(security["auctionDate"])
    announcement_date = parse_date(security["announcementDate"])
    event.add("uid", f"{security['cusip']}-auction@treasurydirect.gov")
    # Do not emit DTSTAMP. It is regenerated on every run, causing the .ics
    # file to change even when Treasury auction data has not changed.
    # event.add("dtstamp", datetime.now(UTC))
    event.add("dtstart", auction_date.date())

    summary = f"{security['securityTerm']} {security['securityType']} Auction"
    event.add("summary", summary)

    description_parts = [
        f"CUSIP: {security['cusip']}",
        f"Security Term: {security['securityTerm']}",
        format_offering_amount(security),
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

    logging.debug(
        "Created auction event for %s on %s: %s",
        security["cusip"],
        auction_date.date(),
        summary,
    )
    return event


def create_result_event(security: dict[str, Any]) -> Event:
    """Create an all-day completed auction event on its auction date."""
    auction_date = parse_date(security["auctionDate"]).date()
    security_type = security.get("type") or security["securityType"]
    rate_field, rate_label = {
        "Bill": ("highDiscountRate", "High Discount Rate"),
        "FRN": ("highDiscountMargin", "High Discount Margin"),
    }.get(security_type, ("highYield", "High Yield"))
    rate = security[rate_field]
    if not rate:
        raise ValueError(f"Missing {rate_label} for {security['cusip']}")
    event = Event()
    event.add("uid", f"{security['cusip']}-{auction_date}-result@treasurydirect.gov")
    event.add("dtstart", auction_date)
    event.add("dtstamp", datetime.combine(auction_date, datetime.min.time(), UTC))
    if updated := security.get("updatedTimestamp"):
        modified = datetime.fromisoformat(updated).replace(tzinfo=EASTERN).astimezone(UTC)
        event.add("last-modified", modified)
    event.add("summary", f"{security['securityTerm']} {security_type} Result: {rate_label} {rate}%")
    results_url = (
        "https://www.treasurydirect.gov/instit/annceresult/press/preanre/"
        f"{auction_date.year}/{security['pdfFilenameCompetitiveResults']}"
    )
    description = [
        f"CUSIP: {security['cusip']}",
        f"Security Term: {security['securityTerm']}",
        f"{rate_label}: {rate}%",
    ]
    if closing_time := security.get("closingTimeCompetitive"):
        description.append(f"Competitive Closing: {closing_time} Eastern")
    for field, label, prefix, suffix in [
        ("highInvestmentRate", "Investment Rate", "", "%"),
        ("spread", "Spread", "", "%"),
        ("pricePer100", "Price per $100", "$", ""),
        ("unadjustedPrice", "Unadjusted Price per $100", "$", ""),
        ("bidToCoverRatio", "Bid-to-Cover Ratio", "", ""),
        ("totalAccepted", "Amount Accepted", "$", ""),
        ("issueDate", "Issue Date", "", ""),
        ("maturityDate", "Maturity Date", "", ""),
    ]:
        if value := security.get(field):
            description.append(f"{label}: {prefix}{value}{suffix}")
    description.append(f"Official Results: {results_url}")
    event.add("description", "\n".join(description))
    event.add("url", results_url)
    event.add("categories", ["Treasury", "Auction Result", security_type])
    return event


def generate_calendar(securities: list[dict[str, Any]], event_types: list[str]) -> Calendar:
    """Generate iCalendar object from Treasury securities data."""
    calendar = Calendar()
    calendar.add("prodid", "-//Treasury Auction Calendar//elmotec.github.io//")
    calendar.add("version", "2.0")

    for security in securities:
        if "result" in event_types:
            calendar.add_component(create_result_event(security))
        if "announcement" in event_types:
            calendar.add_component(create_announcement_event(security))
        if "auction" in event_types:
            calendar.add_component(create_auction_event(security))

    if "result" in event_types:
        calendar.add("x-wr-calname", "U.S. Treasury Auction Results")
    return calendar


def save_calendar(calendar: Calendar, output_file: Path | None = None) -> None:
    """Save calendar to .ics file."""
    output_file = output_file or OUTPUT_FILE
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_bytes(calendar.to_ical())
    logging.info(f"Calendar saved to {output_file}")


def run_git_command(command: list[str]) -> subprocess.CompletedProcess:
    """Execute git command and return result."""
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )


def has_changes_to_commit(output: str) -> bool:
    """Check if there are changes to commit."""
    for line in output.splitlines():
        if (
            line.startswith(("+", "-"))
            and not line.startswith(("+++", "---"))
        ):
            return True
    return False


def commit_and_push(output_file: Path | None = None) -> None:
    """Commit calendar file to git and push to remote."""
    output_file = output_file or OUTPUT_FILE
    result = run_git_command(["git", "add", str(output_file)])
    if result.returncode != 0:
        logging.error(f"Error staging changes: {result.stderr}")
        sys.exit(1)

    result = run_git_command(["git", "diff", "--cached", str(output_file)])

    if not has_changes_to_commit(result.stdout):
        logging.info("No changes to commit")
        return

    result = run_git_command([
        "git", "commit", "-m", f"chore: update {output_file.stem}"
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


def main(
    commit: bool, days_back: int, event_types: list[str], verbose: bool, source: str
) -> None:
    """Main execution flow."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    logging.debug("Event types: %s", ", ".join(event_types))
    logging.info("Fetching Treasury auction data from %s...", source)
    securities = fetch_treasury_data(source)
    logging.info("Found %d securities", len(securities))

    logging.info(f"Filtering securities with auction dates in last {days_back} days...")
    if source != "auctioned":
        securities = filter_securities(securities, days_back)
    output_file = OUTPUT_FILE
    if source == "auctioned":
        today = datetime.now(EASTERN).date()
        cutoff = today - timedelta(days=days_back)
        if securities and cutoff < min(parse_date(row["auctionDate"]).date() for row in securities):
            logging.warning("Requested history exceeds TreasuryDirect recent-results history")
        # Only completed competitive results belong in this feed.
        securities = [
            security for security in securities
            if cutoff <= parse_date(security["auctionDate"]).date() <= today
            and security.get("pdfFilenameCompetitiveResults")
        ]
        event_types = ["result"]
        output_file = RESULTS_FILE
    logging.info("After filtering: %d securities", len(securities))
    logging.info("Generating calendar...")
    try:
        calendar = generate_calendar(securities, event_types)
    except (KeyError, ValueError) as error:
        raise click.ClickException(f"Invalid Treasury auction data: {error}") from error

    save_calendar(calendar, output_file)

    if commit:
        logging.info("Committing and pushing changes...")
        commit_and_push(output_file)
    else:
        logging.info("Skipping commit (use --commit to enable)")

    logging.info("Done!")


@click.command()
@click.option(
    "--source",
    type=click.Choice(["announced", "upcoming", "auctioned"], case_sensitive=False),
    default="upcoming",
    show_default=True,
    help="TreasuryDirect source: auctioned writes treasury-auction-results.ics",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Show debug-level logs",
)
@click.option(
    "-c",
    "--commit",
    is_flag=True,
    default=False,
    help="Commit and push changes to git",
)
@click.option(
    "--days-back",
    type=click.IntRange(min=0),
    default=7,
    help="Include auctions from the last N days (default: 7)",
)
@click.option(
    "--event-type",
    "event_types",
    type=click.Choice(["announcement", "auction"], case_sensitive=False),
    default=["auction"],
    multiple=True,
    help="Upcoming/announced event types (auctioned always produces results)",
)
def cli(
    commit: bool, days_back: int, event_types: list[str], verbose: bool, source: str
) -> None:
    """Download Treasury auction data and generate iCalendar file."""
    if source == "auctioned" and event_types != ("auction",):
        raise click.UsageError("--source auctioned generates results; omit --event-type")
    main(commit, days_back, event_types, verbose, source)


if __name__ == "__main__":
    cli()
