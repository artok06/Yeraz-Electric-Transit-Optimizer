# -*- coding: utf-8 -*-
"""
Created on Mon Jul 14 00:11:48 2025

Optimized script for calculating route distances and maximum trip durations
for each direction of bus routes based on GTFS data.

This script:
- Loads filtered GTFS trip and stop times data for a specified city
- Builds exact service calendar from calendar.txt and calendar_dates.txt
- Calculates the maximum full trip distance per route direction
- Finds max trip duration among all full trips per direction
- Aggregates max daily full trip counts in September per direction
- Updates the initial routes DataFrame in bulk
- Removes incomplete routes missing full trips in any direction or with zero trips

@author: artok
"""

import pandas as pd
from datetime import datetime, timedelta

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
    """
    Combine calendar.txt and calendar_dates.txt to get a dict:
    { service_id: set of valid service dates (YYYYMMDD strings) }
    """
    service_dates = {}

    # Step 1: Add regular dates from calendar.txt according to day flags
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

    # Step 2: Apply exceptions from calendar_dates.txt
    for _, row in calendar_dates_df.iterrows():
        service_id = row['service_id']
        date = str(row['date'])
        exception_type = row['exception_type']

        if service_id not in service_dates:
            service_dates[service_id] = set()

        if exception_type == 1:
            # Add service on this date
            service_dates[service_id].add(date)
        elif exception_type == 2:
            # Remove service on this date
            service_dates[service_id].discard(date)

    return service_dates

# --- Load datasets ---
initial_routes = pd.read_csv("data/raw/initial_routes.csv")
trips_df = pd.read_csv('data/filtered_routes/filtered_trips.csv')
stops_df = pd.read_csv('data/filtered_routes/filtered_stop_times.csv')
calendar_df = pd.read_csv('data/input_gtfs/calendar.txt')
calendar_dates_df = pd.read_csv('data/input_gtfs/calendar_dates.txt')

# Build service dates dictionary combining calendar.txt and calendar_dates.txt
service_dates = build_valid_service_dates(calendar_df, calendar_dates_df)

# Define September 2025 date range
sep_start = datetime.strptime("20250901", "%Y%m%d")
sep_end = datetime.strptime("20250930", "%Y%m%d")
sep_dates = [d.strftime("%Y%m%d") for d in daterange(sep_start, sep_end)]

last_stop_dists = stops_df.groupby('trip_id')['shape_dist_traveled'].last()
stops_grouped = stops_df.groupby('trip_id')

def get_max_duration(trip_ids):
    durations = []
    for trip_id in trip_ids:
        trip_stops = stops_grouped.get_group(trip_id)
        dep = parse_time_to_minutes(trip_stops.iloc[0]['departure_time'])
        arr = parse_time_to_minutes(trip_stops.iloc[-1]['arrival_time'])
        if dep is not None and arr is not None:
            if arr < dep:
                arr += 24 * 60  # handle trips past midnight
            durations.append(arr - dep)
    return round(max(durations), 1) if durations else None

    
 


results = {
    'route_id': [],
    'distance_dir_0_km': [],
    'distance_dir_1_km': [],
    'number_of_daily_roundtrips_0': [],
    'number_of_daily_roundtrips_1': [],
    'trip_time_0_min': [],
    'trip_time_1_min': [],
}

for route_id in trips_df['route_id'].unique():
    trips_for_route = trips_df[trips_df['route_id'] == route_id]

    trips_0 = trips_for_route[trips_for_route['direction_id'] == 0]
    trips_1 = trips_for_route[trips_for_route['direction_id'] == 1]

    max_distance_0 = last_stop_dists[trips_0['trip_id']].max() if not trips_0.empty else 0
    max_distance_1 = last_stop_dists[trips_1['trip_id']].max() if not trips_1.empty else 0

    threshold_0 = 0.9 * max_distance_0
    threshold_1 = 0.9 * max_distance_1

    full_0_ids = set(last_stop_dists[trips_0['trip_id']][last_stop_dists[trips_0['trip_id']] >= threshold_0].index)
    full_1_ids = set(last_stop_dists[trips_1['trip_id']][last_stop_dists[trips_1['trip_id']] >= threshold_1].index)

    trip_to_service_0 = trips_0.set_index('trip_id')['service_id'].to_dict()
    trip_to_service_1 = trips_1.set_index('trip_id')['service_id'].to_dict()

    max_trips_0 = 0
    max_trips_1 = 0

    for date_str in sep_dates:
        active_trips_0 = [
            trip_id for trip_id in full_0_ids
            if date_str in service_dates.get(trip_to_service_0.get(trip_id, ''), set())
        ]
        active_trips_1 = [
            trip_id for trip_id in full_1_ids
            if date_str in service_dates.get(trip_to_service_1.get(trip_id, ''), set())
        ]

        max_trips_0 = max(max_trips_0, len(active_trips_0))
        max_trips_1 = max(max_trips_1, len(active_trips_1))

    trip_time_0_min = get_max_duration(full_0_ids)
    trip_time_1_min = get_max_duration(full_1_ids)

    distance_km_0 = round(max_distance_0 / 1000, 2)
    distance_km_1 = round(max_distance_1 / 1000, 2)

    if distance_km_1 == 0:
        trip_time_1_min = 0

    results['route_id'].append(route_id)
    results['distance_dir_0_km'].append(distance_km_0)
    results['distance_dir_1_km'].append(distance_km_1)
    results['number_of_daily_roundtrips_0'].append(max_trips_0)
    results['number_of_daily_roundtrips_1'].append(max_trips_1)
    results['trip_time_0_min'].append(trip_time_0_min)
    results['trip_time_1_min'].append(trip_time_1_min)

    print(f'{route_id}: 0 - {distance_km_0} km, 1 - {distance_km_1} km, '
          f'max daily trips_0: {max_trips_0}, max daily trips_1: {max_trips_1}, '
          f'trip_time_0: {trip_time_0_min}, trip_time_1: {trip_time_1_min}')

results_df = pd.DataFrame(results).set_index('route_id')
initial_routes = initial_routes.set_index('route_id')
initial_routes.update(results_df)
initial_routes = initial_routes.reset_index()

before_rows = len(initial_routes)
# Exclude routes missing full trips or with zero trips in either direction
initial_routes = initial_routes[
    (initial_routes['distance_dir_0_km'] > 0) &
    (initial_routes['distance_dir_1_km'] > 0) &
    (initial_routes['number_of_daily_roundtrips_0'] > 0) &
    (initial_routes['number_of_daily_roundtrips_1'] > 0)
]
after_rows = len(initial_routes)
print(f"Removed {before_rows - after_rows} incomplete or zero-trip routes. Remaining: {after_rows}")

initial_routes.to_csv("data/raw/initial_routes.csv", index=False)
