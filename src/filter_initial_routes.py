# -*- coding: utf-8 -*-
"""
Created on Sun Jul 13 21:11:25 2025

This script filters raw GTFS bus routes data for relevant daytime bus services
and initializes a blank route-level CSV file ('initial_routes.csv') with all
required columns for subsequent electric bus route planning and optimization.

It saves filtered GTFS subsets and a clean initial routes table for downstream analysis.

@author: artok
"""

# src/initial_filtering.py
import os
import pandas as pd

def run_initial_filtering(project_root: str) -> bool:
    """
    Loads raw GTFS data, filters for bus routes, and creates the initial
    data files needed for the rest of the pipeline.
    
    Returns True on success, False on failure.
    """
    print("--- Running Initial GTFS Filtering ---")
    
    # --- 1. Define File Paths ---
    gtfs_dir = os.path.join(project_root, 'data', 'input_gtfs')
    output_filtered_dir = os.path.join(project_root, 'data', 'filtered_routes')
    raw_data_dir = os.path.join(project_root, 'data', 'raw')
    
    os.makedirs(output_filtered_dir, exist_ok=True)
    os.makedirs(raw_data_dir, exist_ok=True)

    # --- 2. Load Raw GTFS Data ---
    try:
        print("Loading GTFS files...")
        routes_df = pd.read_csv(os.path.join(gtfs_dir, 'routes.txt'))
        trips_df = pd.read_csv(os.path.join(gtfs_dir, 'trips.txt'))
        stop_times_df = pd.read_csv(os.path.join(gtfs_dir, 'stop_times.txt'))
        stops_df = pd.read_csv(os.path.join(gtfs_dir, 'stops.txt'))
        # Shapes are optional
        shapes_path = os.path.join(gtfs_dir, 'shapes.txt')
        shapes_df = pd.read_csv(shapes_path) if os.path.exists(shapes_path) else pd.DataFrame()
        print("GTFS files loaded successfully.")
    except FileNotFoundError as e:
        print(f"❌ ERROR: A required GTFS file is missing. {e}")
        return False

    # --- 3. Filter for Bus Routes ---
    # GTFS route_type=3 is Bus. 700-series are extended bus types.
    bus_route_types = [3] + list(range(700, 751))
    filtered_routes = routes_df[routes_df['route_type'].isin(bus_route_types)].copy()

    # --- 4. CRITICAL CHECK: Ensure Bus Routes Were Found ---
    if filtered_routes.empty:
        print("❌ ERROR: No bus routes found in 'routes.txt'.")
        print("Please check that your GTFS data contains routes with 'route_type = 3' (Bus).")
        print(f"Available route types in your file are: {routes_df['route_type'].unique()}")
        return False
    
    print(f"Found {len(filtered_routes)} bus routes.")

    # --- 5. Filter Other GTFS Files Based on Selected Routes ---
    filtered_trips = trips_df[trips_df['route_id'].isin(filtered_routes['route_id'])]
    filtered_stop_times = stop_times_df[stop_times_df['trip_id'].isin(filtered_trips['trip_id'])]
    if not shapes_df.empty:
        filtered_shapes = shapes_df[shapes_df['shape_id'].isin(filtered_trips['shape_id'])]
    else:
        filtered_shapes = pd.DataFrame()
        
    # --- 6. Save the Filtered Subset of GTFS Data ---
    print(f"Saving filtered GTFS data to: {output_filtered_dir}")
    filtered_routes.to_csv(os.path.join(output_filtered_dir, 'filtered_routes.csv'), index=False)
    filtered_trips.to_csv(os.path.join(output_filtered_dir, 'filtered_trips.csv'), index=False)
    filtered_stop_times.to_csv(os.path.join(output_filtered_dir, 'filtered_stop_times.csv'), index=False)
    if not filtered_shapes.empty:
        filtered_shapes.to_csv(os.path.join(output_filtered_dir, 'filtered_shapes.csv'), index=False)
    stops_df.to_csv(os.path.join(output_filtered_dir, 'stops.csv'), index=False)

    # --- 7. Create the Clean initial_routes.csv File ---
    initial_df = pd.DataFrame()
    initial_df["route_id"] = filtered_routes["route_id"]
    # Use .get() for optional columns to avoid errors
    initial_df["route_number"] = filtered_routes.get("route_short_name")
    initial_df["route_description"] = filtered_routes.get("route_long_name")

    placeholder_cols = [
        "distance_dir_0_km", "distance_dir_1_km", "trip_time_0_min", "trip_time_1_min",
        "road_gradient_0_%", "road_gradient_1_%", "number_of_daily_roundtrips_0", 
        "number_of_daily_roundtrips_1", "stop_coordinates_array_0", "stop_coordinates_array_1",
        "bus_model", # Added bus_model for emissions calculation
        "daily_passenger_demand", "emissions_impact_score", "equity_score"
    ]
    for col in placeholder_cols:
        initial_df[col] = None

    output_path = os.path.join(raw_data_dir, "initial_routes.csv")
    initial_df.to_csv(output_path, index=False)
    print(f"Saved a clean base file for {len(initial_df)} routes to '{output_path}'")
    
    return True

if __name__ == "__main__":
    # This allows the script to be run by itself for testing
    project_root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if run_initial_filtering(project_root_dir):
        print("\n--- ✅ Initial filtering script completed successfully. ---")
    else:
        print("\n--- ❌ Initial filtering script failed. Please check the errors above. ---")
