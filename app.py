import os
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import streamlit as st
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv(Path(__file__).with_name(".env"))
PARIS = ZoneInfo("Europe/Paris")

st.set_page_config(page_title="Nice Departures", layout="wide")

st.html("""
<style>
.stApp {
    background: #08121c;
    color: #f3f5f7;
}

.board-heading {
    font-family: Consolas, "Lucida Console", monospace;
    color: #f8c94e;
    font-size: 36px;
    font-weight: 700;
    letter-spacing: 3px;
    margin-bottom: 4px;
}

.board-subheading {
    color: #b4c3cf;
    font-family: Consolas, "Lucida Console", monospace;
    margin-bottom: 20px;
}

.airport-board {
    overflow-x: auto;
    border: 2px solid #34485a;
    border-radius: 10px;
    background: #101d29;
    box-shadow: 0 12px 30px rgba(0, 0, 0, 0.35);
}

.airport-board table {
    width: 100%;
    min-width: 760px;
    border-collapse: collapse;
    font-family: Consolas, "Lucida Console", monospace;
    font-variant-numeric: tabular-nums;
    text-transform: uppercase;
}

.airport-board th {
    padding: 16px 14px;
    background: #1b2c3b;
    color: #f8c94e;
    font-size: 15px;
    letter-spacing: 2px;
    text-align: left;
}

.airport-board td {
    padding: 18px 14px;
    border-top: 1px solid #304252;
    color: #f4f4ec;
    font-size: 20px;
    font-weight: 700;
    letter-spacing: 1px;
    white-space: nowrap;
}

.airport-board tr:nth-child(even) {
    background: #152432;
}

.airport-board .time {
    color: #f8c94e;
    font-size: 23px;
}

.airport-board .status-ok {
    color: #62d39b;
}

.airport-board .status-delay {
    color: #ffb65c;
}

.airport-board .status-cancel {
    color: #ff7474;
}
</style>
""")

st.html("""
<div class="board-heading">NCE ✈ DEPARTURES</div>
<div class="board-subheading">
    NICE CÔTE D'AZUR · FLIGHT INFORMATION
</div>
""")

neon_url = os.getenv("NEON_DATABASE_URL")
if not neon_url:
    try:
        neon_url = st.secrets.get("NEON_DATABASE_URL")
    except FileNotFoundError:
        pass

if not neon_url:
    st.error("Neon database connection is not configured.")
    st.stop()

try:
    with psycopg.connect(
        neon_url,
        row_factory=dict_row,
        connect_timeout=10,
    ) as conn:
        last_collection = conn.execute("""
            SELECT max(fetched_at) AS last_collection
            FROM public.raw_flight_observations
        """).fetchone()["last_collection"]

        # First try the current, recently collected departures.
        flights = conn.execute("""
            SELECT
                flight_iata,
                arrival_airport,
                scheduled_departure,
                estimated_departure,
                flight_status,
                departure_delay_minutes,
                fetched_at
            FROM analytics.current_departures
            ORDER BY scheduled_departure, flight_iata
        """).fetchall()

        showing_snapshot = not flights

        # If there are no current flights, show only the latest collection.
        if showing_snapshot:
            flights = conn.execute("""
                SELECT
                    flight_iata,
                    arrival_airport,
                    scheduled_departure::timestamp AS scheduled_departure,
                    estimated_departure::timestamp AS estimated_departure,
                    flight_status,
                    departure_delay_minutes,
                    fetched_at
                FROM analytics.stg_flight_observations
                WHERE run_id = (
                    SELECT run_id
                    FROM public.raw_flight_observations
                    ORDER BY fetched_at DESC
                    LIMIT 1
                )
                ORDER BY scheduled_departure::timestamp, flight_iata
            """).fetchall()

except psycopg.Error:
    st.error(
        "Could not read Neon PostgreSQL. Check the database connection settings."
    )
    st.stop()

if last_collection:
    france_time = last_collection.astimezone(PARIS)
    st.caption(
        f"Last collected: {france_time:%d %b %Y, %H:%M} France time"
    )

    age_hours = (
        datetime.now(timezone.utc) - last_collection
    ).total_seconds() / 3600

    if age_hours > 4:
        st.warning(
            "Flight data is over 4 hours old. "
            "Check an official flight source before travelling."
        )
else:
    st.warning("No flight collections found yet.")

if showing_snapshot and flights:
    st.warning(
        "Showing the last collected batch, not current departures. "
        "Some flights may have already departed or changed status."
    )

search = st.text_input(
    "Find your flight",
    placeholder="Enter a flight number, for example LH2273",
)

if search.strip():
    term = search.strip().upper()
    flights = [
        flight
        for flight in flights
        if term in (flight["flight_iata"] or "").upper()
    ]

st.caption(
    f"{len(flights)} flights shown · "
    "AirLabs may not provide every Nice departure"
)

def display_time(value):
    return value.strftime("%H:%M") if value else "--:--"

rows = []

for flight in flights:
    status = (flight["flight_status"] or "Unknown").upper()

    if "CANCEL" in status:
        status_class = "status-cancel"
    elif "DELAY" in status:
        status_class = "status-delay"
    else:
        status_class = "status-ok"

    delay = flight["departure_delay_minutes"]
    delay_text = f"{delay:g} MIN" if delay is not None else "—"

    rows.append(f"""
        <tr>
            <td>{escape(flight["flight_iata"] or "—")}</td>
            <td>{escape(flight["arrival_airport"] or "—")}</td>
            <td class="time">{display_time(flight["scheduled_departure"])}</td>
            <td class="time">{display_time(flight["estimated_departure"])}</td>
            <td class="{status_class}">{escape(status)}</td>
            <td>{escape(delay_text)}</td>
        </tr>
    """)

if rows:
    st.html(f"""
        <div class="airport-board">
            <table>
                <thead>
                    <tr>
                        <th>Flight</th>
                        <th>To</th>
                        <th>Scheduled</th>
                        <th>Estimated</th>
                        <th>Status</th>
                        <th>Delay</th>
                    </tr>
                </thead>
                <tbody>{''.join(rows)}</tbody>
            </table>
        </div>
    """)
else:
    st.info("No matching flights in the available data.")
