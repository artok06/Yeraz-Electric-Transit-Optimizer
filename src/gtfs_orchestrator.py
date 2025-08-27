#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Yeraz Automated Data Orchestrator (v4.4 - Integrated Base Bus Calculation)

This version integrates the logic for calculating the required number of base
buses for each route using a sweep-line algorithm on the busiest day's trips.
"""

import os
import sys
import pandas as pd
import argparse
import shutil
import traceback
import json
from datetime import datetime, timedelta
import requests
from time import sleep

# --- Add project root to path ---
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.append(SRC_DIR)

# --- Import Functions from Scoring Modules ---
from ridership_model import calculate_ridership_demand
from emissions_score import estimate_emissions_impact
from equity_score import calculate_equity_score

# --- HELPER FUNCTIONS ---

def clean_directory(dir_path):
    """Creates a directory if it doesn't exist, or clears its contents if it does."""
    if os.path.exists(dir_path):
        for item_name in os.listdir(dir_path):
            item_path = os.path.join(dir_path, item_name)
            try:
                if os.path.isfile(item_path) or os.path.islink(item_path):
                    os.unlink(item_path)
                elif os.path.isdir(item_path):
                    shutil.rmtree(item_path)
            except Exception as e:
                raise Exception(f"Failed to delete {item_path}. Reason: {e}") from e
    else:
        os.makedirs(dir_path)

def validate_gtfs_files(dataframes: dict, required_columns: dict) -> bool:
    """Checks if all required columns are present in the loaded GTFS DataFrames."""
    print("INFO: Validating required GTFS columns...", flush=True)
    for filename, df in dataframes.items():
        if filename in required_columns:
            for column in required_columns[filename]:
                if column not in df.columns:
                    print(f"[ERROR] Input data validation failed.", flush=True)
                    print(f"File '{filename}' is missing the required column: '{column}'.", flush=True)
                    print(f"Available columns are: {list(df.columns)}", flush=True)
                    return False
    print("INFO: GTFS data validation successful.", flush=True)
    return True

def parse_time_to_minutes(time_str):
    try:
        h, m, s = map(int, time_str.split(':'))
        return h * 60 + m + s / 60
    except Exception: return None

def daterange(start_date, end_date):
    for n in range(int((end_date - start_date).days) + 1):
        yield start_date + timedelta(n)

def build_valid_service_dates(calendar_df, calendar_dates_df):
    service_dates = {}
    for _, row in calendar_df.iterrows():
        service_id, start, end = row['service_id'], datetime.strptime(str(row['start_date']), "%Y%m%d"), datetime.strptime(str(row['end_date']), "%Y%m%d")
        valid_dates = {d.strftime("%Y%m%d") for d in daterange(start, end) if row[d.strftime('%A').lower()] == 1}
        service_dates[service_id] = valid_dates
    for _, row in calendar_dates_df.iterrows():
        service_id, date, exc_type = str(row['service_id']), str(row['date']), row['exception_type']
        if service_id not in service_dates: service_dates[service_id] = set()
        if exc_type == 1: service_dates[service_id].add(date)
        elif exc_type == 2: service_dates[service_id].discard(date)
    return service_dates

def get_max_duration(stops_grouped, trip_ids):
    durations = []
    for trip_id in trip_ids:
        try:
            trip_stops = stops_grouped.get_group(trip_id)
            dep = parse_time_to_minutes(trip_stops.iloc[0]['departure_time'])
            arr = parse_time_to_minutes(trip_stops.iloc[-1]['arrival_time'])
            if dep is not None and arr is not None:
                if arr < dep: arr += 24 * 60
                durations.append(arr - dep)
        except (KeyError, IndexError): continue
    return round(max(durations), 1) if durations else None

def get_elevations(coords):
    if not coords: return []
    locations = "|".join([f"{lat},{lon}" for lat, lon in coords])
    url = f"https://api.open-elevation.com/api/v1/lookup?locations={locations}"
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        results = response.json().get('results', [])
        return [res['elevation'] for res in results]
    except requests.exceptions.RequestException as e:
        print(f"[WARN] Elevation API request failed: {e}", flush=True)
        return [None] * len(coords)

def compute_avg_gradient(shape_df):
    BATCH_SIZE = 100
    shape_df = shape_df.sort_values("shape_pt_sequence").copy()
    shape_df['dist_diff'] = shape_df['shape_dist_traveled'].diff().fillna(0)
    coords = list(zip(shape_df['shape_pt_lat'], shape_df['shape_pt_lon']))
    elevations = []
    for i in range(0, len(coords), BATCH_SIZE):
        batch = coords[i:i + BATCH_SIZE]
        batch_elevs = get_elevations(batch)
        if any(e is None for e in batch_elevs):
            raise RuntimeError("Elevation lookup failed for some points.")
        elevations.extend(batch_elevs)
        sleep(0.2)
    shape_df['elevation'] = elevations
    shape_df['elevation_diff'] = shape_df['elevation'].diff().fillna(0)
    uphill_gain = shape_df[shape_df['elevation_diff'] > 0]['elevation_diff'].sum()
    total_dist = shape_df['shape_dist_traveled'].iloc[-1]
    avg_gradient = round((uphill_gain / total_dist) * 100, 3) if total_dist > 0 else 0
    return avg_gradient

# --- PROCESSING FUNCTIONS ---

def calculate_distance_time_trips(project_root: str) -> bool:
    print("[INFO] Starting calculation of distance, time, and trips...", flush=True)
    try:
        raw_dir = os.path.join(project_root, 'data', 'raw')
        filtered_dir = os.path.join(project_root, 'data', 'filtered_routes')
        gtfs_dir = os.path.join(project_root, 'data', 'input_gtfs')
        initial_routes_path = os.path.join(raw_dir, 'initial_routes.csv')
        
        initial_routes = pd.read_csv(initial_routes_path)
        trips_df = pd.read_csv(os.path.join(filtered_dir, 'filtered_trips.csv'))
        stop_times_df = pd.read_csv(os.path.join(filtered_dir, 'filtered_stop_times.csv'))

        if 'shape_dist_traveled' not in stop_times_df.columns:
            print("[ERROR] 'shape_dist_traveled' not in 'stop_times.txt'. Cannot calculate distances.", flush=True)
            return False

        calendar_path, calendar_dates_path = os.path.join(gtfs_dir, 'calendar.txt'), os.path.join(gtfs_dir, 'calendar_dates.txt')
        has_calendar_data = os.path.exists(calendar_path) and os.path.exists(calendar_dates_path)

        if has_calendar_data:
            print("[INFO] Found calendar files. Using detailed monthly trip calculation.", flush=True)
            service_dates = build_valid_service_dates(pd.read_csv(calendar_path), pd.read_csv(calendar_dates_path))
            sep_dates = [d.strftime("%Y%m%d") for d in daterange(datetime(2025, 9, 1), datetime(2025, 9, 30))]
        else:
            print("[WARN] Calendar files not found. Trip counts will be estimated from total unique trips.", flush=True)

        last_stop_dists = stop_times_df.groupby('trip_id')['shape_dist_traveled'].last()
        stops_grouped = stop_times_df.groupby('trip_id')
        
        results = []
        unique_route_ids = initial_routes['route_id'].unique()
        print(f"[INFO] Processing {len(unique_route_ids)} routes...", flush=True)
        for route_id in unique_route_ids:
            trips_for_route = trips_df[trips_df['route_id'] == route_id]
            trips_0 = trips_for_route[trips_for_route['direction_id'] == 0]
            trips_1 = trips_for_route[trips_for_route['direction_id'] == 1]
            max_dist_0 = last_stop_dists.get(trips_0['trip_id'], default=pd.Series(0)).max() if not trips_0.empty else 0
            max_dist_1 = last_stop_dists.get(trips_1['trip_id'], default=pd.Series(0)).max() if not trips_1.empty else 0
            full_0_ids = set(last_stop_dists[last_stop_dists >= (0.9 * max_dist_0)].index.intersection(trips_0['trip_id']))
            full_1_ids = set(last_stop_dists[last_stop_dists >= (0.9 * max_dist_1)].index.intersection(trips_1['trip_id']))
            if has_calendar_data:
                trip_to_service = trips_for_route.set_index('trip_id')['service_id'].to_dict()
                max_trips_0, max_trips_1 = 0, 0
                for date_str in sep_dates:
                    active_0 = sum(1 for tid in full_0_ids if date_str in service_dates.get(trip_to_service.get(tid), set()))
                    active_1 = sum(1 for tid in full_1_ids if date_str in service_dates.get(trip_to_service.get(tid), set()))
                    max_trips_0 = max(max_trips_0, active_0)
                    max_trips_1 = max(max_trips_1, active_1)
            else:
                max_trips_0 = trips_0['trip_id'].nunique()
                max_trips_1 = trips_1['trip_id'].nunique()
            results.append({
                'route_id': route_id, 'distance_dir_0_km': round(max_dist_0 / 1000, 2), 'distance_dir_1_km': round(max_dist_1 / 1000, 2),
                'number_of_daily_roundtrips_0': max_trips_0, 'number_of_daily_roundtrips_1': max_trips_1,
                'trip_time_0_min': get_max_duration(stops_grouped, full_0_ids), 'trip_time_1_min': get_max_duration(stops_grouped, full_1_ids)
            })
        
        results_df = pd.DataFrame(results).set_index('route_id')
        initial_routes = initial_routes.set_index('route_id')
        initial_routes.update(results_df)
        initial_routes.reset_index(inplace=True)
        before_rows = len(initial_routes)
        initial_routes.dropna(subset=['distance_dir_0_km', 'distance_dir_1_km', 'trip_time_0_min', 'trip_time_1_min'], inplace=True)
        initial_routes = initial_routes[(initial_routes['distance_dir_0_km'] > 0) & (initial_routes['distance_dir_1_km'] > 0) & (initial_routes['number_of_daily_roundtrips_0'] > 0) & (initial_routes['number_of_daily_roundtrips_1'] > 0)].copy()
        after_rows = len(initial_routes)
        print(f"[INFO] Removed {before_rows - after_rows} incomplete routes. Remaining: {after_rows}", flush=True)
        initial_routes.to_csv(initial_routes_path, index=False)
        print("[SUCCESS] Calculation of distance, time, and trips complete.", flush=True)
        return True
    except Exception as e:
        print(f"[ERROR] Failed during distance/time/trip calculation: {e}", flush=True)
        traceback.print_exc(); return False

def calculate_stop_coordinates(project_root: str) -> bool:
    print("[INFO] Starting extraction of stop coordinates...", flush=True)
    try:
        raw_dir = os.path.join(project_root, 'data', 'raw')
        filtered_dir = os.path.join(project_root, 'data', 'filtered_routes')
        initial_routes_path = os.path.join(raw_dir, 'initial_routes.csv')
        initial_routes = pd.read_csv(initial_routes_path)
        trips_df = pd.read_csv(os.path.join(filtered_dir, 'filtered_trips.csv'))
        stop_times_df = pd.read_csv(os.path.join(filtered_dir, 'filtered_stop_times.csv'))
        stops_df = pd.read_csv(os.path.join(filtered_dir, 'stops.csv'))
        if 'shape_dist_traveled' not in stop_times_df.columns:
            print("[ERROR] 'shape_dist_traveled' not in 'stop_times.txt'. Cannot determine longest trip.", flush=True); return False
        if not all(c in stops_df.columns for c in ['stop_id', 'stop_lat', 'stop_lon']):
            print("[ERROR] 'stops.txt' is missing required columns: 'stop_id', 'stop_lat', 'stop_lon'.", flush=True); return False
        stops_lookup = stops_df.set_index('stop_id')[['stop_lat', 'stop_lon']]
        last_stop_dists = stop_times_df.groupby('trip_id')['shape_dist_traveled'].max()
        coord_updates = {} 
        print(f"[INFO] Extracting coordinates for {initial_routes['route_id'].nunique()} routes...", flush=True)
        for route_id in initial_routes['route_id'].unique():
            for direction in [0, 1]:
                trips_for_dir = trips_df[(trips_df['route_id'] == route_id) & (trips_df['direction_id'] == direction)]
                if trips_for_dir.empty: continue
                dir_dists = last_stop_dists.get(trips_for_dir['trip_id'], default=None)
                if dir_dists is None or dir_dists.empty: continue
                max_dist = dir_dists.max()
                if pd.isna(max_dist) or max_dist == 0: continue
                longest_trip_id = dir_dists.idxmax()
                trip_stops = stop_times_df[stop_times_df['trip_id'] == longest_trip_id].sort_values('stop_sequence').merge(stops_lookup, left_on='stop_id', right_index=True)[['stop_lat', 'stop_lon']]
                coord_updates[(route_id, direction)] = json.dumps(trip_stops.values.tolist())
        initial_routes['stop_coordinates_array_0'] = initial_routes['route_id'].apply(lambda rid: coord_updates.get((rid, 0)))
        initial_routes['stop_coordinates_array_1'] = initial_routes['route_id'].apply(lambda rid: coord_updates.get((rid, 1)))
        initial_routes.to_csv(initial_routes_path, index=False)
        print("[SUCCESS] Stop coordinate extraction complete.", flush=True)
        return True
    except Exception as e:
        print(f"[ERROR] Failed during stop coordinate extraction: {e}", flush=True)
        traceback.print_exc(); return False

def calculate_road_gradient(project_root: str) -> bool:
    print("[INFO] Starting calculation of road gradients...", flush=True)
    try:
        raw_dir = os.path.join(project_root, 'data', 'raw')
        filtered_dir = os.path.join(project_root, 'data', 'filtered_routes')
        initial_routes_path = os.path.join(raw_dir, 'initial_routes.csv')
        shapes_path = os.path.join(filtered_dir, 'filtered_shapes.csv')
        if not os.path.exists(shapes_path):
            print("[WARN] 'filtered_shapes.csv' not found. Skipping gradient calculation.", flush=True)
            df = pd.read_csv(initial_routes_path); df["road_gradient_0_%"] = 0.0; df["road_gradient_1_%"] = 0.0; df.to_csv(initial_routes_path, index=False)
            return True
        initial_routes = pd.read_csv(initial_routes_path)
        trips_df = pd.read_csv(os.path.join(filtered_dir, 'filtered_trips.csv'))
        shapes_df = pd.read_csv(shapes_path)
        shape_lengths = shapes_df.groupby('shape_id')['shape_dist_traveled'].max().reset_index()
        shape_lengths.rename(columns={'shape_dist_traveled': 'shape_length_m'}, inplace=True)
        shape_map = trips_df[['route_id', 'direction_id', 'shape_id']].drop_duplicates()
        shape_lengths = shape_lengths.merge(shape_map, on='shape_id')
        shape_lengths['shape_length_km'] = shape_lengths['shape_length_m'] / 1000
        best_shapes = {}
        for _, row in initial_routes.iterrows():
            route_id, target_dist_0, target_dist_1 = row['route_id'], row['distance_dir_0_km'], row['distance_dir_1_km']
            candidates = shape_lengths[shape_lengths['route_id'] == route_id]
            if not candidates.empty:
                candidates_0 = candidates[candidates['direction_id'] == 0]
                candidates_1 = candidates[candidates['direction_id'] == 1]
                if not candidates_0.empty: best_shapes[(route_id, 0)] = candidates_0.iloc[(candidates_0['shape_length_km'] - target_dist_0).abs().argmin()]['shape_id']
                if not candidates_1.empty: best_shapes[(route_id, 1)] = candidates_1.iloc[(candidates_1['shape_length_km'] - target_dist_1).abs().argmin()]['shape_id']
        gradient_updates = {}
        print(f"[INFO] Calculating gradients for {len(initial_routes)} routes. This may take time...", flush=True)
        for idx, row in initial_routes.iterrows():
            route_id = row["route_id"]
            print(f"[INFO] Processing route {route_id} ({idx+1}/{len(initial_routes)})...", flush=True)
            for dir_id in [0, 1]:
                key = (route_id, dir_id)
                if key not in best_shapes: gradient_updates[key] = 0.0; continue
                try:
                    shape_id = best_shapes[key]
                    shape_df = shapes_df[shapes_df["shape_id"] == shape_id]
                    avg_grad = compute_avg_gradient(shape_df)
                    gradient_updates[key] = avg_grad
                except Exception as e:
                    print(f"[WARN] Failed to compute gradient for route {route_id}, dir {dir_id}: {e}", flush=True)
                    gradient_updates[key] = 0.0
        initial_routes['road_gradient_0_%'] = initial_routes['route_id'].apply(lambda rid: gradient_updates.get((rid, 0), 0.0))
        initial_routes['road_gradient_1_%'] = initial_routes['route_id'].apply(lambda rid: gradient_updates.get((rid, 1), 0.0))
        initial_routes.to_csv(initial_routes_path, index=False)
        print("[SUCCESS] Road gradient calculation complete.", flush=True)
        return True
    except Exception as e:
        print(f"[ERROR] Failed during road gradient calculation: {e}", flush=True)
        traceback.print_exc(); return False

def calculate_base_buses(project_root: str) -> bool:
    print("[INFO] Starting base bus calculation...", flush=True)
    try:
        raw_dir = os.path.join(project_root, 'data', 'raw')
        filtered_dir = os.path.join(project_root, 'data', 'filtered_routes')
        gtfs_dir = os.path.join(project_root, 'data', 'input_gtfs')
        initial_routes_path = os.path.join(raw_dir, 'initial_routes.csv')
        initial_routes = pd.read_csv(initial_routes_path)
        trips_df = pd.read_csv(os.path.join(filtered_dir, 'filtered_trips.csv'))
        stop_times_df = pd.read_csv(os.path.join(filtered_dir, 'filtered_stop_times.csv'))
        calendar_path, calendar_dates_path = os.path.join(gtfs_dir, 'calendar.txt'), os.path.join(gtfs_dir, 'calendar_dates.txt')
        if not (os.path.exists(calendar_path) and os.path.exists(calendar_dates_path)):
            print("[WARN] Calendar files not found. Cannot perform complex bus calculation. Skipping.", flush=True)
            initial_routes['base_buses'] = 0; initial_routes.to_csv(initial_routes_path, index=False)
            return True
        service_dates = build_valid_service_dates(pd.read_csv(calendar_path), pd.read_csv(calendar_dates_path))
        sep_dates = [d.strftime("%Y%m%d") for d in daterange(datetime(2025, 9, 1), datetime(2025, 9, 30))]
        last_stop_dists = stop_times_df.groupby('trip_id')['shape_dist_traveled'].last()
        
        busiest_day_trips = {}
        for route_id in initial_routes['route_id'].unique():
            trips_for_route = trips_df[trips_df['route_id'] == route_id]
            for dir_id in [0, 1]:
                trips_dir = trips_for_route[trips_for_route['direction_id'] == dir_id]
                if trips_dir.empty: continue
                max_dist = last_stop_dists.get(trips_dir['trip_id'], default=pd.Series(0)).max()
                full_trip_ids = set(last_stop_dists[last_stop_dists >= (0.9 * max_dist)].index.intersection(trips_dir['trip_id']))
                trip_to_service = trips_dir.set_index('trip_id')['service_id'].to_dict()
                max_daily_trips = 0; busiest_day = []
                for date_str in sep_dates:
                    active_trips = [tid for tid in full_trip_ids if date_str in service_dates.get(trip_to_service.get(tid), set())]
                    if len(active_trips) > max_daily_trips: max_daily_trips = len(active_trips); busiest_day = active_trips
                busiest_day_trips[(route_id, dir_id)] = busiest_day

        def calculate_complex_buses_for_route(route_id):
            intervals = []
            for dir_id in [0, 1]:
                for trip_id in busiest_day_trips.get((route_id, dir_id), []):
                    trip_stops = stop_times_df[stop_times_df['trip_id'] == trip_id].sort_values('stop_sequence')
                    if trip_stops.empty: continue
                    dep, arr = parse_time_to_minutes(trip_stops.iloc[0]['departure_time']), parse_time_to_minutes(trip_stops.iloc[-1]['arrival_time'])
                    if dep is not None and arr is not None:
                        if arr < dep: arr += 24 * 60
                        intervals.append((dep, arr))
            if not intervals: return 0
            events = []
            for start, end in intervals: events.append((start, 'start')); events.append((end, 'end'))
            events.sort()
            current_buses = max_buses = 0
            for _, typ in events:
                current_buses += 1 if typ == 'start' else -1; max_buses = max(max_buses, current_buses)
            return max_buses

        bus_updates = {}
        print(f"[INFO] Calculating base buses for {len(initial_routes)} routes...", flush=True)
        for idx, row in initial_routes.iterrows():
            route_id = row["route_id"]
            print(f"[INFO] Processing route {route_id} ({idx+1}/{len(initial_routes)})...", flush=True)
            try:
                bus_updates[route_id] = calculate_complex_buses_for_route(route_id)
            except Exception as e:
                print(f"[WARN] Failed to compute base buses for route {route_id}: {e}", flush=True)
                bus_updates[route_id] = 0
        
        initial_routes['base_buses'] = initial_routes['route_id'].map(bus_updates)
        initial_routes.to_csv(initial_routes_path, index=False)
        print("[SUCCESS] Base bus calculation complete.", flush=True)
        return True
    except Exception as e:
        print(f"[ERROR] Failed during base bus calculation: {e}", flush=True)
        traceback.print_exc(); return False

# --- Main Orchestrator Logic ---
def run_phase_initial(project_root: str):
    try:
        print("--- Orchestrator: Running PHASE=INITIAL ---", flush=True)
        gtfs_dir = os.path.join(project_root, 'data', 'input_gtfs')
        output_filtered_dir = os.path.join(project_root, 'data', 'filtered_routes')
        raw_data_dir = os.path.join(project_root, 'data', 'raw')
        initial_routes_path = os.path.join(raw_data_dir, 'initial_routes.csv')
        
        print("INFO: Cleaning up artifacts from previous runs...", flush=True)
        clean_directory(output_filtered_dir)
        if os.path.exists(initial_routes_path): os.remove(initial_routes_path)
        os.makedirs(raw_data_dir, exist_ok=True)
        
        print("Loading raw GTFS files...", flush=True)
        dataframes = { "routes.txt": pd.read_csv(os.path.join(gtfs_dir, 'routes.txt')), "trips.txt": pd.read_csv(os.path.join(gtfs_dir, 'trips.txt')), "stop_times.txt": pd.read_csv(os.path.join(gtfs_dir, 'stop_times.txt')), "stops.txt": pd.read_csv(os.path.join(gtfs_dir, 'stops.txt')) }
        shapes_path = os.path.join(gtfs_dir, 'shapes.txt')
        if os.path.exists(shapes_path): dataframes["shapes.txt"] = pd.read_csv(shapes_path)
        
        required_cols = {"routes.txt": ["route_id"], "trips.txt": ["route_id", "trip_id"], "stop_times.txt": ["trip_id"]}
        if not validate_gtfs_files(dataframes, required_cols): sys.exit(1)
        
        routes_df = dataframes["routes.txt"]
        if 'route_type' in routes_df.columns:
            filtered_routes = routes_df[routes_df['route_type'].isin([3] + list(range(700, 751)))].copy()
        else:
            print("WARN: 'route_type' column not found, assuming all routes are buses.", flush=True)
            filtered_routes = routes_df.copy()

        if filtered_routes.empty: print("[ERROR] No bus routes were found after filtering.", flush=True); sys.exit(1)
            
        print(f"Found {len(filtered_routes)} bus routes. Saving filtered GTFS subset...", flush=True)
        filtered_routes.to_csv(os.path.join(output_filtered_dir, 'filtered_routes.csv'), index=False)
        
        trips_df, stop_times_df, stops_df = dataframes["trips.txt"], dataframes["stop_times.txt"], dataframes["stops.txt"]
        shapes_df = dataframes.get("shapes.txt", pd.DataFrame())
        
        filtered_trips = trips_df[trips_df['route_id'].isin(filtered_routes['route_id'])]
        filtered_trips.to_csv(os.path.join(output_filtered_dir, 'filtered_trips.csv'), index=False)
        filtered_stop_times = stop_times_df[stop_times_df['trip_id'].isin(filtered_trips['trip_id'])]
        filtered_stop_times.to_csv(os.path.join(output_filtered_dir, 'filtered_stop_times.csv'), index=False)
        stops_df.to_csv(os.path.join(output_filtered_dir, 'stops.csv'), index=False)
        if not shapes_df.empty and 'shape_id' in filtered_trips.columns:
            filtered_shapes = shapes_df[shapes_df['shape_id'].isin(filtered_trips['shape_id'].dropna())]
            filtered_shapes.to_csv(os.path.join(output_filtered_dir, 'filtered_shapes.csv'), index=False)
            
        print("Creating clean 'initial_routes.csv' file...", flush=True)
        initial_df = pd.DataFrame()
        initial_df["route_id"] = filtered_routes["route_id"]
        initial_df["route_number"] = filtered_routes.get("route_short_name")
        initial_df["route_description"] = filtered_routes.get("route_long_name")
        placeholder_cols = [ "distance_dir_0_km", "distance_dir_1_km", "trip_time_0_min", "trip_time_1_min",
                             "road_gradient_0_%", "road_gradient_1_%", "number_of_daily_roundtrips_0", 
                             "number_of_daily_roundtrips_1", "stop_coordinates_array_0", "stop_coordinates_array_1",
                             "daily_passenger_demand", "emissions_impact_score", "equity_score" ]
        for col in placeholder_cols: initial_df[col] = None
        initial_df.to_csv(initial_routes_path, index=False)
        
        print(f"Saved base file for {len(initial_df)} routes to '{os.path.basename(initial_routes_path)}'.", flush=True)
        print("--- [SUCCESS] Initial Phase Complete ---", flush=True)
    except Exception as e:
        print(f"\n[FATAL ERROR] An unexpected error occurred: {e}", flush=True)
        traceback.print_exc(); sys.exit(1)

def run_phase_scoring(project_root, selected_routes):
    print(f"--- Orchestrator: Running PHASE=SCORING for routes: {selected_routes} ---", flush=True)
    routes_file = os.path.join(project_root, 'data', 'raw', 'initial_routes.csv')
    df = pd.read_csv(routes_file)
    df['route_id'] = df['route_id'].astype(str)
    df = df[df['route_id'].isin(selected_routes)]
    df.to_csv(routes_file, index=False)
    print(f"Filtered initial_routes.csv to {len(df)} selected routes.", flush=True)

    processing_steps = [
        calculate_distance_time_trips, 
        calculate_stop_coordinates,
        calculate_road_gradient,
        calculate_base_buses
    ]
    for step_func in processing_steps:
        print(f"\n[STEP] Running: {step_func.__name__}", flush=True)
        if not step_func(project_root):
            print(f"[ERROR] Halting: Step '{step_func.__name__}' failed.", flush=True)
            sys.exit(1)
            
    df = pd.read_csv(routes_file)
            
    print("\n[SCORING] Predicting Ridership Demand...", flush=True)
    model_path = os.path.join(project_root, "models", "ridership.joblib")
    geo_dir = os.path.join(project_root, "data", "input_geospatial")
    df = calculate_ridership_demand(df, model_path, geo_dir)

    print("\n[SCORING] Estimating Emissions Impact...", flush=True)
    df = estimate_emissions_impact(df)

    print("\n[SCORING] Calculating Social Equity Score...", flush=True)
    df = calculate_equity_score(df, geo_dir)
    
    df.to_csv(routes_file, index=False)
    print("--- [SUCCESS] Scoring Phase Complete ---", flush=True)

if __name__ == "__main__":
    if os.name == 'nt':
       sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description="Yeraz Data Processing Orchestrator")
    parser.add_argument("--phase", type=str, required=True, choices=['initial', 'scoring'])
    parser.add_argument("--routes", type=str, help="A comma-separated string of route_ids.")
    args = parser.parse_args()
    if args.phase == 'initial':
        run_phase_initial(PROJECT_ROOT)
    elif args.phase == 'scoring':
        if not args.routes:
            print("[ERROR] --routes argument is required for scoring phase.", flush=True); sys.exit(1)
        run_phase_scoring(PROJECT_ROOT, args.routes.split(','))