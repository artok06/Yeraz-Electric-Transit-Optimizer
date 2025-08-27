#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CTA Ridership Prediction – Automated Prediction Pipeline (v2.0)
Adapted for modular execution by the orchestrator.
"""

import os
import ast
from typing import List

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import box, LineString

import rasterio
from rasterio.mask import mask as rio_mask
from rasterio.warp import calculate_default_transform, reproject, Resampling
import joblib

# ------------------------
# Configuration (defaults, can be overridden by function args)
# ------------------------
TARGET_CRS = "EPSG:32616"
BUFFER_METERS = 400.0
POP_STAT = "sum"
BLD_STAT = "mean"

# ------------------------
# Geospatial File Handling
# ------------------------
def find_geospatial_files(directory: str, prefix: str) -> List[str]:
    """Finds single (prefix.tif) or multi-tile (prefix_i.tif) rasters."""
    single_file = os.path.join(directory, f"{prefix}.tif")
    if os.path.exists(single_file):
        print(f"INFO: Found single tile: {os.path.basename(single_file)}")
        return [single_file]

    multi_files = []
    i = 1
    while True:
        multi_file = os.path.join(directory, f"{prefix}_{i}.tif")
        if os.path.exists(multi_file):
            multi_files.append(multi_file)
            i += 1
        else:
            break
    if multi_files:
        print(f"INFO: Found {len(multi_files)} multi-tiles for prefix '{prefix}'")
    return multi_files

def check_and_reproject_rasters(raster_paths: List[str], target_crs_str: str, temp_dir: str) -> List[str]:
    """Checks raster CRS and reprojects to target_crs_str if necessary."""
    os.makedirs(temp_dir, exist_ok=True)
    processed_paths = []
    target_crs_obj = rasterio.crs.CRS.from_string(target_crs_str)

    for src_path in raster_paths:
        with rasterio.open(src_path) as src:
            if src.crs == target_crs_obj:
                processed_paths.append(src_path)
                continue

            print(f"INFO: CRS mismatch for '{os.path.basename(src_path)}'. Reprojecting to {target_crs_str}...")

            dest_filename = os.path.basename(src_path).replace('.tif', '_reprojected.tif')
            dest_path = os.path.join(temp_dir, dest_filename)

            transform, width, height = calculate_default_transform(
                src.crs, target_crs_obj, src.width, src.height, *src.bounds)

            kwargs = src.meta.copy()
            kwargs.update({
                'crs': target_crs_obj, 'transform': transform,
                'width': width, 'height': height
            })

            with rasterio.open(dest_path, 'w', **kwargs) as dst:
                reproject(
                    source=rasterio.band(src, 1), destination=rasterio.band(dst, 1),
                    src_transform=src.transform, src_crs=src.crs,
                    dst_transform=transform, dst_crs=target_crs_obj,
                    resampling=Resampling.nearest)

            processed_paths.append(dest_path)

    return processed_paths

def check_geospatial_overlap(routes_gdf: gpd.GeoDataFrame, raster_paths: List[str]) -> bool:
    """Checks if the bounding box of routes intersects with any raster's bounding box."""
    routes_bbox = box(*routes_gdf.total_bounds)
    if routes_bbox.is_empty:
        print("ERROR: Route geometries are empty or invalid.")
        return False

    for raster_path in raster_paths:
        with rasterio.open(raster_path) as src:
            raster_bbox = box(*src.bounds)
            if routes_bbox.intersects(raster_bbox):
                print("SUCCESS: Geospatial overlap confirmed.")
                return True

    print("\nERROR: Geographic overlap check failed!")
    print("Your bus routes and your geospatial .tif files are for different locations.")
    print("Please provide a routes file and .tif files that cover the same area.")
    return False

# ------------------------
# Core Processing Functions
# ------------------------
def parse_stop_coords(raw):
    if raw is None or (isinstance(raw, float) and np.isnan(raw)): return []
    if isinstance(raw, str):
        try: raw = ast.literal_eval(raw)
        except (ValueError, SyntaxError): return []
    if not isinstance(raw, (list, tuple)): return []
    coords = []
    for pt in raw:
        if isinstance(pt, (list, tuple)) and len(pt) == 2:
            try:
                lat, lon = float(pt[0]), float(pt[1])
                coords.append((lon, lat))
            except (ValueError, TypeError): continue
    return coords

def compute_stop_density(df: pd.DataFrame) -> pd.DataFrame:
    df['num_stops_0'] = df['stop_coords_0'].apply(len)
    df['num_stops_1'] = df['stop_coords_1'].apply(len)
    avg_num_stops = df[['num_stops_0', 'num_stops_1']].mean(axis=1)
    avg_distance = df[['distance_dir_0_km', 'distance_dir_1_km']].mean(axis=1)
    df['stop_density_per_km'] = avg_num_stops / avg_distance.replace(0, np.nan)
    df['stop_density_per_km'] = df['stop_density_per_km'].fillna(0)
    df.drop(columns=['num_stops_0', 'num_stops_1'], inplace=True)
    return df

def zonal_stat_raster(raster_path, geom, stat="sum"):
    with rasterio.open(raster_path) as src:
        coords = [geom.__geo_interface__]
        out_image, _ = rio_mask(src, coords, crop=True)
        data = out_image[0].astype(float)
        data[data == src.nodata] = np.nan
        return np.nansum(data) if stat == "sum" else np.nanmean(data)

def zonal_stat_multitile(raster_paths, geom, stat="sum"):
    for raster_path in raster_paths:
        try:
            if geom is None or geom.is_empty: continue
            val = zonal_stat_raster(raster_path, geom, stat)
            if not np.isnan(val): return val
        except Exception: continue
    return np.nan

def add_zonal_stats_multitile(df: pd.DataFrame, pop_tiles: List[str], bld_tiles: List[str]) -> pd.DataFrame:
    df['pop_total'] = df['route_buffer_geom'].apply(lambda geom: zonal_stat_multitile(pop_tiles, geom, POP_STAT) if geom else np.nan)
    df['building_mean'] = df['route_buffer_geom'].apply(lambda geom: zonal_stat_multitile(bld_tiles, geom, BLD_STAT) if geom else np.nan)
    return df

def engineer_base_features(df: pd.DataFrame, target_crs_str: str) -> pd.DataFrame:
    df = df.copy()

    df['line_geom'] = df['stop_coordinates_array_0'].apply(lambda x: LineString(parse_stop_coords(x)) if len(parse_stop_coords(x)) >= 2 else None)
    gdf = gpd.GeoDataFrame(df, geometry='line_geom', crs="EPSG:4326")
    gdf = gdf.to_crs(target_crs_str)
    gdf['route_buffer_geom'] = gdf.geometry.buffer(BUFFER_METERS)

    gdf['distance_mean_km'] = gdf[['distance_dir_0_km', 'distance_dir_1_km']].mean(axis=1)
    gdf['trip_time_mean_min'] = gdf[['trip_time_0_min', 'trip_time_1_min']].mean(axis=1)
    gdf['trip_time_mean_min_clipped'] = gdf['trip_time_mean_min'].clip(upper=300.0)
    gdf['avg_speed_kph'] = gdf['distance_mean_km'] / (gdf['trip_time_mean_min'] / 60.0).replace(0, np.nan)
    gdf['daily_trips'] = gdf[['number_of_daily_roundtrips_0', 'number_of_daily_roundtrips_1']].mean(axis=1)
    gdf['stop_coords_0'] = gdf['stop_coordinates_array_0'].apply(parse_stop_coords)
    gdf['stop_coords_1'] = gdf['stop_coordinates_array_1'].apply(parse_stop_coords)
    gdf = compute_stop_density(gdf)
    gdf['span_proxy'] = gdf['trip_time_mean_min_clipped'] * gdf['daily_trips']

    return pd.DataFrame(gdf)

# ------------------------
# Main Prediction Pipeline Function for Orchestrator
# ------------------------
def calculate_ridership_demand(df_routes: pd.DataFrame, model_path: str, geo_dir: str) -> pd.DataFrame:
    """
    Main function called by the orchestrator to predict ridership.
    """
    if df_routes.empty:
        print("WARN: Ridership calculation skipped as no routes were provided.")
        return df_routes

    # --- Step 1: Find and Validate Geospatial Files ---
    print("--- Ridership: Geospatial File Setup ---")
    pop_files = find_geospatial_files(geo_dir, "population")
    bld_files = find_geospatial_files(geo_dir, "building")
    if not pop_files or not bld_files:
        print(f"ERROR: Could not find population/building .tif files in '{geo_dir}'. Aborting ridership calculation.")
        return df_routes

    # --- Step 2: Automatically Reproject Rasters if Needed ---
    temp_dir = os.path.join(os.path.dirname(geo_dir), 'temp', 'reprojected')
    processed_pop_tifs = check_and_reproject_rasters(pop_files, TARGET_CRS, temp_dir)
    processed_bld_tifs = check_and_reproject_rasters(bld_files, TARGET_CRS, temp_dir)

    # --- Step 3: Load Route Data and Check for Overlap ---
    print("\n--- Ridership: Data Loading and Validation ---")
    temp_geom = df_routes['stop_coordinates_array_0'].apply(lambda x: LineString(parse_stop_coords(x)) if len(parse_stop_coords(x)) >= 2 else None)
    routes_gdf_wgs84 = gpd.GeoDataFrame(df_routes, geometry=temp_geom, crs="EPSG:4326")
    routes_gdf_proj = routes_gdf_wgs84.to_crs(TARGET_CRS)

    if not check_geospatial_overlap(routes_gdf_proj, processed_pop_tifs):
        print("ERROR: Aborting ridership calculation due to no geographic overlap.")
        return df_routes

    # --- Step 4: Full Feature Engineering ---
    print("\n--- Ridership: Feature Engineering ---")
    df_processed = engineer_base_features(df_routes, TARGET_CRS)
    df_processed = add_zonal_stats_multitile(df_processed, processed_pop_tifs, processed_bld_tifs)

    # --- Step 5: Load Model and Predict ---
    print("\n--- Ridership: Prediction ---")
    
    print(f"Loading model pipeline from file: '{os.path.basename(model_path)}'...", flush=True)

    if not os.path.exists(model_path):
        # --- FIX #2: Corrected the error message print statement ---
        print(f"ERROR: Model file not found at path ending with '{os.path.basename(model_path)}'. Cannot predict ridership.", flush=True)
        return df_routes
    pipeline = joblib.load(model_path)

    feature_cols = [
        "distance_mean_km", "trip_time_mean_min_clipped", "daily_trips",
        "avg_speed_kph", "stop_density_per_km", "span_proxy",
        "pop_total", "building_mean"
    ]

    for col in feature_cols:
        if col not in df_processed.columns: df_processed[col] = np.nan
    df_processed[feature_cols] = df_processed[feature_cols].fillna(df_processed[feature_cols].median())

    print("Predicting ridership demand...")
    predicted_log_ridership = pipeline.predict(df_processed[feature_cols])
    
    df_routes['daily_passenger_demand'] = np.expm1(predicted_log_ridership).round().astype(int)

    print("\nSUCCESS! Predicted ridership and added to 'daily_passenger_demand' column.")
    return df_routes