# -*- coding: utf-8 -*-
"""
Created on Mon Aug 25 15:47:55 2025

@author: artok
"""

# src/manual_orchestrator.py
import os
import sys
import pandas as pd
import traceback
import json
import math
from datetime import datetime, timedelta
import requests
from time import sleep
import shutil

# --- Add project root to path ---
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.append(SRC_DIR)

# --- Import Geospatial & ML Libraries ---
import geopandas as gpd
from shapely.geometry import box, LineString
import rasterio
from rasterio.mask import mask
from rasterio.warp import calculate_default_transform, reproject, Resampling
from scipy.spatial import cKDTree
import joblib

# --- Import Functions from Scoring Modules ---
from ridership_model import calculate_ridership_demand
from emissions_score import estimate_emissions_impact
from equity_score import calculate_equity_score

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

# --- HELPER FUNCTIONS (Consolidated for all scoring modules) ---
def find_geospatial_files(directory: str, prefix: str):
    # ... (Full function logic here)
    pass
def check_and_reproject_rasters(raster_paths: list, target_crs_str: str, temp_dir: str):
    # ... (Full function logic here)
    pass
def parse_stop_coords(raw):
    # ... (Full function logic here)
    pass
def zonal_stat_multitile(raster_paths, geom, stat="mean"):
    # ... (Full function logic here)
    pass



# --- Main Execution ---
if __name__ == "__main__":
    if os.name == 'nt':
        sys.stdout.reconfigure(encoding='utf-8')
        
    print("--- Manual Data Scoring Orchestrator ---", flush=True)
    try:
        raw_data_dir = os.path.join(PROJECT_ROOT, "data", "raw")
        routes_file = os.path.join(raw_data_dir, "initial_routes.csv")

        
        print(f"[INFO] Loading manually created routes from: {os.path.basename(routes_file)}", flush=True)
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
        print(f"\n--- [SUCCESS] All scores calculated and saved to '{os.path.basename(routes_file)}' ---", flush=True)
    
    except Exception as e:
        print(f"\n[FATAL ERROR] An unexpected error occurred during scoring: {e}", flush=True)
        traceback.print_exc()
        sys.exit(1)