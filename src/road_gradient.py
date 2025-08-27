# -*- coding: utf-8 -*-
"""
Created on Fri Jul 18 19:27:18 2025

Calculates average road gradient (%) per route and direction by querying elevation data
for the route shapes. The longest shape closest to the route distance is selected per direction.

Steps:
- Loads filtered GTFS trips, shapes, and initial routes data
- Maps each route and direction to the best matching shape (closest length)
- Queries elevations in batches from Open Elevation API
- Computes average uphill gradient percentage clipped to ±15%
- Saves the gradient values into initial_routes DataFrame
- Saves the updated CSV for further processing

Note: Be mindful of API rate limits. The script waits 0.2 seconds between batches.

@author: artok
"""

import pandas as pd
import requests
from time import sleep

# Batch size for elevation API calls to reduce number of requests
BATCH_SIZE = 100

# --- Load Data ---
trips = pd.read_csv('data/filtered_routes/filtered_trips.csv')
initial_routes = pd.read_csv('data/raw/initial_routes.csv')
shapes = pd.read_csv('data/filtered_routes/filtered_shapes.csv')

# --- Precompute shape lengths (max distance traveled per shape_id) ---
shape_lengths = (
    shapes.groupby('shape_id')['shape_dist_traveled']
    .max()
    .reset_index()
    .rename(columns={'shape_dist_traveled': 'shape_length_m'})
)

# Map route_id & direction_id to shape_id for lookup
shape_map = trips[['route_id', 'direction_id', 'shape_id']].drop_duplicates()
shape_lengths = shape_lengths.merge(shape_map, on='shape_id')
shape_lengths['shape_length_km'] = shape_lengths['shape_length_m'] / 1000

# --- Select best matching shape per route and direction ---
best_shapes = {}
for _, row in initial_routes.iterrows():
    route_id = row['route_id']
    for dir_id in [0, 1]:
        target_dist = row[f'distance_dir_{dir_id}_km']
        candidates = shape_lengths[
            (shape_lengths['route_id'] == route_id) &
            (shape_lengths['direction_id'] == dir_id)
        ]
        if not candidates.empty:
            # Find shape with length closest to target distance
            best = candidates.iloc[(candidates['shape_length_km'] - target_dist).abs().argmin()]
            best_shapes[(route_id, dir_id)] = best['shape_id']

# --- Function to get elevations from Open Elevation API ---
def get_elevations(coords):
    """
    Query Open Elevation API for a list of (lat, lon) coordinates.
    Returns a list of elevations (meters). Handles errors gracefully.
    """
    if not coords:
        return []
    locations = "|".join([f"{lat},{lon}" for lat, lon in coords])
    url = f"https://api.open-elevation.com/api/v1/lookup?locations={locations}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        results = response.json().get('results', [])
        return [res['elevation'] for res in results]
    except Exception as e:
        print(f"Elevation API error: {e}")
        return [None] * len(coords)

# --- Compute average uphill gradient (%) for a shape DataFrame ---
def compute_avg_gradient(shape_df):
    """
    Computes average uphill gradient (%) clipped between -15% and +15%.

    Args:
        shape_df (DataFrame): shape points ordered by sequence, containing lat, lon, and shape_dist_traveled.

    Returns:
        float: average uphill gradient percent.
    """
    shape_df = shape_df.sort_values("shape_pt_sequence").copy()
    shape_df['dist_diff'] = shape_df['shape_dist_traveled'].diff().fillna(0)

    coords = list(zip(shape_df['shape_pt_lat'], shape_df['shape_pt_lon']))
    elevations = []

    # Query elevations in batches to respect API limits
    for i in range(0, len(coords), BATCH_SIZE):
        batch = coords[i:i + BATCH_SIZE]
        batch_elevs = get_elevations(batch)
        if any(e is None for e in batch_elevs):
            raise RuntimeError("Elevation lookup failed for some points.")
        elevations.extend(batch_elevs)
        sleep(0.2)  # Pause to avoid hitting API rate limits

    shape_df['elevation'] = elevations
    shape_df['elevation_diff'] = shape_df['elevation'].diff().fillna(0)

    # Gradient percent = elevation change / distance * 100, clipped to ±15%
    shape_df['gradient_percent'] = (
        shape_df['elevation_diff'] / shape_df['dist_diff'].replace(0, 1e-6)
    ) * 100
    shape_df['gradient_percent'] = shape_df['gradient_percent'].clip(-15, 15).fillna(0)

    # Calculate total uphill elevation gain and total distance
    uphill_gain = shape_df[shape_df['elevation_diff'] > 0]['elevation_diff'].sum()
    total_dist = shape_df['shape_dist_traveled'].iloc[-1]

    avg_gradient = round((uphill_gain / total_dist) * 100, 3) if total_dist > 0 else 0
    return avg_gradient

# --- Initialize gradient columns in initial_routes DataFrame ---
initial_routes["road_gradient_0_%"] = None
initial_routes["road_gradient_1_%"] = None

# --- Calculate and assign gradients per route and direction ---
for idx, row in initial_routes.iterrows():
    route_id = row["route_id"]
    print(f"Processing route {route_id}")
    for dir_id in [0, 1]:
        col = f"road_gradient_{dir_id}_%"
        key = (route_id, dir_id)

        if key not in best_shapes:
            print(f"No shape found for direction {dir_id}. Setting {col} = 0")
            initial_routes.at[idx, col] = 0.0
            continue

        shape_id = best_shapes[key]
        shape_df = shapes[shapes["shape_id"] == shape_id]

        try:
            avg_grad = compute_avg_gradient(shape_df)
            initial_routes.at[idx, col] = avg_grad
            print(f"{col}: {avg_grad}%")
        except Exception as e:
            print(f"Failed to compute gradient for route {route_id}, direction {dir_id}: {e}")

# --- Save the updated initial_routes CSV ---
initial_routes.to_csv('data/raw/initial_routes.csv', index=False)
print("Road gradient calculation completed and saved.")
