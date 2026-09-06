#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Yeraz Electric Transit Optimizer - Exact MIP (PuLP / CBC)

Drop-in replacement for greedy_heuristic.py:
  * same required CLI arguments
  * reads the same CSVs (data/raw/{initial_routes,electric_buses,charging_depots}.csv,
    data/filtered_routes/stops.csv)
  * writes the same temp/results.json structure and the same per-route folium maps

What changed vs. the greedy (by design, all discussed):
  * Globally optimal selection via branch-and-cut instead of a myopic greedy pass.
  * Depot O&M is charged ONCE per activated depot (greedy double-counted it per route).
  * Service energy is counted for the whole assigned fleet, not a single bus
    (greedy under-counted energy by ~the fleet size). EXPECT higher costs and therefore
    possibly different / fewer selected routes than the greedy for the same budget.
  * Depot energy-supply capacity constraint added (uses charger power).

Optional, off by default so default behaviour matches the greedy:
  * --fare F            include fare revenue offset (default 0 => no fare term)
  * --alpha_gradient A  energy penalty per % gradient, uses road_gradient_*_% columns
                        (default 0 => gradient ignored, as in the greedy)
  * --mu --sigma --epsilon  enable the true chance constraint: feasibility uses the
                        robust multiplier W = mu + Phi^-1(1-eps)*sigma. If omitted,
                        feasibility weather = 1.0 (range uses gamma only, as in the greedy).
                        Energy COST always uses --w_weather (as in the greedy).

Dependency: pip install pulp   (CBC ships with PuLP)
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
import pulp
import requests
import polyline

try:
    from statistics import NormalDist  # stdlib, Python 3.8+
    def _norm_quantile(p):
        return NormalDist().inv_cdf(p)
except Exception:  # pragma: no cover
    def _norm_quantile(p):
        # Acklam-style fallback; only used if statistics.NormalDist is unavailable
        import math as _m
        a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
             1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
        b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
             6.680131188771972e+01, -1.328068155288572e+01]
        c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
             -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
        d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
             3.754408661907416e+00]
        pl, ph = 0.02425, 1 - 0.02425
        if p < pl:
            q = _m.sqrt(-2 * _m.log(p))
            return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
        if p <= ph:
            q = p - 0.5; r = q*q
            return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
        q = _m.sqrt(-2 * _m.log(1-p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


# --- Helper Functions (identical to greedy where shared) ---
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
    
def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371
    lat1_rad, lon1_rad, lat2_rad, lon2_rad = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2_rad - lon1_rad
    dlat = lat2_rad - lat1_rad
    aa = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(aa), math.sqrt(1 - aa))
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


def _f(val, default=0.0):
    """Safe float conversion."""
    try:
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


# --- Main Optimizer Logic ---
if __name__ == "__main__":
    if os.name == 'nt':
        sys.stdout.reconfigure(encoding='utf-8')
    SRC_DIR = os.path.dirname(os.path.abspath(__file__))
    PROJECT_ROOT = os.path.dirname(SRC_DIR)

    parser = argparse.ArgumentParser(description='Yeraz Electric Transit Optimizer - Exact MIP')
    # --- Required args: identical to the greedy heuristic ---
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
    # --- Optional args: default to greedy behaviour ---
    parser.add_argument('--fare', type=float, default=0.0,
                        help='Average fare per passenger ($). 0 => no fare-revenue term.')
    parser.add_argument('--alpha_gradient', type=float, default=0.0,
                        help='Energy penalty per %% gradient. 0 => gradient ignored.')
    parser.add_argument('--mu', type=float, default=None,
                        help='Mean weather multiplier (enables chance constraint with --sigma/--epsilon).')
    parser.add_argument('--sigma', type=float, default=None,
                        help='Std of weather multiplier.')
    parser.add_argument('--epsilon', type=float, default=0.99,
                        help='Allowed violation prob (e.g. 0.05 => 95%% reliability).')
    parser.add_argument('--time_limit', type=int, default=120,
                        help='Solver time limit in seconds.')
    args = parser.parse_args()

    print("[INFO] Starting Yeraz Exact MIP Optimizer...", flush=True)

    # Feasibility weather multiplier: robust quantile if chance-constraint params given,
    # else 1.0 (range/energy feasibility use gamma only, matching the greedy).
    if args.mu is not None and args.sigma is not None and args.epsilon is not None:
        W_feas = args.mu + _norm_quantile(1.0 - args.epsilon) * args.sigma
        print(f"[INFO] Chance constraint ON: W_feas = {W_feas:.4f} "
              f"(mu={args.mu}, sigma={args.sigma}, eps={args.epsilon})", flush=True)
    else:
        W_feas = 1.0
    W_cost = args.w_weather  # energy COST always uses w_weather, as in the greedy

    data_dir = os.path.join(PROJECT_ROOT, "data", "raw")
    gtfs_dir = os.path.join(PROJECT_ROOT, "data", "filtered_routes")

    # 1. Load data
    try:
        initial_routes_df = pd.read_csv(os.path.join(data_dir, "initial_routes.csv"))
        electric_buses_df = pd.read_csv(os.path.join(data_dir, "electric_buses.csv"))
        charging_depots_df = pd.read_csv(os.path.join(data_dir, "charging_depots.csv"))
    except FileNotFoundError as e:
        print(f"[ERROR] Required input CSVs missing: {e}", flush=True)
        sys.exit(1)

    # 2. Optional stop-name lookup (for maps)
    stop_names = {}
    try:
        stops_path = os.path.join(gtfs_dir, "stops.csv")
        if os.path.exists(stops_path):
            stops_df = pd.read_csv(stops_path)
            stops_df['stop_lat_lon'] = stops_df.apply(
                lambda r: (round(r['stop_lat'], 6), round(r['stop_lon'], 6)), axis=1)
            stop_names = stops_df.set_index('stop_lat_lon')['stop_name'].to_dict()
    except Exception as e:
        print(f"[WARN] Could not load stops.csv: {e}", flush=True)

    print("[INFO] Data loaded. Normalizing metrics...", flush=True)

    # 3. Max-normalization (matches the greedy)
    initial_routes_df['normalized_emissions'] = normalize_parameters(initial_routes_df, 'emissions_impact_score')
    initial_routes_df['normalized_equity'] = normalize_parameters(initial_routes_df, 'equity_score')
    initial_routes_df['normalized_ridership'] = normalize_parameters(initial_routes_df, 'daily_passenger_demand')

    routes_dict = initial_routes_df.set_index('route_id').to_dict('index')
    buses_dict = electric_buses_df.set_index('model_id').to_dict('index')
    depots_dict = charging_depots_df.set_index('depot_id').to_dict('index')

    # 4. Build candidate route set + precompute parameters
    #    (skip routes whose coordinates / base_buses cannot be parsed, like the greedy)
    I, route_coords = [], {}
    Dbus, Dbar = {}, {}        # per-bus raw distance (range) and gradient-adjusted (energy)
    req_buses = {}             # ceil(base_buses * (1 + beta))
    dist_depot = {}            # (k, i) -> avg terminus->depot distance (km)
    norm_em, norm_eq, norm_rid = {}, {}, {}
    u = {}                     # daily ridership

    K = list(depots_dict.keys())
    depot_loc = {}
    for k in K:
        try:
            depot_loc[k] = ast.literal_eval(str(depots_dict[k]['location']))[0]
        except Exception:
            depot_loc[k] = None

    for i in routes_dict:
        route = routes_dict[i]
        b_base = _f(route.get('base_buses'), 0.0)
        if b_base <= 0:
            continue
        try:
            coords_0 = ast.literal_eval(str(route['stop_coordinates_array_0']))
            coords_1 = ast.literal_eval(str(route['stop_coordinates_array_1']))
            if not coords_0 or not coords_1:
                continue
            t0_lat, t0_lon = coords_0[0]
            t1_lat, t1_lon = coords_1[0]
        except (ValueError, SyntaxError, IndexError, TypeError):
            continue

        n0 = _f(route.get('number_of_daily_roundtrips_0'))
        n1 = _f(route.get('number_of_daily_roundtrips_1'))
        d0 = _f(route.get('distance_dir_0_km'))
        d1 = _f(route.get('distance_dir_1_km'))
        g0 = _f(route.get('road_gradient_0_%'))
        g1 = _f(route.get('road_gradient_1_%'))

        total_trips_dist = n0 * d0 + n1 * d1
        grad_trips_dist = n0 * d0 * (1 + args.alpha_gradient * abs(g0)) + \
                          n1 * d1 * (1 + args.alpha_gradient * abs(g1))
        Dbus[i] = total_trips_dist / b_base          # raw per-bus daily distance (range)
        Dbar[i] = grad_trips_dist / b_base           # gradient-adjusted per-bus (energy)
        req_buses[i] = math.ceil(b_base * (1 + args.beta_reserve))

        for k in K:
            if depot_loc[k] is None:
                continue
            dk0 = haversine_distance(t0_lat, t0_lon, depot_loc[k][0], depot_loc[k][1])
            dk1 = haversine_distance(t1_lat, t1_lon, depot_loc[k][0], depot_loc[k][1])
            dist_depot[(k, i)] = (dk0 + dk1) / 2

        norm_em[i] = _f(route.get('normalized_emissions'))
        norm_eq[i] = _f(route.get('normalized_equity'))
        norm_rid[i] = _f(route.get('normalized_ridership'))
        u[i] = _f(route.get('daily_passenger_demand'))
        route_coords[i] = (coords_0, coords_1)
        I.append(i)

    J = list(buses_dict.keys())
    N = {j: _f(buses_dict[j].get('fleet_size')) for j in J}
    r = {j: _f(buses_dict[j].get('range_km')) for j in J}
    eta = {j: _f(buses_dict[j].get('energy_use_kWh_per_km')) for j in J}
    battery = {j: _f(buses_dict[j].get('battery_capacity_kWh')) for j in J}
    m_bus = {j: _f(buses_dict[j].get('monthly_om_cost_$')) for j in J}

    v = {k: _f(depots_dict[k].get('number_of_charging_points')) for k in K}
    p_chg = {k: _f(depots_dict[k].get('max_power_per_point_kW')) for k in K}
    m_chg = {k: _f(depots_dict[k].get('monthly_om_cost_$')) for k in K}

    if not I or not J or not K:
        print("[WARN] No candidate routes / buses / depots after parsing.", flush=True)
        sys.exit(0)

    # full charge time per (bus, depot) in minutes  (battery / charger power)
    t_charge = {}
    for j in J:
        for k in K:
            t_charge[(j, k)] = (battery[j] / p_chg[k] * 60.0) if p_chg[k] > 0 else float('inf')

    # 5. Build the MIP
    print("[INFO] Building MIP model...", flush=True)
    prob = pulp.LpProblem("Yeraz_MIP", pulp.LpMaximize)

    ridx = {i: n for n, i in enumerate(I)}
    jidx = {j: n for n, j in enumerate(J)}
    kidx = {k: n for n, k in enumerate(K)}

    x = {i: pulp.LpVariable(f"x_{ridx[i]}", cat="Binary") for i in I}
    y = {(j, i): pulp.LpVariable(f"y_{jidx[j]}_{ridx[i]}", lowBound=0, cat="Integer")
         for j in J for i in I}
    a = {(j, i): pulp.LpVariable(f"a_{jidx[j]}_{ridx[i]}", cat="Binary")
         for j in J for i in I}
    zk = {k: pulp.LpVariable(f"zk_{kidx[k]}", cat="Binary") for k in K}
    zki = {(k, i): pulp.LpVariable(f"zki_{kidx[k]}_{ridx[i]}", cat="Binary")
           for k in K for i in I}
    w = {(k, j, i): pulp.LpVariable(f"w_{kidx[k]}_{jidx[j]}_{ridx[i]}", lowBound=0, cat="Integer")
         for k in K for j in J for i in I}

    # Forbid assignment to depots with unknown distance
    for k in K:
        for i in I:
            if (k, i) not in dist_depot:
                prob += zki[(k, i)] == 0

    # Objective: weighted, max-normalized benefits
    prob += pulp.lpSum(
        (args.w_env * norm_em[i] + args.w_equity * norm_eq[i] + args.w_ridership * norm_rid[i]) * x[i]
        for i in I
    )

    # C1: Monthly budget (bus O&M + depot O&M + energy at expected weather - fare revenue)
    service_energy = pulp.lpSum(eta[j] * y[(j, i)] * Dbar[i] for j in J for i in I)
    deadhead_energy = pulp.lpSum(
        eta[j] * w[(k, j, i)] * 2 * dist_depot[(k, i)]
        for k in K for j in J for i in I if (k, i) in dist_depot
    )
    prob += (
        pulp.lpSum(y[(j, i)] * m_bus[j] for j in J for i in I)
        + pulp.lpSum(zk[k] * m_chg[k] for k in K)
        + 30 * W_cost * args.energy_cost * (service_energy + deadhead_energy)
        - 30 * args.fare * pulp.lpSum(u[i] * x[i] for i in I)
        <= args.budget
    ), "Budget"

    # C2: Fleet availability
    for j in J:
        prob += pulp.lpSum(y[(j, i)] for i in I) <= N[j], f"Fleet_{jidx[j]}"

    # C3: Maximum routes
    prob += pulp.lpSum(x[i] for i in I) <= args.max_routes, "MaxRoutes"

    # C4: Range (chance-constrained when W_feas > 1)
    for i in I:
        max_dd = max((dist_depot[(k, i)] for k in K if (k, i) in dist_depot), default=0.0)
        for j in J:
            M = args.gamma_safety * W_feas * (Dbus[i] + 2 * max_dd)  # tight big-M
            prob += (
                args.gamma_safety * W_feas *
                (Dbus[i] + 2 * pulp.lpSum(zki[(k, i)] * dist_depot[(k, i)]
                                          for k in K if (k, i) in dist_depot))
                <= r[j] + M * (1 - a[(j, i)])
            ), f"Range_{jidx[j]}_{ridx[i]}"

    # C5: Depot activation linking
    for k in K:
        for i in I:
            prob += zki[(k, i)] <= zk[k], f"Activate_{kidx[k]}_{ridx[i]}"

    # C6: Assignment integrity
    for i in I:
        prob += pulp.lpSum(a[(j, i)] for j in J) == x[i], f"OneModel_{ridx[i]}"
        prob += pulp.lpSum(zki[(k, i)] for k in K) == x[i], f"OneDepot_{ridx[i]}"

    # C7: Depot charging-time capacity
    for k in K:
        prob += pulp.lpSum(w[(k, j, i)] * t_charge[(j, k)] for j in J for i in I
                           if t_charge[(j, k)] != float('inf')) <= v[k] * args.t_night, \
            f"ChargeTime_{kidx[k]}"

    # C8: Depot energy-supply capacity (robust weather)
    for k in K:
        prob += (
            args.gamma_safety * W_feas * pulp.lpSum(
                w[(k, j, i)] * eta[j] * (Dbar[i] + 2 * dist_depot[(k, i)])
                for j in J for i in I if (k, i) in dist_depot)
            <= v[k] * p_chg[k] * (args.t_night / 60.0)
        ), f"EnergySupply_{kidx[k]}"

    # C9: Service & fleet coupling
    for i in I:
        for j in J:
            prob += y[(j, i)] >= req_buses[i] * a[(j, i)], f"MinFleet_{jidx[j]}_{ridx[i]}"
            prob += y[(j, i)] <= N[j] * a[(j, i)], f"MaxFleet_{jidx[j]}_{ridx[i]}"

    # C10: Exact linearization of w = z_ki * y_ji
    for k in K:
        for j in J:
            for i in I:
                prob += w[(k, j, i)] <= N[j] * zki[(k, i)], f"Lin1_{kidx[k]}_{jidx[j]}_{ridx[i]}"
                prob += w[(k, j, i)] <= y[(j, i)], f"Lin2_{kidx[k]}_{jidx[j]}_{ridx[i]}"
                prob += w[(k, j, i)] >= y[(j, i)] - N[j] * (1 - zki[(k, i)]), \
                    f"Lin3_{kidx[k]}_{jidx[j]}_{ridx[i]}"

    # 6. Solve
    print("[INFO] Solving (CBC, branch-and-cut)...", flush=True)
    status = prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=args.time_limit))
    status_str = pulp.LpStatus[prob.status]
    print(f"[INFO] Solver status: {status_str}", flush=True)

    # 7. Extract solution
    final_solution = []
    allocated_cost = 0.0
    if prob.status == pulp.LpStatusOptimal or pulp.value(prob.objective) is not None:
        activated_depots = set()
        for i in I:
            if x[i].value() is None or x[i].value() < 0.5:
                continue
            jstar = next((j for j in J if a[(j, i)].value() and a[(j, i)].value() > 0.5), None)
            kstar = next((k for k in K if zki[(k, i)].value() and zki[(k, i)].value() > 0.5), None)
            if jstar is None or kstar is None:
                continue
            ybuses = int(round(y[(jstar, i)].value() or 0))
            dd = dist_depot.get((kstar, i), 0.0)
            route_energy = 30 * W_cost * args.energy_cost * eta[jstar] * (
                ybuses * Dbar[i] + ybuses * 2 * dd)
            route_cost = ybuses * m_bus[jstar] + route_energy  # depot O&M handled once below
            activated_depots.add(kstar)
            final_solution.append({
                'route_id': i,
                'route_number': routes_dict[i].get('route_number'),
                'route_description': routes_dict[i].get('route_description'),
                'bus_model': jstar,
                'depot_id': kstar,
                'required_buses': ybuses,
                'cost': route_cost,
                'emissions_impact_score': _f(routes_dict[i].get('emissions_impact_score')),
                'ridership': _f(routes_dict[i].get('daily_passenger_demand')),
                'equity_score': _f(routes_dict[i].get('equity_score')),
                'norm_emissions': norm_em[i],
                'norm_equity': norm_eq[i],
            })
        depot_om_total = sum(m_chg[k] for k in activated_depots)
        fare_total = 30 * args.fare * sum(u[i] for i in [s['route_id'] for s in final_solution])
        allocated_cost = sum(s['cost'] for s in final_solution) + depot_om_total - fare_total

    # 8. Maps  (same style as greedy_heuristic - Copy.py)
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
                    m = folium.Map(location=stops_coords[0], zoom_start=13, tiles="CartoDB positron")

                    # 1. The Route Line (Neon Cyan) - Uses OSRM Path
                    folium.PolyLine(
                        realistic_path,
                        color="#0076ba", # Cyan / Neon Green
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
                            color="#ffbf00",      # Electric Gold Border
                            fill=True,
                            fill_color='#ffbf00', # Electric Gold Fill
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

    # 9. Output JSON  (superset of both greedy output schemas)
    if final_solution:
        table_data = [[s['route_number'], s['route_description'],
                       buses_dict[s['bus_model']].get('model_name', s['bus_model']),
                       s['required_buses'], s['depot_id'],
                       f"{s['emissions_impact_score']:.2f}", f"{s['ridership']:,.0f}",
                       f"{s['equity_score']:.2f}", f"${s['cost']:,.2f}"] for s in final_solution]
        headers = ["Route", "Description", "Bus Model", "# Buses", "Depot",
                   "Emissions Score", "Ridership", "Equity", "Monthly Cost"]
        table_string = tabulate(table_data, headers=headers, tablefmt="pretty")
        summary_notes = (f"- Solver status: {status_str} (exact MIP)\n"
                         f"- Total monthly budget used: ${allocated_cost:,.2f} / ${args.budget:,.2f}\n"
                         f"- Total emissions impact score: "
                         f"{sum(s['emissions_impact_score'] for s in final_solution):.2f}")
    else:
        table_string = "No routes selected."
        summary_notes = f"Solver status: {status_str}. Please relax budget or other constraints."

    gui_output = {
        "summary_table": table_string,
        "summary_notes": summary_notes,
        "map_paths": map_paths,
        "has_solution": bool(final_solution),
        "solver_status": status_str,
        "total_buses": sum(s['required_buses'] for s in final_solution),
        "total_cost": allocated_cost,
        "total_emissions_reduction": f"{sum(s['emissions_impact_score'] for s in final_solution):.2f}",
        "equity_impact": f"{sum(s['equity_score'] for s in final_solution):.2f}",
        "objective_value": round(pulp.value(prob.objective) or 0.0, 4),
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
                "ridership": s['ridership'],
            } for s in final_solution
        ],
    }

    temp_dir = os.path.join(PROJECT_ROOT, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    results_path = os.path.join(temp_dir, "results.json")
    try:
        with open(results_path, 'w', encoding='utf-8') as f:
            f.write(json.dumps(gui_output, ensure_ascii=False, indent=4))
        print(f"[INFO] Results saved to {results_path}", flush=True)
    except Exception as e:
        print(f"[ERROR] Failed to write results JSON: {repr(e)}", flush=True)
        sys.exit(1)

    print("[INFO] Optimization finished.", flush=True)