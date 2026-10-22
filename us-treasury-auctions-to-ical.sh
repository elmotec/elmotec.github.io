#!/usr/bin/bash

set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

exec uv run scripts/us-treasury-auctions-to-ical.py --source upcoming "$@"
exec uv run scripts/us-treasury-auctions-to-ical.py --source auctioned "$@"
