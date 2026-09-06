# -*- coding: utf-8 -*-
"""
Self-Contained Equity Score Calculator (v7.0 - Unique Temp Dirs)
Adapted for modular execution by the orchestrator.
"""

import geopandas as gpd
import pandas as pd
import rasterio
from shapely.geometry import Point, box
import numpy as np
import ast
import os
from typing import List
from scipy.spatial import cKDTree
from rasterio.mask import mask as rio_mask
from rasterio.warp import calculate_default_transform, reproject, Resampling
import shutil
from datetime import datetime # Import datetime to create unique directory names

# --- Configuration & Parameters ---
TARGET_CRS = "EPSG:32616"
WALKABLE_BUFFER_METERS = 400.0
W_POPULATION = 0.7
W_BUILDING = 0.3

# --- Automated Geospatial File Handling ---
def find_geospatial_files(directory: str, prefix: str) -> List[str]:
    single_file = os.path.join(directory, f"{prefix}.tif")
    if os.path.exists(single_file): return [single_file]
    multi_files = []
    i = 1
    while True:
        multi_file = os.path.join(directory, f"{prefix}_{i}.tif")
        if os.path.exists(multi_file): multi_files.append(multi_file); i += 1
        else: break
    return multi_files

def check_and_reproject_rasters(raster_paths: List[str], target_crs_str: str, temp_dir: str) -> List[str]:
    # Ensure the unique temporary directory exists before use
    os.makedirs(temp_dir, exist_ok=True)
    processed_paths = []
    target_crs_obj = rasterio.crs.CRS.from_string(target_crs_str)
    for src_path in raster_paths:
        with rasterio.open(src_path) as src:
            if src.crs == target_crs_obj:
                processed_paths.append(src_path)
                continue
            print(f"INFO: Equity CRS mismatch for '{os.path.basename(src_path)}'. Reprojecting...")
            dest_filename = os.path.basename(src_path).replace('.tif', '_reprojected.tif')
            dest_path = os.path.join(temp_dir, dest_filename)
            transform, width, height = calculate_default_transform(
                src.crs, target_crs_obj, src.width, src.height, *src.bounds)
            kwargs = src.meta.copy()
            kwargs.update({ 'crs': target_crs_obj, 'transform': transform, 'width': width, 'height': height })
            with rasterio.open(dest_path, 'w', **kwargs) as dst:
                reproject(source=rasterio.band(src, 1), destination=rasterio.band(dst, 1),
                          src_transform=src.transform, src_crs=src.crs,
                          dst_transform=transform, dst_crs=target_crs_obj,
                          resampling=Resampling.nearest)
            processed_paths.append(dest_path)
    return processed_paths

def check_geospatial_overlap(geodata: gpd.GeoDataFrame, raster_paths: List[str]) -> bool:
    bbox = box(*geodata.total_bounds)
    if bbox.is_empty: return False
    for raster_path in raster_paths:
        with rasterio.open(raster_path) as src:
            if src.crs != geodata.crs:
                print(f"ERROR: CRS mismatch between stops ({geodata.crs}) and raster ({src.crs}).")
                return False
            if bbox.intersects(box(*src.bounds)):
                print(f"SUCCESS: Overlap confirmed between data and '{os.path.basename(raster_path)}'.")
                return True
    return False

# --- Core Calculation Functions ---
def parse_stop_coords(raw):
    if raw is None or (isinstance(raw, float) and pd.isna(raw)): return []
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

def extract_unique_stops_from_routes(routes_df: pd.DataFrame) -> gpd.GeoDataFrame:
    print("Extracting unique stop coordinates from all routes...")
    all_coords = set()
    for _, row in routes_df.iterrows():
        coords0 = parse_stop_coords(row.get('stop_coordinates_array_0', '[]'))
        coords1 = parse_stop_coords(row.get('stop_coordinates_array_1', '[]'))
        all_coords.update(coords0)
        all_coords.update(coords1)
    if not all_coords: raise ValueError("No valid stop coordinates found in the routes file.")
    unique_stops_df = pd.DataFrame(list(all_coords), columns=['stop_lon', 'stop_lat'])
    geometry = [Point(xy) for xy in zip(unique_stops_df["stop_lon"], unique_stops_df["stop_lat"])]
    stops_gdf = gpd.GeoDataFrame(unique_stops_df, geometry=geometry, crs="EPSG:4326")
    print(f"Found {len(stops_gdf)} unique stops across all routes.")
    return stops_gdf

def zonal_stat_multitile(raster_paths, geom, stat="mean"):
    for raster_path in raster_paths:
        try:
            if geom is None or geom.is_empty: continue
            with rasterio.open(raster_path) as src:
                out_image, _ = rio_mask(src, [geom], crop=True)
                data = out_image[0].astype(float)
                no_data_value = src.nodata
                if no_data_value is not None: data[data == no_data_value] = np.nan

                if stat == "mean": val = np.nanmean(data)
                elif stat == "sum": val = np.nansum(data)

                if not np.isnan(val): return val
        except Exception: continue
    return np.nan

def calculate_stop_level_eps_simplified(stops_gdf_proj: gpd.GeoDataFrame, pop_tifs: List[str], bld_tifs: List[str]) -> gpd.GeoDataFrame:
    print(f"Calculating metrics for each stop in a {WALKABLE_BUFFER_METERS}m radius...")
    stops_gdf_proj['buffer'] = stops_gdf_proj.geometry.buffer(WALKABLE_BUFFER_METERS)
    stops_gdf_proj['pop_in_buffer'] = stops_gdf_proj['buffer'].apply(lambda geom: zonal_stat_multitile(pop_tifs, geom, stat="sum"))
    stops_gdf_proj['bld_in_buffer'] = stops_gdf_proj['buffer'].apply(lambda geom: zonal_stat_multitile(bld_tifs, geom, stat="mean"))

    for col in ['pop_in_buffer', 'bld_in_buffer']:
        min_val, max_val = stops_gdf_proj[col].min(), stops_gdf_proj[col].max()
        if (max_val - min_val) == 0:
            stops_gdf_proj[f'{col}_norm'] = 0.5
        else:
            stops_gdf_proj[f'{col}_norm'] = (stops_gdf_proj[col] - min_val) / (max_val - min_val)

    stops_gdf_proj['EPS'] = (W_POPULATION * stops_gdf_proj['pop_in_buffer_norm']) + \
                            (W_BUILDING * stops_gdf_proj['bld_in_buffer_norm'])
    return stops_gdf_proj.fillna(0)

def aggregate_eps_to_routes(routes_df: pd.DataFrame, stops_with_eps: gpd.GeoDataFrame) -> pd.DataFrame:
    print("Aggregating stop-level EPS to routes...")
    stop_coords_proj = np.vstack([stops_with_eps.geometry.x, stops_with_eps.geometry.y]).T
    stop_eps_array = stops_with_eps['EPS'].to_numpy()
    stop_tree = cKDTree(stop_coords_proj)

    route_eps_scores = []
    for _, row in routes_df.iterrows():
        eps_values = []
        for direction in ['stop_coordinates_array_0', 'stop_coordinates_array_1']:
            coords_wgs84 = [Point(lon, lat) for lon, lat in parse_stop_coords(row.get(direction, '[]'))]
            if not coords_wgs84: continue
            route_stops_gdf = gpd.GeoDataFrame(geometry=coords_wgs84, crs="EPSG:4326").to_crs(TARGET_CRS)
            route_coords_proj = np.vstack([route_stops_gdf.geometry.x, route_stops_gdf.geometry.y]).T
            dist, idx = stop_tree.query(route_coords_proj, distance_upper_bound=50)
            valid_indices = idx[idx < len(stop_eps_array)]
            if valid_indices.size > 0: eps_values.extend(stop_eps_array[valid_indices])

        route_eps_scores.append(np.mean(eps_values) if eps_values else np.nan)

    routes_df['equity_score'] = route_eps_scores
    min_score, max_score = routes_df['equity_score'].min(), routes_df['equity_score'].max()
    if (max_score - min_score) == 0: routes_df['equity_score'] = 0.5
    else: routes_df['equity_score'] = (routes_df['equity_score'] - min_score) / (max_score - min_score)

    routes_df['equity_score'] = routes_df['equity_score'].fillna(0).round(4)
    return routes_df

# --- Main Orchestrator Function ---
def calculate_equity_score(routes_df: pd.DataFrame, geo_dir: str) -> pd.DataFrame:
    """
    Main function called by the orchestrator to calculate equity score.
    """
    if routes_df.empty:
        print("WARN: Equity score calculation skipped as no routes were provided.")
        return routes_df

    try:
        print("\n--- Equity: Geospatial File Setup ---")
        pop_tifs = find_geospatial_files(geo_dir, "population")
        bld_tifs = find_geospatial_files(geo_dir, "building")
        if not pop_tifs or not bld_tifs:
            raise FileNotFoundError(f"Could not find population/building rasters in {geo_dir}.")

        # --- MODIFICATION START ---
        # Create a unique directory for this run to avoid file lock errors.
        # This replaces the old method of cleaning a static 'temp' directory.
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_specific_dir = os.path.join(os.path.dirname(geo_dir), 'temp_runs', f'run_{timestamp}')
        temp_dir = os.path.join(run_specific_dir, 'reprojected')
        print(f"INFO: Using unique temporary directory for this run: {temp_dir}")
        # --- MODIFICATION END ---

        processed_pop_tifs = check_and_reproject_rasters(pop_tifs, TARGET_CRS, temp_dir)
        processed_bld_tifs = check_and_reproject_rasters(bld_tifs, TARGET_CRS, temp_dir)

        print("\n--- Equity: Data Loading and Validation ---")
        unique_stops_gdf = extract_unique_stops_from_routes(routes_df)
        unique_stops_gdf_proj = unique_stops_gdf.to_crs(TARGET_CRS)

        if not check_geospatial_overlap(unique_stops_gdf_proj, processed_pop_tifs):
            raise SystemExit("Stopping equity calculation: No geographic overlap between stops and rasters.")

        stops_with_eps = calculate_stop_level_eps_simplified(unique_stops_gdf_proj, processed_pop_tifs, processed_bld_tifs)
        final_routes_df = aggregate_eps_to_routes(routes_df, stops_with_eps)

        print(f"\nSUCCESS! Calculated equity scores.")
        return final_routes_df

    except Exception as e:
        print(f"ERROR during equity score calculation: {e}")
        # Return the original dataframe so the pipeline can continue if needed
        if 'equity_score' not in routes_df.columns:
            routes_df['equity_score'] = pd.NA
        return routes_df