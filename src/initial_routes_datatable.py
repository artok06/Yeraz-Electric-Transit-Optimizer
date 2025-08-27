# -*- coding: utf-8 -*-
"""
Created on Sun Jul 13 21:11:25 2025

This script initializes a blank CSV file called 'initial_routes.csv' with the required
columns for electric bus route planning and optimization. It ensures consistent structure
for downstream data processing tasks.

@author: artok
"""

import csv
import os

# -----------------------------------------------------------------------------
# Define the schema (header) of the CSV file with planned route features.
# Columns marked here reflect computed or input attributes for each route.
# -----------------------------------------------------------------------------
route_header = [
    "route_id",                     # Unique identifier for each route
    "route_number",                 # Public-facing route number (e.g. 52, X30)
    "route_description",            # Description or corridor name
    
    "distance_dir_0_km",            # Total route distance in direction 0 (km)
    "distance_dir_1_km",            # Total route distance in direction 1 (km)
    "trip_time_0_min",              # Duration of a one-way trip in dir 0 (min)
    "trip_time_1_min",              # Duration of a one-way trip in dir 1 (min)
    
    "road_gradient_0_%",            # Average elevation gradient for dir 0 (%)
    "road_gradient_1_%",            # Average elevation gradient for dir 1 (%)
    
    "number_of_daily_roundtrips_0", # Estimated roundtrips per day (dir 0 origin)
    "number_of_daily_roundtrips_1", # Estimated roundtrips per day (dir 1 origin)
    
    "stop_coordinates_array_0",     # List of stop coordinates (lat/lon) for dir 0
    "stop_coordinates_array_1",     # List of stop coordinates (lat/lon) for dir 1
    
    "base_buses",                   # Number of buses required to operate the route
                                    # (e.g., “simple # complex” like “5 # 8”)
    
    "passenger_daily_demand",       # Estimated daily ridership (from gravity model)
    "emissions_score",             # Projected CO2 reduction per day (kg)
    "equity_score",                 # Accessibility/equity metric for prioritization
    
    "min_headway_min"
]

# -----------------------------------------------------------------------------
# Set the output directory path for saving the CSV
# -----------------------------------------------------------------------------
output_dir = "data/raw"
os.makedirs(output_dir, exist_ok=True)  # Create the directory if it doesn't exist

# -----------------------------------------------------------------------------
# Construct full path for output file
# -----------------------------------------------------------------------------
file_name = os.path.join(output_dir, "initial_routes.csv")

# -----------------------------------------------------------------------------
# Create and write the header to the CSV file
# -----------------------------------------------------------------------------
with open(file_name, mode='w', newline='') as file:
    writer = csv.writer(file)
    writer.writerow(route_header)

# -----------------------------------------------------------------------------
# Final confirmation
# -----------------------------------------------------------------------------
print(f"Blank CSV with route features has been created: {file_name}.")
