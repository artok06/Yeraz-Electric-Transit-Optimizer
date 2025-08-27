# -*- coding: utf-8 -*-
"""
Self-Contained Emissions Impact Score Calculator
Adapted for modular execution by the orchestrator.
"""

import pandas as pd
import ast

# --- Tunable Physics-Based Parameters ---
GRADIENT_SENSITIVITY_FACTOR = 0.04
STOP_DENSITY_SENSITIVITY_FACTOR = 0.05

# --- Helper Function ---
def parse_and_count_stops(raw) -> int:
    """Parses coordinate string and returns the number of stops."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return 0
    if isinstance(raw, str):
        try:
            return len(ast.literal_eval(raw))
        except (ValueError, SyntaxError):
            return 0
    return 0

# --- Main Calculation Function ---
def estimate_emissions_impact(routes_df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculates an emissions impact score based on route characteristics.
    """
    if routes_df.empty:
        print("WARN: Emissions calculation skipped as no routes were provided.")
        return routes_df

    df = routes_df.copy()

    # --- Step 1: Calculate Total Work (Total Daily Distance) ---
    df["total_daily_km"] = (df["distance_dir_0_km"] * df["number_of_daily_roundtrips_0"]) + \
                           (df["distance_dir_1_km"] * df["number_of_daily_roundtrips_1"])

    # --- Step 2: Calculate Energy Intensity Factors ---
    df["road_gradient_0_%"] = df["road_gradient_0_%"].fillna(0)
    df["road_gradient_1_%"] = df["road_gradient_1_%"].fillna(0)
    df['avg_gradient_%'] = df[["road_gradient_0_%", "road_gradient_1_%"]].abs().mean(axis=1)

    num_stops_0 = df['stop_coordinates_array_0'].apply(parse_and_count_stops)
    num_stops_1 = df['stop_coordinates_array_1'].apply(parse_and_count_stops)
    total_distance_km = df['distance_dir_0_km'] + df['distance_dir_1_km']
    df['avg_stop_density'] = (num_stops_0 + num_stops_1) / total_distance_km.replace(0, pd.NA)
    df['avg_stop_density'] = df['avg_stop_density'].fillna(0)

    # --- Step 3: Combine factors into an Energy Intensity Multiplier ---
    df['energy_intensity_factor'] = 1 + \
                                     (df['avg_gradient_%'] * GRADIENT_SENSITIVITY_FACTOR) + \
                                     (df['avg_stop_density'] * STOP_DENSITY_SENSITIVITY_FACTOR)

    # --- Step 4: Calculate Final Score ---
    # The orchestrator expects the column to be named 'emissions_impact_score'
    df['emissions_impact_score'] = (df['total_daily_km'] * df['energy_intensity_factor']).round(2)
    
    # We only need to return the original dataframe with the new column
    routes_df['emissions_impact_score'] = df['emissions_impact_score']

    print("SUCCESS! Calculated emissions impact score.")
    return routes_df