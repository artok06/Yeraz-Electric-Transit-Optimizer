# -*- coding: utf-8 -*-
"""
Created on Sun Jul 27 13:05:11 2025

This script performs the following steps for electric bus deployment modeling:

1. Calls distance_trip_time_calculation.py to update initial_routes.csv with
   route distances and durations based on GTFS data.
2. Loads updated route and trip data from disk.
3. Computes the minimum number of required buses per route using either:
    - A simple time-based formula (buses = total driving minutes / available time)
    - A complex trip overlap analysis with a sweep-line algorithm
4. Saves the computed results to a new column base_buses in initial_routes.csv.

@author: artok
"""

import pandas as pd
from datetime import datetime, timedelta
import subprocess
import sys
import os

# --- Paths and imports ---
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
script_path = "src/distance_trips_time_calculation.py"

# --- Call dependent script to update initial_routes.csv ---
print("Running distance and trip time calculation script...")
result = subprocess.run(
    ["python", script_path],
    cwd=project_root,
    capture_output=True,
    text=True
)
print("distance_trips_time_calculation.py finished.")

# --- Load datasets ---
initial_routes = pd.read_csv("data/raw/initial_routes.csv")
trips_df = pd.read_csv('data/filtered_routes/filtered_trips.csv')
stops_df = pd.read_csv('data/filtered_routes/filtered_stop_times.csv')
calendar_df = pd.read_csv('data/input_gtfs/calendar.txt')
calendar_dates_df = pd.read_csv('data/input_gtfs/calendar_dates.txt')

# --- Helper functions ---
def parse_time_to_minutes(time_str):
    try:
        h, m, s = map(int, time_str.split(':'))
        return h * 60 + m + s / 60
    except Exception:
        return None

def daterange(start_date, end_date):
    for n in range(int((end_date - start_date).days) + 1):
        yield start_date + timedelta(n)

def build_valid_service_dates(calendar_df, calendar_dates_df):
    service_dates = {}
    for _, row in calendar_df.iterrows():
        service_id = row['service_id']
        start = datetime.strptime(str(row['start_date']), "%Y%m%d")
        end = datetime.strptime(str(row['end_date']), "%Y%m%d")

        valid_dates = set()
        for single_date in daterange(start, end):
            weekday_name = single_date.strftime('%A').lower()
            if row[weekday_name] == 1:
                valid_dates.add(single_date.strftime("%Y%m%d"))

        service_dates[service_id] = valid_dates

    for _, row in calendar_dates_df.iterrows():
        service_id = row['service_id']
        date = str(row['date'])
        exception_type = row['exception_type']

        if service_id not in service_dates:
            service_dates[service_id] = set()

        if exception_type == 1:
            service_dates[service_id].add(date)
        elif exception_type == 2:
            service_dates[service_id].discard(date)

    return service_dates

def get_max_duration(trip_ids, stops_grouped):
    durations = []
    for trip_id in trip_ids:
        try:
            trip_stops = stops_grouped.get_group(trip_id)
        except KeyError:
            continue
        dep = parse_time_to_minutes(trip_stops.iloc[0]['departure_time'])
        arr = parse_time_to_minutes(trip_stops.iloc[-1]['arrival_time'])
        if dep is not None and arr is not None:
            if arr < dep:
                arr += 24 * 60  # handle trips past midnight
            durations.append(arr - dep)
    return round(max(durations), 1) if durations else 0

# --- Build service dates dict ---
print("Building service dates dictionary...")
service_dates = build_valid_service_dates(calendar_df, calendar_dates_df)
print(f"Loaded service dates for {len(service_dates)} service_ids.")

# Define September 2025 date range
sep_start = datetime.strptime("20250901", "%Y%m%d")
sep_end = datetime.strptime("20250930", "%Y%m%d")
sep_dates = [d.strftime("%Y%m%d") for d in daterange(sep_start, sep_end)]

last_stop_dists = stops_df.groupby('trip_id')['shape_dist_traveled'].last()
stops_grouped = stops_df.groupby('trip_id')

# --- Precompute full trip sets and max daily trips for each route and direction ---
print("Computing max daily trips and durations per route and direction...")

route_data = {}

for route_id in trips_df['route_id'].unique():
    trips_for_route = trips_df[trips_df['route_id'] == route_id]

    # Split by direction
    trips_0 = trips_for_route[trips_for_route['direction_id'] == 0]
    trips_1 = trips_for_route[trips_for_route['direction_id'] == 1]

    max_dist_0 = last_stop_dists[trips_0['trip_id']].max() if not trips_0.empty else 0
    max_dist_1 = last_stop_dists[trips_1['trip_id']].max() if not trips_1.empty else 0

    threshold_0 = 0.9 * max_dist_0
    threshold_1 = 0.9 * max_dist_1

    full_0_ids = set(last_stop_dists[trips_0['trip_id']][last_stop_dists[trips_0['trip_id']] >= threshold_0].index)
    full_1_ids = set(last_stop_dists[trips_1['trip_id']][last_stop_dists[trips_1['trip_id']] >= threshold_1].index)

    trip_to_service_0 = trips_0.set_index('trip_id')['service_id'].to_dict()
    trip_to_service_1 = trips_1.set_index('trip_id')['service_id'].to_dict()

    max_daily_trips_0 = 0
    max_daily_trips_1 = 0

    busiest_day_trips_0 = []
    busiest_day_trips_1 = []

    # Find busiest day trips count
    for date_str in sep_dates:
        active_trips_0 = [
            trip_id for trip_id in full_0_ids
            if date_str in service_dates.get(trip_to_service_0.get(trip_id, ''), set())
        ]
        active_trips_1 = [
            trip_id for trip_id in full_1_ids
            if date_str in service_dates.get(trip_to_service_1.get(trip_id, ''), set())
        ]

        if len(active_trips_0) > max_daily_trips_0:
            max_daily_trips_0 = len(active_trips_0)
            busiest_day_trips_0 = active_trips_0

        if len(active_trips_1) > max_daily_trips_1:
            max_daily_trips_1 = len(active_trips_1)
            busiest_day_trips_1 = active_trips_1

    trip_time_0_min = get_max_duration(full_0_ids, stops_grouped)
    trip_time_1_min = get_max_duration(full_1_ids, stops_grouped)

    distance_km_0 = round(max_dist_0 / 1000, 2)
    distance_km_1 = round(max_dist_1 / 1000, 2)

    if distance_km_1 == 0:
        trip_time_1_min = 0

    route_data[route_id] = {
        'distance_dir_0_km': distance_km_0,
        'distance_dir_1_km': distance_km_1,
        'number_of_daily_roundtrips_0': max_daily_trips_0,
        'number_of_daily_roundtrips_1': max_daily_trips_1,
        'trip_time_0_min': trip_time_0_min,
        'trip_time_1_min': trip_time_1_min,
        'busiest_day_trips_0': busiest_day_trips_0,
        'busiest_day_trips_1': busiest_day_trips_1,
    }

    print(f"Route {route_id}: Distances {distance_km_0}km / {distance_km_1}km, "
          f"Max daily trips {max_daily_trips_0} / {max_daily_trips_1}, "
          f"Trip times {trip_time_0_min}min / {trip_time_1_min}min")

# Exclude list fields when updating the DataFrame to avoid dtype warnings
route_data_for_update = {
    k: {
        key: val
        for key, val in v.items()
        if key not in ['busiest_day_trips_0', 'busiest_day_trips_1']
    }
    for k, v in route_data.items()
}

results_df = pd.DataFrame(route_data_for_update).T.reset_index().rename(columns={'index': 'route_id'})

initial_routes = initial_routes.set_index('route_id')
initial_routes.update(results_df.set_index('route_id'))
initial_routes = initial_routes.reset_index()

# Remove incomplete routes (zero distances or zero trips)
before_count = len(initial_routes)
initial_routes = initial_routes[
    (initial_routes['distance_dir_0_km'] > 0) &
    (initial_routes['distance_dir_1_km'] > 0) &
    (initial_routes['number_of_daily_roundtrips_0'] > 0) &
    (initial_routes['number_of_daily_roundtrips_1'] > 0)
]
after_count = len(initial_routes)
print(f"Filtered incomplete routes: removed {before_count - after_count}, remaining {after_count}")

# --- Bus calculation functions ---
def calculate_simple_buses(trip_time_0, trips_0, trip_time_1, trips_1, operational_minutes_per_day=960):
    buses_0 = (trip_time_0 * trips_0) / operational_minutes_per_day if trip_time_0 and trips_0 else 0
    buses_1 = (trip_time_1 * trips_1) / operational_minutes_per_day if trip_time_1 and trips_1 else 0
    return int(buses_0 + buses_1 + 0.9999)

def calculate_complex_buses(route_id, stops_df, busiest_day_trips_0, busiest_day_trips_1):
    print(f"Calculating buses for route {route_id}...")
    intervals = []

    for direction, trip_ids in [(0, busiest_day_trips_0), (1, busiest_day_trips_1)]:
        for trip_id in trip_ids:
            trip_stops = stops_df[stops_df['trip_id'] == trip_id].sort_values('stop_sequence')
            if trip_stops.empty:
                continue

            dep_time = parse_time_to_minutes(trip_stops.iloc[0]['departure_time'])
            arr_time = parse_time_to_minutes(trip_stops.iloc[-1]['arrival_time'])
            if dep_time is not None and arr_time is not None:
                if arr_time < dep_time:
                    arr_time += 24 * 60  # overnight
                intervals.append((dep_time, arr_time))

    if not intervals:
        print(f"  No intervals found for route {route_id}, buses = 0")
        return 0

    events = []
    for start, end in intervals:
        events.append((start, 'start'))
        events.append((end, 'end'))

    events.sort()
    current_buses = max_buses = 0
    for _, typ in events:
        current_buses += 1 if typ == 'start' else -1
        max_buses = max(max_buses, current_buses)

    return max_buses

# --- Main bus calculation ---
def main(calculation_type="simple"):
    if calculation_type not in ["simple", "complex"]:
        raise ValueError("Invalid calculation_type: choose 'simple' or 'complex'")

    print(f"Starting bus calculation using '{calculation_type}' method...\n")

    for idx, row in initial_routes.iterrows():
        route_id = row['route_id']

        trip_time_0 = row.get('trip_time_0_min', 0)
        trip_time_1 = row.get('trip_time_1_min', 0)
        trips_0 = row.get('number_of_daily_roundtrips_0', 0)
        trips_1 = row.get('number_of_daily_roundtrips_1', 0)

        if calculation_type == "simple":
            buses_needed = calculate_simple_buses(trip_time_0, trips_0, trip_time_1, trips_1)
            print(f"Route {route_id}: SIMPLE buses needed = {buses_needed}")

        else:
            # Use precomputed busiest day trips for complex calc
            route_busiest_0 = route_data.get(route_id, {}).get('busiest_day_trips_0', [])
            route_busiest_1 = route_data.get(route_id, {}).get('busiest_day_trips_1', [])
            buses_needed = calculate_complex_buses(route_id, stops_df, route_busiest_0, route_busiest_1)
            print(f"Route {route_id}: Buses needed = {buses_needed}")

        initial_routes.at[idx, 'base_buses'] = buses_needed

    initial_routes.to_csv("data/raw/initial_routes.csv", index=False)
    print("\n✅ Saved updated 'base_buses' column to initial_routes.csv")

if __name__ == "__main__":
    # Change to "complex" to use sweep-line method instead of simple calculation
    main(calculation_type="complex")
