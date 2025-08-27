# -*- coding: utf-8 -*-
"""
Created on Sun Jul 27 12:17:16 2025

This script creates a dummy dataset for charging depots
and saves it to a CSV file. This data will be used as input
for the bus fleet electrification optimization model.

@author: artok
"""

import pandas as pd
import os

# --- Step 1: Define the columns for the charging station dataset ---
# The columns are based on the parameters we need for the optimization model.
depot_columns = [
    'depot_id',
    'location',
    'number_of_chargers',
    'max_charging_power_per_unit_kW',
    'monthly_om_cost_$'
]

# --- Step 2: Create an empty DataFrame with the specified columns ---
charging_depots_df = pd.DataFrame(columns=depot_columns)

'''# --- Step 3: Define the mock data and add it to the DataFrame ---
# The mock data is based on the hypothetical depots we designed earlier.
depot_data = [
    # Data for Depot 1
    ['D1', '[48.118, 11.520]', 50, 150, 5000],
    # Data for Depot 2
    ['D2', '[48.163, 11.605]', 70, 150, 6000],
    # Data for Depot 3
    ['D3', '[48.200, 11.450]', 40, 150, 4500]
]

for row in depot_data:
    # Add each row of data to the DataFrame
    charging_depots_df.loc[len(charging_depots_df)] = row'''

# --- Step 4: Ensure the output directory exists and save the file ---
output_dir = "data/raw"
output_file_path = os.path.join(output_dir, "charging_depots.csv")
os.makedirs(output_dir, exist_ok=True)

charging_depots_df.to_csv(output_file_path, index=False)

print("Charging depots dataset created.")

