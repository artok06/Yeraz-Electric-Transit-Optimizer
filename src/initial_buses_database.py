# -*- coding: utf-8 -*-
"""
Created on Sun Jul 27 12:15:48 2025

This script creates a dummy dataset for electric bus models
and saves it to a CSV file. This data will be used as input
for the bus fleet electrification optimization model.

@author: artok
"""

import pandas as pd
import os

# --- Step 1: Define the columns for the electric bus dataset ---
bus_columns = [
    'model_id',
    'model_name',
    'fleet_size',
    'range_km',
    'energy_use_kWh/km',
    'battery_capacity_kWh',
    'full_charging_time_h',
    'max_charging_power_kW',
    'monthly_om_cost_$'
]

# --- Step 2: Create an empty DataFrame with the specified columns ---
electric_buses_df = pd.DataFrame(columns=bus_columns)

'''# --- Step 3: Define the mock data and add it to the DataFrame ---
bus_data = [
    # Data for 'model_a'
    ['model_a', 'Standard City Bus', 100, 250, 1.5, 400, 2.0, 150, 1500],
    # Data for 'model_b'
    ['model_b', 'Extended Range Bus', 50, 350, 1.2, 420, 3.0, 100, 1800]
]

for row in bus_data:
    # Add each row of data to the DataFrame
    electric_buses_df.loc[len(electric_buses_df)] = row'''

# --- Step 4: Ensure the output directory exists and save the file ---
output_dir = "data/raw"
output_file_path = os.path.join(output_dir, "electric_buses.csv")
os.makedirs(output_dir, exist_ok=True)

electric_buses_df.to_csv(output_file_path, index=False)

print("Electric bus models dataset created.")
