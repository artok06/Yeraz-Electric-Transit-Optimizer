#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Yeraz Electric Transit Optimizer - Greedy Heuristic (Dashboard Compatible + Futuristic Maps)

CORRECTED: cost and feasibility now match the exact MIP, so an exact-vs-greedy
comparison isolates the selection strategy (greedy ranking vs. global optimum)
rather than accounting differences. Fixes:
  (1) energy is fleet-wide (x required_buses), not per-bus;
  (2) depot O&M is charged once per activated depot, not once per route;
  (3) depot capacity is the MIP's time-shared charging limit and energy-supply
      limit, not a "buses <= chargers" slot count.
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
import requests
import polyline


# --- Helper Functions ---
def _f(val, default=0.0):
    """Safe float conversion."""
    try:
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return default
        return float(val)
    except (TypeError, ValueError):
        return default
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

def get_osrm_path(waypoints):
    """
    Takes a list of (lat, lon) tuples (the stops).
    Returns a detailed list of (lat, lon) points following the road network.
    """
    if not waypoints or len(waypoints) < 2:
        return waypoints

    # OSRM expects "lon,lat"
    # We limit the number of waypoints to avoid URL length issues (approx 100 max)
    # If too many stops, we take a subsample to get the general shape
    if len(waypoints) > 80:
        step = len(waypoints) // 80 + 1
        sampled_waypoints = waypoints[::step]
        # Ensure start and end are always included
        if waypoints[-1] != sampled_waypoints[-1]:
            sampled_waypoints.append(waypoints[-1])
        coords_for_api = sampled_waypoints
    else:
        coords_for_api = waypoints

    coords_str = ";".join([f"{lon},{lat}" for lat, lon in coords_for_api])
    url = f"http://router.project-osrm.org/route/v1/driving/{coords_str}?geometries=polyline&overview=full"

    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            print(f"[WARN] OSRM API Error: {r.status_code}. Using straight lines.", flush=True)
            return waypoints # Fallback

        data = r.json()
        if 'routes' not in data or not data['routes']:
            return waypoints

        encoded = data['routes'][0]['geometry']
        path = polyline.decode(encoded)
        return path
    except Exception as e:
        print(f"[WARN] OSRM Failed: {e}. Using straight lines.", flush=True)
        return waypoints

# --- Main Optimizer Logic ---
if __name__ == "__main__":
    if os.name == 'nt':
        sys.stdout.reconfigure(encoding='utf-8')
    SRC_DIR = os.path.dirname(os.path.abspath(__file__))
    PROJECT_ROOT = os.path.dirname(SRC_DIR)

    parser = argparse.ArgumentParser(description='Yeraz Electric Transit Optimizer')
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

    # 1. Load Data
    try:
        initial_routes_df = pd.read_csv(os.path.join(data_dir, "initial_routes.csv"))
        electric_buses_df = pd.read_csv(os.path.join(data_dir, "electric_buses.csv"))
        charging_depots_df = pd.read_csv(os.path.join(data_dir, "charging_depots.csv"))
    except FileNotFoundError as e:
        print(f"[ERROR] Required input CSVs missing: {e}", flush=True)
        sys.exit(1)

    # 2. Optional GTFS Loading
    stop_names = {}
    try:
        stops_path = os.path.join(gtfs_dir, "stops.csv")
        if os.path.exists(stops_path):
            stops_df = pd.read_csv(stops_path)
            stops_df['stop_lat_lon'] = stops_df.apply(lambda r: (round(r['stop_lat'], 6), round(r['stop_lon'], 6)), axis=1)
            stop_names = stops_df.set_index('stop_lat_lon')['stop_name'].to_dict()
    except Exception as e:
        print(f"[WARN] Could not load stops.csv: {e}", flush=True)

    print("[INFO] Data loaded. Normalizing metrics...", flush=True)

    # 3. Normalization
    initial_routes_df['normalized_emissions'] = normalize_parameters(initial_routes_df, 'emissions_impact_score')
    initial_routes_df['normalized_equity'] = normalize_parameters(initial_routes_df, 'equity_score')
    initial_routes_df['normalized_ridership'] = normalize_parameters(initial_routes_df, 'daily_passenger_demand')

    routes_dict = initial_routes_df.set_index('route_id').to_dict('index')
    buses_dict = electric_buses_df.set_index('model_id').to_dict('index')
    depots_dict = charging_depots_df.set_index('depot_id').to_dict('index')
    I, J, K = list(routes_dict.keys()), list(buses_dict.keys()), list(depots_dict.keys())

    # 4. Evaluation Loop
    # Build, per route, the list of feasible (bus, depot) options with MIP-consistent
    # marginal cost and depot load. Depot O&M is NOT included here -- it is charged
    # once per activated depot during selection (step 5).
    route_candidates = {}
    for i in I:
        route = routes_dict[i]
        objective_score = (args.w_env * route['normalized_emissions'] +
                           args.w_equity * route['normalized_equity'] +
                           args.w_ridership * route['normalized_ridership'])

        required_buses = math.ceil(route['base_buses'] * (1 + args.beta_reserve))

        try:
            coords_0 = ast.literal_eval(str(route['stop_coordinates_array_0']))
            coords_1 = ast.literal_eval(str(route['stop_coordinates_array_1']))
            if not coords_0 or not coords_1: continue
            terminus_lat_0, terminus_lon_0 = coords_0[0]
            terminus_lat_1, terminus_lon_1 = coords_1[0]
        except (ValueError, SyntaxError, IndexError): continue

        # Distance to depots
        distance_to_depot = {}
        for k in K:
            try:
                depot_loc = ast.literal_eval(str(depots_dict[k]['location']))
                depot_lat, depot_lon = depot_loc[0]
                dist_0 = haversine_distance(terminus_lat_0, terminus_lon_0, depot_lat, depot_lon)
                dist_1 = haversine_distance(terminus_lat_1, terminus_lon_1, depot_lat, depot_lon)
                distance_to_depot[k] = (dist_0 + dist_1) / 2
            except: continue

        total_trips_dist = (route['number_of_daily_roundtrips_0'] * route['distance_dir_0_km'] +
                            route['number_of_daily_roundtrips_1'] * route['distance_dir_1_km'])
        avg_bus_daily_dist = total_trips_dist / route['base_buses'] if route['base_buses'] > 0 else 0

        options = []
        for j in J:
            bus = buses_dict[j]
            if required_buses > bus['fleet_size']: continue
            for k in K:
                if k not in distance_to_depot: continue
                depot = depots_dict[k]
                total_daily_dist = avg_bus_daily_dist + 2 * distance_to_depot[k]

                # Range feasibility (chance constraint off here -> robust factor Wf = 1.0)
                if total_daily_dist * args.gamma_safety > bus['range_km']:
                    continue

                # FIX 1: fleet-wide energy (x required_buses)
                fleet_energy_month = (required_buses * total_daily_dist *
                                      bus['energy_use_kWh_per_km'] * args.w_weather * 30)
                # Marginal cost = bus O&M + energy. Depot O&M handled once in selection.
                marginal_cost = required_buses * bus['monthly_om_cost_$'] + fleet_energy_month * args.energy_cost

                # FIX 3: depot loads for the MIP's aggregate capacity limits
                charge_time = required_buses * (bus['battery_capacity_kWh'] / depot['max_power_per_point_kW']) * 60
                energy_night = (args.gamma_safety * required_buses *
                                bus['energy_use_kWh_per_km'] * total_daily_dist)

                options.append({
                    'bus_model': j, 'depot_id': k,
                    'marginal_cost': marginal_cost,
                    'charge_time': charge_time,
                    'energy_night': energy_night,
                    'required_buses': required_buses,
                })

        if not options:
            continue
        options.sort(key=lambda o: o['marginal_cost'])
        route_candidates[i] = {
            'route_id': i,
            'route_number': route['route_number'],
            'route_description': route['route_description'],
            'objective_score': objective_score,
            'required_buses': required_buses,
            'emissions_impact_score': route['emissions_impact_score'],
            'ridership': route['daily_passenger_demand'],
            'equity_score': route['equity_score'],
            'norm_emissions': route['normalized_emissions'],
            'norm_equity': route['normalized_equity'],
            'options': options,
        }

    # 5. Greedy Selection
    # Pick routes in descending objective score. For each, take the cheapest option
    # that still fits fleet, per-depot charging-time, per-depot energy supply, and
    # budget. Depot O&M (FIX 2) is added only the first time a depot is activated.
    print("[INFO] Selecting routes...", flush=True)
    final_solution = []
    allocated_buses = {j: 0 for j in J}
    allocated_charge_time = {k: 0.0 for k in K}
    allocated_energy_night = {k: 0.0 for k in K}
    active_depots = set()
    allocated_cost = 0.0

    ranked = sorted(route_candidates.values(), key=lambda c: c['objective_score'], reverse=True)
    for cand in ranked:
        if len(final_solution) >= args.max_routes:
            break
        chosen = None
        for opt in cand['options']:
            j, k, req_b = opt['bus_model'], opt['depot_id'], opt['required_buses']
            depot = depots_dict[k]
            cap_time = depot['number_of_charging_points'] * args.t_night
            cap_energy = depot['number_of_charging_points'] * depot['max_power_per_point_kW'] * (args.t_night / 60.0)
            depot_fee = 0.0 if k in active_depots else depot['monthly_om_cost_$']

            if (allocated_buses[j] + req_b <= buses_dict[j]['fleet_size'] and
                allocated_charge_time[k] + opt['charge_time'] <= cap_time and
                allocated_energy_night[k] + opt['energy_night'] <= cap_energy and
                allocated_cost + opt['marginal_cost'] + depot_fee <= args.budget):
                chosen = (opt, depot_fee)
                break

        if chosen is None:
            continue
        opt, depot_fee = chosen
        j, k, req_b = opt['bus_model'], opt['depot_id'], opt['required_buses']
        allocated_buses[j] += req_b
        allocated_charge_time[k] += opt['charge_time']
        allocated_energy_night[k] += opt['energy_night']
        allocated_cost += opt['marginal_cost'] + depot_fee
        active_depots.add(k)

        final_solution.append({
            'objective_score': cand['objective_score'],
            'route_id': cand['route_id'],
            'route_number': cand['route_number'],
            'route_description': cand['route_description'],
            'bus_model': j,
            'depot_id': k,
            'cost': opt['marginal_cost'],
            'required_buses': req_b,
            'emissions_impact_score': cand['emissions_impact_score'],
            'ridership': cand['ridership'],
            'equity_score': cand['equity_score'],
            'norm_emissions': cand['norm_emissions'],
            'norm_equity': cand['norm_equity'],
        })

    # 6. Map Generation
    map_dir = "solution"
    empty_directory(map_dir); os.makedirs(map_dir, exist_ok=True)
    map_paths = []

    if final_solution:
        print(f"[INFO] Selected {len(final_solution)} routes. Generating realistic maps...", flush=True)
        for sol in final_solution:
            try:
                # Retrieve coordinates (STOPS)
                r_id = sol['route_id']
                coords_0 = ast.literal_eval(str(routes_dict[r_id].get('stop_coordinates_array_0') or '[]'))
                coords_1 = ast.literal_eval(str(routes_dict[r_id].get('stop_coordinates_array_1') or '[]'))
                stops_coords = coords_0 + coords_1

                # Retrieve Info for Popups
                route_number = sol['route_number']
                route_desc = sol['route_description']
                bus_model_name = buses_dict[sol['bus_model']]['model_name']
                cost = sol['cost']
                depot_id = sol['depot_id']
                depot_coords = ast.literal_eval(str(depots_dict[depot_id]['location']))[0]
                ridership = sol['ridership']
                emissions = sol['emissions_impact_score']
                equity = sol['equity_score']

                if stops_coords:
                    # FETCH REALISTIC PATH FROM OSRM
                    # Pass the stops to OSRM to get the road shape
                    realistic_path = get_osrm_path(stops_coords)

                    # --- FUTURISTIC MAP LOGIC ---
                    m = folium.Map(location=stops_coords[0], zoom_start=13, tiles="CartoDB dark_matter")

                    # 1. The Route Line (Neon Cyan) - Uses OSRM Path
                    folium.PolyLine(
                        realistic_path,
                        color='#00ffcc', # Cyan / Neon Green
                        weight=5,
                        opacity=0.8,
                        tooltip=f"Route {route_number} (Optimized Path)"
                    ).add_to(m)

                    # 2. The Stops (Electric Gold Dots) - Uses Original Stop Coords
                    for i, stop_coords in enumerate(stops_coords):
                        rounded_coords = (round(stop_coords[0], 6), round(stop_coords[1], 6))
                        stop_name = stop_names.get(rounded_coords, f"Stop {i+1}")

                        folium.CircleMarker(
                            location=stop_coords,
                            radius=4,
                            color='#ffd700',      # Electric Gold Border
                            fill=True,
                            fill_color='#ffd700', # Electric Gold Fill
                            fill_opacity=1.0,
                            tooltip=stop_name
                        ).add_to(m)

                    # 3. The Depot (Red Bolt)
                    if depot_coords:
                        folium.Marker(
                            location=depot_coords,
                            icon=folium.Icon(color='red', icon='charging-station', prefix='fa'),
                            tooltip=f"Depot {depot_id}"
                        ).add_to(m)

                    # 4. Info Popup
                    popup_html = f"""
                    <div style="font-family: sans-serif; min-width: 180px;">
                        <h4 style="margin: 0 0 8px 0; color: #333;">Route {route_number}</h4>
                        <span style="color: #666; font-size: 0.9em;">{route_desc}</span><br>
                        <br>
                        <b>Bus Model:</b> {bus_model_name}<br>
                        <b>Depot:</b> {depot_id}<br>
                        <hr style="border: 0; border-top: 1px solid #ccc; margin: 8px 0;">
                        <b>Monthly Cost:</b> ${cost:,.0f}<br>
                        <b>Daily Ridership:</b> {ridership:,.0f}<br>
                        <b>Emissions Score:</b> {emissions:.2f}<br>
                        <b>Equity Score:</b> {equity:.2f}
                    </div>
                    """
                    folium.Marker(
                        location=stops_coords[len(stops_coords)//2],
                        popup=folium.Popup(popup_html, max_width=300),
                        icon=folium.Icon(color='blue', icon='info-sign', prefix='fa')
                    ).add_to(m)

                    map_file = os.path.join(map_dir, f"route_{sol['route_number']}.html")
                    m.save(map_file)
                    map_paths.append(map_file)
            except Exception as e:
                print(f"[WARN] Map error for {sol['route_number']}: {e}", flush=True)

    # 7. Generate Output JSON
    table_data = [[s['route_number'], s['route_description'], buses_dict[s['bus_model']]['model_name'], f"${s['cost']:,.0f}"] for s in final_solution]
    table_string = tabulate(table_data, headers=["Route", "Desc", "Bus", "Cost"], tablefmt="pretty")

    summary_notes = f"Selected {len(final_solution)} routes within budget of ${args.budget:,.0f}."

    gui_output = {
        "summary_table": table_string,
        "summary_notes": summary_notes,
        "map_paths": map_paths,
        "has_solution": bool(final_solution),
        "total_buses": sum(s['required_buses'] for s in final_solution),
        "total_cost": allocated_cost,
        "total_emissions_reduction": f"{sum(s['emissions_impact_score'] for s in final_solution):.2f}",
        "equity_impact": f"{sum(s['equity_score'] for s in final_solution):.2f}",
        "sum_norm_equity": round(sum(s['norm_equity'] for s in final_solution), 4),
        "sum_norm_ridership": round(sum(_f(routes_dict[s['route_id']].get('normalized_ridership')) for s in final_solution), 4),
        "sum_norm_emissions": round(sum(s['norm_emissions'] for s in final_solution), 4),
        "n_routes": len(final_solution),
        "selected_routes": [
            {
                "route_id": s['route_number'],
                "description": s['route_description'],
                "cost": s['cost'],
                "emissions_score": round(s['norm_emissions'] * 100, 1),
                "equity_score": round(s['norm_equity'] * 100, 1),
                "ridership": s['ridership']
            }
            for s in final_solution
        ]
    }

    temp_dir = os.path.join(PROJECT_ROOT, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    with open(os.path.join(temp_dir, "results.json"), 'w', encoding='utf-8') as f:
        json.dump(gui_output, f, ensure_ascii=False, indent=4)

    print("[INFO] Optimization finished. Results saved.", flush=True)