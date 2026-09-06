#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Yeraz Electric Transit Optimizer - Greedy Heuristic with Visualization (v2.4 - Final)

This script selects routes for electrification using a greedy heuristic.
This is the final, stable version with all corrections, including corrected
depot-selection logic, enhanced map outputs, and robust JSON file output for the GUI.
"""

import pandas as pd
import os
import math
import json
import sys
import folium
from tabulate import tabulate
import argparse
import shutil
import ast

# --- Helper Functions ---
def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371
    lat1_rad, lon1_rad, lat2_rad, lon2_rad = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2_rad - lon1_rad
    dlat = lat2_rad - lat1_rad
    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def normalize_parameters(df, column_name):
    if column_name not in df.columns or df[column_name].isnull().all():
        return pd.Series([0] * len(df.index), index=df.index)
    max_val = df[column_name].max()
    if pd.isna(max_val) or max_val == 0:
        return pd.Series([0] * len(df.index), index=df.index)
    return df[column_name] / max_val

def empty_directory(dir_path):
    if os.path.exists(dir_path):
        for filename in os.listdir(dir_path):
            file_path = os.path.join(dir_path, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path): os.unlink(file_path)
                elif os.path.isdir(file_path): shutil.rmtree(file_path)
            except Exception: pass

# --- Main Optimizer Logic ---
if __name__ == "__main__":
    if os.name == 'nt':
        sys.stdout.reconfigure(encoding='utf-8')
    SRC_DIR = os.path.dirname(os.path.abspath(__file__))
    PROJECT_ROOT = os.path.dirname(SRC_DIR)

    parser = argparse.ArgumentParser(description='Yeraz Electric Transit Optimizer - Greedy Heuristic')
    parser.add_argument('--w_env', type=float, required=True)
    parser.add_argument('--w_equity', type=float, required=True)
    parser.add_argument('--w_ridership', type=float, required=True)
    parser.add_argument('--budget', type=float, required=True)
    parser.add_argument('--energy_cost', type=float, required=True)
    parser.add_argument('--max_routes', type=int, required=True)
    parser.add_argument('--w_weather', type=float, required=True)
    parser.add_argument('--gamma_safety', type=float, required=True)
    parser.add_argument('--beta_reserve', type=float, required=True)
    parser.add_argument('--t_night', type=int, required=True)
    args = parser.parse_args()

    print("[INFO] Starting Yeraz Greedy Heuristic Optimizer...", flush=True)
    
    data_dir = os.path.join(PROJECT_ROOT, "data", "raw")
    gtfs_dir = os.path.join(PROJECT_ROOT, "data", "filtered_routes")
    
    try:
        initial_routes_df = pd.read_csv(os.path.join(data_dir, "initial_routes.csv"))
        electric_buses_df = pd.read_csv(os.path.join(data_dir, "electric_buses.csv"))
        charging_depots_df = pd.read_csv(os.path.join(data_dir, "charging_depots.csv"))
        stops_df = pd.read_csv(os.path.join(gtfs_dir, "stops.csv"))
    except FileNotFoundError as e:
        print(f"[ERROR] A required file was not found. {e}", flush=True)
        sys.exit(1)

    print("[INFO] All data files loaded successfully.", flush=True)

    stops_df['stop_lat_lon'] = stops_df.apply(lambda r: (round(r['stop_lat'], 6), round(r['stop_lon'], 6)), axis=1)
    stop_names = stops_df.set_index('stop_lat_lon')['stop_name'].to_dict()

    initial_routes_df['normalized_emissions'] = normalize_parameters(initial_routes_df, 'emissions_impact_score')
    initial_routes_df['normalized_equity'] = normalize_parameters(initial_routes_df, 'equity_score')
    initial_routes_df['normalized_ridership'] = normalize_parameters(initial_routes_df, 'daily_passenger_demand')

    routes_dict = initial_routes_df.set_index('route_id').to_dict('index')
    buses_dict = electric_buses_df.set_index('model_id').to_dict('index')
    depots_dict = charging_depots_df.set_index('depot_id').to_dict('index')
    I, J, K = list(routes_dict.keys()), list(buses_dict.keys()), list(depots_dict.keys())

    print("[INFO] Evaluating all feasible route-bus-depot combinations...", flush=True)
    all_feasible_solutions = []
    for i in I:
        route = routes_dict[i]
        objective_score = (args.w_env * route['normalized_emissions'] +
                           args.w_equity * route['normalized_equity'] +
                           args.w_ridership * route['normalized_ridership'])
        required_buses = math.ceil(route['base_buses'] * (1 + args.beta_reserve))
        try:
            coords_0 = ast.literal_eval(str(route['stop_coordinates_array_0'])); coords_1 = ast.literal_eval(str(route['stop_coordinates_array_1']))
            terminus_lat_0, terminus_lon_0 = coords_0[0]; terminus_lat_1, terminus_lon_1 = coords_1[0]
        except (TypeError, ValueError, IndexError, SyntaxError): continue
        
        distance_to_depot = {}
        for k in K:
            try:
                depot_lat, depot_lon = ast.literal_eval(depots_dict[k]['location'])[0]
                dist_0 = haversine_distance(terminus_lat_0, terminus_lon_0, depot_lat, depot_lon)
                dist_1 = haversine_distance(terminus_lat_1, terminus_lon_1, depot_lat, depot_lon)
                distance_to_depot[k] = (dist_0 + dist_1) / 2
            except (TypeError, ValueError, IndexError, SyntaxError): continue
        
        for j in J:
            bus = buses_dict[j]
            if required_buses > bus['fleet_size']: continue
            
            best_option_for_bus = None
            lowest_cost = float('inf')

            for k in K:
                depot = depots_dict[k]
                total_trips_dist = (route['number_of_daily_roundtrips_0'] * route['distance_dir_0_km'] + route['number_of_daily_roundtrips_1'] * route['distance_dir_1_km'])
                avg_bus_daily_dist = total_trips_dist / route['base_buses'] if route['base_buses'] > 0 else 0
                total_daily_dist = avg_bus_daily_dist + 2 * distance_to_depot.get(k, float('inf'))
                
                range_ok = total_daily_dist * args.gamma_safety <= bus['range_km']
                charging_time_req = required_buses * (bus['battery_capacity_kWh'] / depot['max_power_per_point_kW']) * 60
                charging_time_ok = charging_time_req <= depot['number_of_charging_points'] * args.t_night
                
                if range_ok and charging_time_ok:
                    energy_demand = total_daily_dist * bus['energy_use_kWh_per_km'] * args.w_weather * 30
                    total_cost = (required_buses * bus['monthly_om_cost_$']) + depot['monthly_om_cost_$'] + (energy_demand * args.energy_cost)
                    if total_cost < lowest_cost:
                        lowest_cost = total_cost
                        best_option_for_bus = {
                            'objective_score': objective_score, 'route_id': i, 'route_number': route['route_number'],
                            'route_description': route['route_description'], 'bus_model': j, 'depot_id': k,
                            'cost': total_cost, 'required_buses': required_buses,
                            'emissions_impact_score': route['emissions_impact_score'],
                            'ridership': route['daily_passenger_demand'], 'equity_score': route['equity_score']
                        }
            if best_option_for_bus:
                all_feasible_solutions.append(best_option_for_bus)
    
    all_feasible_solutions.sort(key=lambda x: x['objective_score'], reverse=True)
    
    print("[INFO] Greedily selecting the best combination of routes...", flush=True)
    final_solution = []
    allocated_buses = {j:0 for j in J}; allocated_chargers = {k:0 for k in K}; allocated_cost = 0

    for sol in all_feasible_solutions:
        if len(final_solution) >= args.max_routes: break
        r, b, d, req_b, cost = sol['route_id'], sol['bus_model'], sol['depot_id'], sol['required_buses'], sol['cost']
        if (allocated_buses[b] + req_b <= buses_dict[b]['fleet_size'] and
            allocated_chargers[d] + req_b <= depots_dict[d]['number_of_charging_points'] and
            allocated_cost + cost <= args.budget and
            r not in [s['route_id'] for s in final_solution]):
            final_solution.append(sol)
            allocated_buses[b] += req_b; allocated_chargers[d] += req_b; allocated_cost += cost

    map_dir = "solution"
    empty_directory(map_dir); os.makedirs(map_dir, exist_ok=True)
    
    if final_solution:
        print(f"[INFO] Found {len(final_solution)} routes for the final solution.", flush=True)
        table_data = [[s['route_number'], s['route_description'], buses_dict[s['bus_model']]['model_name'],
                       s['required_buses'], s['depot_id'], f"{s['emissions_impact_score']:.2f}",
                       f"{s['ridership']:,.0f}", f"{s['equity_score']:.2f}", f"${s['cost']:,.2f}"] for s in final_solution]
        headers = ["Route", "Description", "Bus Model", "# Buses", "Depot", "Emissions Score", "Ridership", "Equity", "Monthly Cost"]
        table_string = tabulate(table_data, headers=headers, tablefmt="pretty")
        summary_notes = (f"- Total monthly budget used: ${allocated_cost:,.2f} / ${args.budget:,.2f}\n"
                         f"- Total emissions impact score of selected routes: {sum(s['emissions_impact_score'] for s in final_solution):.2f}")
        
        print("[INFO] Generating interactive maps for selected routes...", flush=True)
        map_paths = []
        for sol in final_solution:
            try:
                route_id, route_number, route_desc = sol['route_id'], sol['route_number'], sol['route_description']
                bus_model_id, depot_id, buses_alloc, cost = sol['bus_model'], sol['depot_id'], sol['required_buses'], sol['cost']
                emissions, ridership, equity = sol['emissions_impact_score'], sol['ridership'], sol['equity_score']
                bus_model_name = buses_dict[bus_model_id]['model_name']
                
                depot_coords = ast.literal_eval(str(depots_dict[depot_id]['location']))[0]
                coords_0 = ast.literal_eval(str(routes_dict[route_id].get('stop_coordinates_array_0')) or '[]')
                coords_1 = ast.literal_eval(str(routes_dict[route_id].get('stop_coordinates_array_1')) or '[]')
                full_coords = coords_0 + coords_1
                if not full_coords: continue
                
                m = folium.Map(location=full_coords[0], zoom_start=13, tiles="CartoDB positron")
                folium.PolyLine(full_coords, color='#007bff', weight=5, opacity=0.8, tooltip=f"Route {route_number}").add_to(m)

                for i, stop_coords in enumerate(full_coords):
                    rounded_coords = (round(stop_coords[0], 6), round(stop_coords[1], 6))
                    stop_name = stop_names.get(rounded_coords, f"Stop {i+1}")
                    folium.CircleMarker(location=stop_coords, radius=4, color='#ffc107', fill=True, fill_color='#ffc107', fill_opacity=0.9, tooltip=stop_name).add_to(m)
                
                if depot_coords:
                    folium.Marker(location=depot_coords, icon=folium.Icon(color='red', icon='charging-station', prefix='fa'), tooltip=f"Depot {depot_id}").add_to(m)

                popup_html = f"""
                <b>Route:</b> {route_number} - {route_desc}<br>
                <b>Bus Model:</b> {bus_model_name} ({buses_alloc} buses)<br>
                <b>Depot:</b> {depot_id}<br>
                <hr>
                <b>Monthly Cost:</b> ${cost:,.2f}<br>
                <b>Ridership:</b> {ridership:,.0f} passengers/day<br>
                <b>Emissions Score:</b> {emissions:.2f}<br>
                <b>Equity Score:</b> {equity:.2f}
                """
                folium.Marker(
                    location=full_coords[len(full_coords)//2],
                    popup=folium.Popup(popup_html, max_width=400),
                    icon=folium.Icon(color='blue', icon='info-sign', prefix='fa')
                ).add_to(m)
                
                map_file = os.path.join(map_dir, f"route_{route_number}.html")
                m.save(map_file)
                map_paths.append(map_file)
            except Exception as e:
                print(f"[WARN] Could not generate map for route {sol.get('route_number', 'N/A')}: {e}", flush=True)

    else:
        print("[WARN] No feasible solution found with the given constraints.", flush=True)
        table_string, summary_notes, map_paths = "No routes selected.", "Please relax budget or other constraints.", []

    # --- MODIFIED: Final Step with Robust File Writing ---
    
    # Define project root for creating the correct temp path
    SRC_DIR = os.path.dirname(os.path.abspath(__file__))
    PROJECT_ROOT = os.path.dirname(SRC_DIR)
    
    temp_dir = os.path.join(PROJECT_ROOT, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    results_path = os.path.join(temp_dir, "results.json")
    
    gui_output = {
        "summary_table": table_string,
        "summary_notes": summary_notes,
        "map_paths": map_paths,
        "has_solution": bool(final_solution)
    }
    
    try:
        # First, create the JSON string in memory with UTF-8 characters
        json_string = json.dumps(gui_output, ensure_ascii=False, indent=4)
        
        # Then, write that string to a file opened with UTF-8 encoding
        with open(results_path, 'w', encoding='utf-8') as f:
            f.write(json_string)
            
        print(f"[INFO] Results successfully saved to temporary file: {results_path}", flush=True)
        
    except Exception as e:
        # Use repr(e) to create an error message that is safe to print
        print(f"[ERROR] Failed to write results to JSON file. Details: {repr(e)}", flush=True)
        sys.exit(1)