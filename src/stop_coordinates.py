# -*- coding: utf-8 -*-
"""
Created on Tue Jul 15 23:42:18 2025

Extracts and saves the ordered stop coordinates for the longest full trip
in each direction of every route, storing them as JSON strings in the 
initial_routes DataFrame for further processing.

This script:
- Loads filtered GTFS trips and stop times data
- Loads stops location lookup
- Identifies the longest full trip per route and direction by shape distance
- Extracts ordered stop coordinates (latitude and longitude) for that trip
- Saves coordinates as JSON strings into 'stop_coordinates_array_0' and 'stop_coordinates_array_1' columns
- Saves the updated initial_routes CSV for downstream use

@author: artok
"""

import pandas as pd
import os
import json

# --- 1. Load filtered GTFS data and stops lookup ---

trips_df = pd.read_csv('data/filtered_routes/filtered_trips.csv')
stops_df = pd.read_csv('data/filtered_routes/filtered_stop_times.csv')

# Stops lookup: index by stop_id, get lat/lon
stops_lookup = (
    pd.read_csv('data/filtered_routes/stops.csv')
    .set_index('stop_id')[['stop_lat', 'stop_lon']]
)

# Load initial routes metadata
initial_routes = pd.read_csv("data/raw/initial_routes.csv")

# Precompute max shape_dist_traveled per trip to identify full-length trips
last_stop_dists = stops_df.groupby('trip_id')['shape_dist_traveled'].max()

# --- 2. Extract stop coordinates for longest full trip per route and direction ---

for route_id in trips_df['route_id'].unique():
    for direction in [0, 1]:
        # Filter trips for this route and direction
        trips_for_dir = trips_df[
            (trips_df['route_id'] == route_id) &
            (trips_df['direction_id'] == direction)
        ]

        if trips_for_dir.empty:
            continue

        # Find the trip(s) with the maximum distance traveled
        trip_ids = trips_for_dir['trip_id'].unique()
        dir_dists = last_stop_dists[trip_ids]
        max_dist = dir_dists.max()

        if pd.isna(max_dist) or max_dist == 0:
            continue

        # Pick the first longest trip ID (usually only one)
        longest_trip_id = dir_dists[dir_dists == max_dist].index[0]

        # Get stops of the longest trip ordered by stop_sequence
        trip_stops = (
            stops_df[stops_df['trip_id'] == longest_trip_id]
            .sort_values('stop_sequence')
            .merge(stops_lookup, left_on='stop_id', right_index=True)
            [['stop_lat', 'stop_lon']]
        )

        # Convert to list of [lat, lon] pairs
        coord_array = trip_stops.values.tolist()

        # Column name to save coordinates JSON string
        col_name = f'stop_coordinates_array_{direction}'

        # Ensure the column exists and is of object dtype
        if col_name not in initial_routes.columns:
            initial_routes[col_name] = ""
        initial_routes[col_name] = initial_routes[col_name].astype('object')

        # Store JSON string of coordinates in the DataFrame
        initial_routes.loc[initial_routes['route_id'] == route_id, col_name] = json.dumps(coord_array)

# --- 3. Save the updated initial_routes CSV ---

os.makedirs("data/raw", exist_ok=True)
initial_routes.to_csv("data/raw/initial_routes.csv", index=False)

print("Done! Coordinates saved to stop_coordinates_array_0 and stop_coordinates_array_1.")
