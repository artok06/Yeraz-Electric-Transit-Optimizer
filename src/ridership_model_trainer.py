#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug 19 20:01:29 2025

@author: artok


CTA Ridership Prediction – End-to-End Training Pipeline (Fully Corrected)

This script performs the following steps:
1.  Loads CTA route data and ridership information.
2.  Performs feature engineering, including handling of geospatial data.
3.  Correctly parses [latitude, longitude] coordinate data.
4.  Uses GeoPandas to reproject route geometries to match the CRS of
    the input raster files (e.g., population, building density).
5.  Calculates zonal statistics (population, building density) for each route.
6.  Trains a HistGradientBoostingRegressor model using a robust pipeline
    that includes feature scaling.
7.  Evaluates the model using GroupKFold cross-validation.
8.  Saves the final, trained pipeline (preprocessor + model) for later use.
"""

import os
import math
import ast
from typing import List

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString

import rasterio
from rasterio.mask import mask as rio_mask

from sklearn.model_selection import GroupKFold
from sklearn.metrics import mean_squared_error, mean_absolute_percentage_error
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
import joblib

# ------------------------
# Configuration
# ------------------------
RAW_DIR = "data/training"
PROC_DIR = "data/processed"
MODEL_DIR = "models"

INITIAL_ROUTES_CSV = os.path.join(RAW_DIR, "cta_initial_routes.csv")
RIDERSHIP_CSV = os.path.join(RAW_DIR, "cta_ridership.csv")

# IMPORTANT: Update this list to point to your NEW reprojected UTM files
POP_TIFS = [os.path.join(RAW_DIR, f"population_{i}_UTM.tif") for i in range(1, 3) if os.path.isfile(os.path.join(RAW_DIR, f"population_{i}_UTM.tif"))]
BLD_TIFS = [os.path.join(RAW_DIR, f"building_{i}_UTM.tif") for i in range(1, 3) if os.path.isfile(os.path.join(RAW_DIR, f"building_{i}_UTM.tif"))]

BUFFER_METERS = 400.0
POP_STAT = "sum"
BLD_STAT = "mean"
RANDOM_STATE = 42

# ------------------------
# Helper functions
# ------------------------
def parse_stop_coords(raw):
    """
    Correctly parses a string of coordinates, handling the [latitude, longitude]
    format from the source data and converting it to the (longitude, latitude)
    format required by GIS tools.
    """
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return []
    if isinstance(raw, str):
        try:
            raw = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return []
    if not isinstance(raw, (list, tuple)):
        return []

    coords = []
    for pt in raw:
        if isinstance(pt, (list, tuple)) and len(pt) == 2:
            try:
                # The data is in [latitude, longitude] format.
                lat, lon = float(pt[0]), float(pt[1])
                # GIS tools expect (longitude, latitude) format.
                coords.append((lon, lat))
            except (ValueError, TypeError):
                continue
    return coords

def compute_stop_density(df: pd.DataFrame) -> pd.DataFrame:
    df['num_stops_0'] = df['stop_coords_0'].apply(len)
    df['num_stops_1'] = df['stop_coords_1'].apply(len)
    avg_num_stops = df[['num_stops_0', 'num_stops_1']].mean(axis=1)
    avg_distance = df[['distance_dir_0_km', 'distance_dir_1_km']].mean(axis=1)
    df['stop_density_per_km'] = avg_num_stops / avg_distance.replace(0, np.nan)
    df['stop_density_per_km'] = df['stop_density_per_km'].fillna(0) # Fixed for modern pandas
    df.drop(columns=['num_stops_0', 'num_stops_1'], inplace=True)
    return df

# ------------------------
# Zonal stats
# ------------------------
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
            if geom is None or geom.is_empty:
                continue
            val = zonal_stat_raster(raster_path, geom, stat)
            if not np.isnan(val):
                return val
        except Exception:
            continue
    return np.nan

def add_zonal_stats_multitile(df: pd.DataFrame, pop_tiles: List[str], bld_tiles: List[str]) -> pd.DataFrame:
    df['pop_total'] = df['route_buffer_geom'].apply(
        lambda geom: zonal_stat_multitile(pop_tiles, geom, POP_STAT) if geom else np.nan
    )
    df['building_mean'] = df['route_buffer_geom'].apply(
        lambda geom: zonal_stat_multitile(bld_tiles, geom, BLD_STAT) if geom else np.nan
    )
    return df

# ------------------------
# Feature Engineering (with CRS Handling)
# ------------------------
def engineer_base_features(df: pd.DataFrame, raster_files: List[str]) -> pd.DataFrame:
    if not raster_files:
        raise FileNotFoundError("Cannot determine target CRS because no raster files were found.")

    with rasterio.open(raster_files[0]) as src:
        target_crs = src.crs
        print(f"INFO: Target CRS found from raster: {target_crs}")

    # --- THIS IS THE MODIFIED LINE ---
    df = df[(df['number_of_daily_roundtrips_0'] > 8) & (df['number_of_daily_roundtrips_1'] > 8)].copy()
    
    df['line_geom'] = df['stop_coordinates_array_0'].apply(lambda x: LineString(parse_stop_coords(x)) if len(parse_stop_coords(x)) >= 2 else None)

    gdf = gpd.GeoDataFrame(df, geometry='line_geom', crs="EPSG:4326")

    print("INFO: Reprojecting route geometries to match raster CRS...")
    gdf = gdf.to_crs(target_crs)

    print(f"INFO: Creating buffer of {BUFFER_METERS} meters...")
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
# Ridership loader
# ------------------------
def load_ridership(file_path: str) -> pd.DataFrame:
    df = pd.read_csv(file_path)
    df['Month_Beginning'] = pd.to_datetime(df['Month_Beginning'], errors='coerce')
    df = df[df['Month_Beginning'] >= pd.Timestamp('2025-01-01')]
    return df.dropna(subset=['Avg_Weekday_Rides'])

# ------------------------
# Model training
# ------------------------
def train_model(df: pd.DataFrame):
    data = df.dropna(subset=['Avg_Weekday_Rides', 'pop_total', 'building_mean']).copy()
    if data.empty:
        print("[INFO] No rows with ridership target after cleaning. Skipping training.")
        return None, pd.DataFrame(), None

    scaling_features = ["distance_mean_km", "trip_time_mean_min_clipped", "daily_trips"]
    passthrough_features = ["avg_speed_kph", "stop_density_per_km", "span_proxy", "pop_total", "building_mean"]
    feature_cols = scaling_features + passthrough_features
    
    X = data[feature_cols]
    y = np.log1p(data["Avg_Weekday_Rides"])
    
    preprocessor = ColumnTransformer(
        transformers=[('num', StandardScaler(), scaling_features), ('pass', 'passthrough', passthrough_features)],
        remainder='drop')
        
    model_pipeline = Pipeline(steps=[('preprocessor', preprocessor), ('regressor', HistGradientBoostingRegressor(random_state=RANDOM_STATE))])
    
    print("\n--- Starting Cross-Validation ---")
    gkf = GroupKFold(n_splits=5)
    rmse_list, mape_list = [], []
    for fold, (train_idx, val_idx) in enumerate(gkf.split(X, y, groups=data["route_id"])):
        X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]
        model_pipeline.fit(X_train, y_train)
        y_pred_log = model_pipeline.predict(X_val)
        rmse = np.sqrt(mean_squared_error(y_val, y_pred_log))
        mape = mean_absolute_percentage_error(np.expm1(y_val), np.expm1(y_pred_log))
        print(f"Fold {fold+1}: RMSE_log={rmse:.4f}  MAPE={mape:.3f}")
        rmse_list.append(rmse)
        mape_list.append(mape)
        
    print(f"\nCV mean RMSE_log: {np.mean(rmse_list):.4f} ± {np.std(rmse_list):.4f}")
    print(f"CV mean MAPE    : {np.mean(mape_list):.3f} ± {np.std(mape_list):.3f}")
    
    print("\n--- Training Final Model on All Data ---")
    final_pipeline = Pipeline(steps=[('preprocessor', preprocessor),('regressor', HistGradientBoostingRegressor(random_state=RANDOM_STATE))])
    final_pipeline.fit(X, y)
    
    return final_pipeline, data, feature_cols

# ------------------------
# Main pipeline
# ------------------------
def main():
    os.makedirs(PROC_DIR, exist_ok=True)
    os.makedirs(MODEL_DIR, exist_ok=True)

    if not POP_TIFS or not BLD_TIFS:
        print("ERROR: Could not find reprojected raster files (e.g., 'population_1_UTM.tif').")
        print("Please run the reprojection process first.")
        return

    df_features = engineer_base_features(pd.read_csv(INITIAL_ROUTES_CSV), POP_TIFS)
    df_features = add_zonal_stats_multitile(df_features, POP_TIFS, BLD_TIFS)
    
    rid = load_ridership(RIDERSHIP_CSV)
    if rid.empty:
        print('[INFO] No ridership rows available. Exiting.')
        return

    merged = pd.merge(df_features, rid, on='route_id', how='inner')
    print(f"\nINFO: Found {len(merged)} rows after merging features and ridership.")
    print("INFO: Inspecting data before cleaning:")
    print(merged[['route_id', 'Avg_Weekday_Rides', 'pop_total', 'building_mean']].head(10))
    if merged.empty:
        print('[WARN] No overlap between features and ridership. Exiting.')
        return

    pipeline, data_used, feature_cols = train_model(merged)
    if pipeline is None:
        return

    pipeline_path = os.path.join(MODEL_DIR, 'ridership.joblib')
    joblib.dump(pipeline, pipeline_path)
    print(f'Saved trained pipeline -> {pipeline_path}')

    data_used['predicted_ridership'] = np.expm1(pipeline.predict(data_used[feature_cols]))
    output_path = os.path.join(PROC_DIR, 'training_data_with_predictions.csv')
    data_used.to_csv(output_path, index=False)
    print(f'Saved training data with predictions -> {output_path}')

if __name__ == '__main__':
    main()
    