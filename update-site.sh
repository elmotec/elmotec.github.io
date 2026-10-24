#!/usr/bin/bash

set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

uv run scripts/us-treasury-auctions-to-ical.py --source upcoming "$@"
uv run scripts/us-treasury-auctions-to-ical.py --source auctioned "$@"
