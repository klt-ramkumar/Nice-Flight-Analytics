import os
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
import requests
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

load_dotenv()

API_URL = "https://airlabs.co/api/v9/schedules"
AIRPORT = "NCE"
PAGE_SIZE = 100
MAX_PAGES = int(os.getenv("FLIGHT_MAX_PAGES", "3"))

if not 1 <= MAX_PAGES <= 30:
    raise RuntimeError("FLIGHT_MAX_PAGES must be between 1 and 30.")

# This counts requests made by THIS script, not older requests
# or requests made elsewhere using your AirLabs key.
MONTHLY_SCRIPT_LIMIT = 900

api_key = os.getenv("AIRLABS_API_KEY")
neon_url = os.getenv("NEON_DATABASE_URL")

if not api_key:
    raise RuntimeError("Set AIRLABS_API_KEY.")
if not neon_url:
    raise RuntimeError("Set NEON_DATABASE_URL for the Neon database.")

run_id = uuid4()
total_saved = 0
requests_this_run = 0
previous_page_said_more = False

try:
    conn = psycopg.connect(neon_url, autocommit=True)
except psycopg.Error:
    raise RuntimeError("Could not connect to Neon PostgreSQL. Check NEON_DATABASE_URL.") from None

with conn:
    # Create the tables on the first run. Later runs reuse them.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_requests (
            id BIGSERIAL PRIMARY KEY,
            attempted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            run_id UUID NOT NULL,
            page_number INTEGER NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_flight_observations (
            id BIGSERIAL PRIMARY KEY,
            run_id UUID NOT NULL,
            fetched_at TIMESTAMPTZ NOT NULL,
            source_page INTEGER NOT NULL,
            row_on_page INTEGER NOT NULL,
            flight_data JSONB NOT NULL,
            UNIQUE (run_id, source_page, row_on_page)
        )
    """)

    for page_number in range(1, MAX_PAGES + 1):
        # Check this script's recorded requests for the current UTC month.
        month_start = datetime.now(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )

        used_this_month = conn.execute(
            """
            SELECT count(*)
            FROM api_requests
            WHERE attempted_at >= %s
            """,
            (month_start,),
        ).fetchone()[0]

        if used_this_month >= MONTHLY_SCRIPT_LIMIT:
            print("Stopped: this script reached its monthly request limit.")
            break

        # Record the attempt BEFORE calling the API, including requests
        # that might return an error or an empty page.
        conn.execute(
            """
            INSERT INTO api_requests (run_id, page_number)
            VALUES (%s, %s)
            """,
            (run_id, page_number),
        )
        requests_this_run += 1

        offset = page_number - 1

        try:
            http_response = requests.get(
                API_URL,
                params={
                    "api_key": api_key,
                    "dep_iata": AIRPORT,
                    "limit": PAGE_SIZE,
                    "offset": offset,
                },
                timeout=30,
            )
            http_response.raise_for_status()
            data = http_response.json()
        except (requests.RequestException, ValueError):
            # Avoid printing the request URL because it contains the API key.
            print(f"Page {page_number}: request failed. Stopping this run.")
            break

        if not isinstance(data, dict) or data.get("error"):
            print(f"Page {page_number}: API returned an error. Stopping this run.")
            break

        flights = data.get("response")
        request_info = data.get("request", {})
        has_more = request_info.get("has_more") if isinstance(request_info, dict) else None

        if not isinstance(flights, list):
            print(f"Page {page_number}: unexpected response format. Stopping.")
            break

        print(f"Page {page_number}: {len(flights)} records | more pages: {has_more}")

        if not flights:
            if previous_page_said_more:
                print("Warning: previous page said more existed, but this page is empty.")
                print("We cannot confirm that this snapshot contains every flight.")
            break

        fetched_at = datetime.now(timezone.utc)

        # Save this entire page together.
        with conn.transaction():
            for row_number, flight in enumerate(flights, start=1):
                if not isinstance(flight, dict):
                    raise RuntimeError("Unexpected flight record. Page was not saved.")

                conn.execute(
                    """
                    INSERT INTO raw_flight_observations
                        (run_id, fetched_at, source_page, row_on_page, flight_data)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        run_id,
                        fetched_at,
                        page_number,
                        row_number,
                        Jsonb(flight),
                    ),
                )

        total_saved += len(flights)

        if has_more is not True:
            if has_more is None:
                print("Stopped: API did not provide has_more; completeness is uncertain.")
            else:
                print("Stopped: API says there are no more pages.")
            break

        previous_page_said_more = True
    else:
        print(f"Stopped after {MAX_PAGES} request(s).")
        if previous_page_said_more:
            print("Warning: AirLabs reported more flights; this snapshot may be incomplete.")

print(f"\nRecords saved to PostgreSQL: {total_saved}")
print(f"API requests attempted in this run: {requests_this_run}")




