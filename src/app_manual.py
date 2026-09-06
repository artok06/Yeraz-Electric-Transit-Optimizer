# src/app_manual.py
import streamlit as st
import pandas as pd
import os
import sys
import subprocess
import threading
import queue
import json
import time
import math
import shutil
import streamlit.components.v1 as components
from html import escape

import folium
from folium.plugins import Draw
from streamlit_folium import st_folium
import requests
import polyline

# --- Configuration ---
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SRC_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
RAW_DATA_DIR = os.path.join(DATA_DIR, "raw")
INPUT_GEO_DIR = os.path.join(DATA_DIR, "input_geospatial")
SOLUTION_DIR = os.path.join(PROJECT_ROOT, "solution")
TEMP_DIR = os.path.join(PROJECT_ROOT, "temp")

# --- Helper Functions ---
def clean_directory(dir_path):
    if not os.path.exists(dir_path): os.makedirs(dir_path); return
    for item_name in os.listdir(dir_path):
        item_path = os.path.join(dir_path, item_name)
        try:
            if os.path.isfile(item_path) or os.path.islink(item_path): os.unlink(item_path)
            elif os.path.isdir(item_path): shutil.rmtree(item_path)
        except Exception as e:
            raise Exception(f"Failed to delete {item_path}. Reason: {e}.") from e

def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371; lat1_rad, lon1_rad, lat2_rad, lon2_rad = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2_rad - lon1_rad; dlat = lat2_rad - lat1_rad
    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)); return R * c

def get_osrm_route(coords):
    if len(coords) < 2: return [], 0
    coordinates_str = ";".join([f"{c[1]},{c[0]}" for c in coords])
    url = f"http://router.project-osrm.org/route/v1/driving/{coordinates_str}?geometries=polyline&overview=full"
    try:
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            data = response.json()
            distance_km = data['routes'][0]['distance'] / 1000
            decoded_coords = polyline.decode(data['routes'][0]['geometry'])
            return decoded_coords, distance_km
    except Exception as e:
        st.warning(f"OSRM Error: Could not snap to road network. Using straight lines. Details: {e}")
    total_dist = sum(haversine_distance(coords[i][0], coords[i][1], coords[i+1][0], coords[i+1][1]) for i in range(len(coords)-1))
    return coords, total_dist

def get_elevations(coords):
    elevations = []
    for i in range(0, len(coords), 100):
        batch = coords[i:i + 100]
        locations = "|".join([f"{c[0]},{c[1]}" for c in batch])
        url = f"https://api.open-elevation.com/api/v1/lookup?locations={locations}"
        try:
            response = requests.get(url, timeout=20)
            response.raise_for_status()
            results = response.json().get('results', [])
            elevations.extend([res['elevation'] for res in results])
            time.sleep(0.2)
        except Exception as e:
            st.warning(f"Elevation API Error: {e}. Using 0 for elevation.")
            return [0] * len(coords)
    return elevations

def compute_avg_uphill_gradient(coords):
    if len(coords) < 2: return 0
    elevations = get_elevations(coords)
    uphill_gain, total_dist = 0, 0
    for i in range(len(coords) - 1):
        dist_segment = haversine_distance(coords[i][0], coords[i][1], coords[i+1][0], coords[i+1][1])
        if len(elevations) > i+1:
            elev_diff = elevations[i+1] - elevations[i]
            total_dist += dist_segment
            if elev_diff > 0: uphill_gain += elev_diff
    return round((uphill_gain / (total_dist * 1000)) * 100, 3) if total_dist > 0 else 0

def run_subprocess_in_thread(cmd, log_placeholder):
    q = queue.Queue()

    scroll_script = """
        <script>
            var logBox = parent.document.getElementById("log-box");
            if (logBox) {
                logBox.scrollTop = logBox.scrollHeight;
            }
        </script>
    """

    def worker():
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO(); startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        proc = subprocess.Popen(
            cmd, cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, encoding='utf-8', startupinfo=startupinfo)
        for line in iter(proc.stdout.readline, ''): q.put(line)
        return_code = proc.wait()
        q.put(('PROCESS_DONE', return_code))

    thread = threading.Thread(target=worker, daemon=True); thread.start()
    output_lines = []
    final_return_code = -1

    while True:
        item = q.get()
        if isinstance(item, tuple) and item[0] == 'PROCESS_DONE':
            final_return_code = item[1]
            break
        
        output_lines.append(item.rstrip())
        
        with log_placeholder.container():
            # Escape HTML characters in log lines for safety
            escaped_lines = [escape(line) for line in output_lines]
            log_html = "<br>".join(escaped_lines)
            
            st.markdown(
                f'<div id="log-box" style="height:250px; overflow-y:auto; color: white; background-color: #111; white-space:pre-wrap; font-family:monospace; border:1px solid #ccc; padding:10px; border-radius: 5px;">{log_html}</div>',
                unsafe_allow_html=True
            )
            components.html(scroll_script, height=0)
        time.sleep(0.05)
    
    return "".join(output_lines), final_return_code

# --- Streamlit App ---
st.set_page_config(layout="wide", page_title="Yeraz Route Creator")
st.title("Yeraz Optimizer (No-GTFS Workflow)")

# --- Session State Management ---
if 'stage' not in st.session_state: st.session_state.stage = 'create_routes'
if 'manual_routes' not in st.session_state: st.session_state.manual_routes = []
if 'current_route' not in st.session_state: st.session_state.current_route = {}

# --- Sidebar (appears after route creation) ---
if st.session_state.stage not in ['create_routes', 'upload_rasters']:
    st.sidebar.title("Optimization Parameters")
    w_env = st.sidebar.slider("Environmental Weight", 0.0, 1.0, 0.40, 0.05)
    w_equity = st.sidebar.slider("Equity Weight", 0.0, 1.0, 0.35, 0.05)
    w_ridership = st.sidebar.slider("Ridership Weight", 0.0, 1.0, 0.25, 0.05)
    st.sidebar.subheader("Financial Constraints")
    budget = st.sidebar.number_input("Monthly Budget ($)", min_value=100000, value=500000)
    energy_cost = st.sidebar.number_input("Energy Cost ($/kWh)", min_value=0.01, value=0.20, format="%.2f")
    st.sidebar.subheader("Operational Constraints")
    max_routes = st.sidebar.number_input("Max Routes to Electrify", min_value=1, value=5, step=1)
    w_weather = st.sidebar.number_input("Weather Factor", min_value=1.0, value=1.10, format="%.2f")
    gamma_safety = st.sidebar.number_input("Battery Safety Factor", min_value=1.0, value=1.15, format="%.2f")
    beta_reserve = st.sidebar.number_input("Reserve Fleet Factor", min_value=0.0, value=0.10, format="%.2f")
    t_night = st.sidebar.number_input("Night Charging Time (minutes)", min_value=60, value=480, step=30)

# --- Main App Body ---

if st.session_state.stage == 'create_routes':
    st.header("Step 1: Create Your Bus Routes")
    col1, col2 = st.columns([1, 2])
    with col1:
        st.subheader("A. Enter Route Parameters")
        with st.form("route_form", clear_on_submit=True):
            route_id = st.text_input("Route ID (Unique)", f"MANUAL-{len(st.session_state.manual_routes) + 1}")
            route_number = st.text_input("Route Number (e.g., M1)")
            route_desc = st.text_input("Route Description (e.g., Central Station Loop)")
            avg_speed_kmh = st.number_input("Average Speed (km/h)", 1.0, 100.0, 20.0, 0.5)
            service_hours = st.number_input("Daily Service Hours", 1.0, 24.0, 18.0, 0.5)
            min_headway = st.number_input("Peak Headway (minutes)", 1.0, 120.0, 15.0, 1.0)
            submitted = st.form_submit_button("Add This Route to the List Below")

        if submitted:
            if not all([route_id, route_number, route_desc]): st.error("Please fill in all route parameter fields.")
            elif 'osrm_coords_0' not in st.session_state.current_route: st.error("Please draw and process at least Direction 0.")
            else:
                dist_0, dist_1 = st.session_state.current_route.get('dist_0',0), st.session_state.current_route.get('dist_1',0)
                trip_time_0, trip_time_1 = (dist_0/avg_speed_kmh)*60 if avg_speed_kmh>0 else 0, (dist_1/avg_speed_kmh)*60 if avg_speed_kmh>0 else 0
                daily_trips = round((60 / min_headway) * service_hours) if min_headway > 0 else 0
                base_buses = math.ceil((trip_time_0 + trip_time_1) / min_headway) if min_headway > 0 else 1
                
                # --- THIS SECTION IS NOW CORRECTED ---
                new_route = {
                    'route_id': route_id, 'route_number': route_number, 'route_description': route_desc,
                    'distance_dir_0_km': round(dist_0,2), 'distance_dir_1_km': round(dist_1,2),
                    'trip_time_0_min': round(trip_time_0,2), 'trip_time_1_min': round(trip_time_1,2),
                    'road_gradient_0_%': st.session_state.current_route.get('grad_0', 0),
                    'road_gradient_1_%': st.session_state.current_route.get('grad_1', 0),
                    'number_of_daily_roundtrips_0': daily_trips,
                    'number_of_daily_roundtrips_1': daily_trips,
                    # Save the USER-DRAWN stops, not the OSRM path
                    'stop_coordinates_array_0': json.dumps(st.session_state.current_route.get('drawn_stops_0', [])),
                    'stop_coordinates_array_1': json.dumps(st.session_state.current_route.get('drawn_stops_1', [])),
                    'base_buses': base_buses
                }
                # ------------------------------------

                st.session_state.manual_routes.append(new_route)
                st.session_state.current_route = {}
                st.rerun()
    with col2:
        st.subheader("B. Draw Route Stops")
        map_col1, map_col2 = st.columns(2)
        with map_col1:
            st.markdown("###### Direction 0 (Outbound)")
            m0 = folium.Map(location=[40.1792, 44.4991], zoom_start=12)
            Draw(export=False, draw_options={'polyline':False,'polygon':False,'rectangle':False,'circle':False,'circlemarker':False}).add_to(m0)
            map_data_0 = st_folium(m0, height=600, width=800, key="map_0")
            if st.button("Process Direction 0"):
                with st.spinner("Processing..."):
                    stops = [f["geometry"]["coordinates"][::-1] for f in map_data_0.get("all_drawings",[]) if f["geometry"]["type"] == "Point"]
                    if len(stops) >= 2:
                        st.session_state.current_route['drawn_stops_0'] = stops
                        st.session_state.current_route['osrm_coords_0'], st.session_state.current_route['dist_0'] = get_osrm_route(stops)
                        st.session_state.current_route['grad_0'] = compute_avg_uphill_gradient(st.session_state.current_route['osrm_coords_0'])
                        st.success(f"Dir 0 Processed: {st.session_state.current_route['dist_0']:.2f} km")
                    else: st.warning("Draw at least 2 stops for Direction 0.")
        with map_col2:
            st.markdown("###### Direction 1 (Return)")
            m1 = folium.Map(location=[40.1792, 44.4991], zoom_start=12)
            Draw(export=False, draw_options={'polyline':False,'polygon':False,'rectangle':False,'circle':False,'circlemarker':False}).add_to(m1)
            map_data_1 = st_folium(m1, height=600, width=800, key="map_1")
            if st.button("Process Direction 1"):
                with st.spinner("Processing..."):
                    stops = [f["geometry"]["coordinates"][::-1] for f in map_data_1.get("all_drawings",[]) if f["geometry"]["type"] == "Point"]
                    if len(stops) >= 2:
                        st.session_state.current_route['drawn_stops_1'] = stops
                        st.session_state.current_route['osrm_coords_1'], st.session_state.current_route['dist_1'] = get_osrm_route(stops)
                        st.session_state.current_route['grad_1'] = compute_avg_uphill_gradient(st.session_state.current_route['osrm_coords_1'])
                        st.success(f"Dir 1 Processed: {st.session_state.current_route['dist_1']:.2f} km")
                    else: st.warning("Draw at least 2 stops for Direction 1.")
        

        
        st.subheader("C. Preview Snapped Route")
        if st.session_state.current_route:
            preview_coords = st.session_state.current_route.get('osrm_coords_0', [])
            if not preview_coords: preview_coords = st.session_state.current_route.get('osrm_coords_1', [])
            if preview_coords:
                map_center = [pd.DataFrame(preview_coords, columns=['lat','lon'])['lat'].mean(), pd.DataFrame(preview_coords, columns=['lat','lon'])['lon'].mean()]
                preview_map = folium.Map(location=map_center, zoom_start=12)
                if st.session_state.current_route.get('osrm_coords_0'):
                    folium.PolyLine(st.session_state.current_route['osrm_coords_0'], color="blue", weight=5, opacity=0.8, tooltip="Direction 0").add_to(preview_map)
                    for stop in st.session_state.current_route.get('drawn_stops_0', []):
                        folium.Marker(stop, icon=folium.Icon(color='blue', icon='circle', prefix='fa')).add_to(preview_map)
                if st.session_state.current_route.get('osrm_coords_1'):
                    folium.PolyLine(st.session_state.current_route['osrm_coords_1'], color="red", weight=5, opacity=0.8, tooltip="Direction 1").add_to(preview_map)
                    for stop in st.session_state.current_route.get('drawn_stops_1', []):
                        folium.Marker(stop, icon=folium.Icon(color='red', icon='circle', prefix='fa')).add_to(preview_map)
                st_folium(preview_map, height=600, width=800, key="preview_map")

    if st.session_state.manual_routes:
        st.markdown("---"); st.header("Step 2: Finalize and Upload Rasters")
        st.dataframe(pd.DataFrame(st.session_state.manual_routes))
        if st.button("Finalize Routes and Proceed", type="primary"):
            with st.spinner("Saving route data..."):
                final_df = pd.DataFrame(st.session_state.manual_routes)
                placeholder_cols = ["daily_passenger_demand", "emissions_impact_score", "equity_score"]
                for col in placeholder_cols: final_df[col] = None
                os.makedirs(RAW_DATA_DIR, exist_ok=True)
                final_df.to_csv(os.path.join(RAW_DATA_DIR, "initial_routes.csv"), index=False)
                st.session_state.selected_routes_str = ",".join(final_df['route_id'].astype(str))
                st.session_state.stage = 'upload_rasters'
                st.rerun()

if st.session_state.stage == 'upload_rasters':
    st.header("Step 3: Upload Geospatial Data (.tif files)")
    pop_files = st.file_uploader("Upload Population Raster(s)", type=['tif', 'tiff'], accept_multiple_files=True)
    bld_files = st.file_uploader("Upload Building Raster(s)", type=['tif', 'tiff'], accept_multiple_files=True)
    st.markdown("---")
    if st.button("Save Rasters and Continue to Scoring", type="primary"):
        if pop_files and bld_files:
            try:
                with st.spinner("Cleaning and preparing raster files..."):
                    clean_directory(INPUT_GEO_DIR)
                    st.write("[INFO] Staging directory cleared.")
                    if len(pop_files) == 1:
                        with open(os.path.join(INPUT_GEO_DIR, "population.tif"),"wb") as f:f.write(pop_files[0].getbuffer())
                    else:
                        for i, file in enumerate(pop_files, 1):
                            with open(os.path.join(INPUT_GEO_DIR, f"population_{i}.tif"),"wb") as f:f.write(file.getbuffer())
                    st.write("[INFO] Population file(s) saved.")
                    if len(bld_files) == 1:
                        with open(os.path.join(INPUT_GEO_DIR, "building.tif"),"wb") as f:f.write(bld_files[0].getbuffer())
                    else:
                        for i, file in enumerate(bld_files, 1):
                            with open(os.path.join(INPUT_GEO_DIR, f"building_{i}.tif"),"wb") as f:f.write(file.getbuffer())
                    st.write("[INFO] Building file(s) saved.")
                st.success("Rasters saved. Proceeding to scoring pipeline...")
                time.sleep(2)
                st.session_state.stage = 'scoring'
                st.rerun()
            except Exception as e:
                st.error(f"[ERROR] A critical error occurred: {e}")
        else:
            st.error("[ERROR] Please upload files for both population and building data.")

if st.session_state.stage == 'scoring':
    st.header("Step 4: Calculating Scores")
    num_routes = len(st.session_state.get('selected_routes_str', '').split(','))
    st.info(f"Running the scoring pipeline on your {num_routes} manually created route(s)...")
    with st.expander("Show Live Log", expanded=True):
        log_placeholder = st.empty()
    orchestrator_cmd = [sys.executable, os.path.join(SRC_DIR, "manual_orchestrator.py")]
    full_output, return_code = run_subprocess_in_thread(orchestrator_cmd, log_placeholder)
    if return_code == 0:
        st.success("Scoring complete! Ready for operational data.")
        #if st.button("Continue to Enter Operational Data", type="primary"):
        st.session_state.stage = 'operational_data'
        st.rerun()
    else:
        st.error("The backend scoring script failed. Please review the log above for errors.")
        if st.button("Start Over"):
            for key in list(st.session_state.keys()): del st.session_state[key]
            st.rerun()

if st.session_state.stage == 'operational_data':
    st.header("Step 5: Enter Operational Data")
    st.subheader("Electric Bus Models")
    bus_data = [{"model_id":1, "model_name": "Proterra ZX5", "fleet_size": 20, "range_km": 400, "energy_use_kWh_per_km": 1.1, "battery_capacity_kWh": 440, "full_charging_time_h": 4, "max_charging_power_kW": 125, "monthly_om_cost_$": 5000}]
    edited_bus_df = st.data_editor(pd.DataFrame(bus_data), num_rows="dynamic", key="bus_editor")
    st.subheader("Charging Depots")
    st.info("For 'location', use a JSON-style list of lists, e.g., [[41.88, -87.62]]")
    depot_data = [{"depot_id": 1, "depot_name": "Central Depot", "number_of_charging_points": 15, "max_power_per_point_kW": 150, "monthly_om_cost_$": 10000, "location": "[[41.88, -87.62]]"}]
    edited_depot_df = st.data_editor(pd.DataFrame(depot_data), num_rows="dynamic", key="depot_editor")
    if st.button("Save and Proceed to Final Step", type="primary"):
        with st.spinner("Saving data..."):
            edited_bus_df.to_csv(os.path.join(RAW_DATA_DIR, "electric_buses.csv"), index=False)
            edited_depot_df.to_csv(os.path.join(RAW_DATA_DIR, "charging_depots.csv"), index=False)
        st.session_state.stage = 'optimize'; st.rerun()

# --- STEP 6: FINAL OPTIMIZATION & DASHBOARD ---
if st.session_state.stage in ['optimize', 'results']:
    st.header("Step 6: Optimization & Results")

    # --- ACTION BUTTON ---
    if st.session_state.stage == 'optimize':
        st.info("🚀 All data is ready. Click below to run the Yeraz AI Optimizer.")
        col1, col2 = st.columns([1, 4])
        with col1:
            if st.button("Run Optimization", type="primary", use_container_width=True):
                st.session_state.stage = 'results'
                st.rerun()

    # --- RESULTS DASHBOARD ---
    if st.session_state.stage == 'results':
        
        # 1. LOADING STATE
        if not st.session_state.get('optimization_results'):
            with st.status("🔄 Optimizing Transit Network...", expanded=True) as status:
                st.write("Initializing heuristic solver...")
                log_placeholder = st.empty()
                
                optimizer_cmd = [
                    sys.executable, os.path.join(SRC_DIR, "mip_heuristic.py"),
                    f"--w_env={w_env}", f"--w_equity={w_equity}", f"--w_ridership={w_ridership}",
                    f"--budget={budget}", f"--energy_cost={energy_cost}", f"--max_routes={max_routes}",
                    f"--w_weather={w_weather}", f"--gamma_safety={gamma_safety}",
                    f"--beta_reserve={beta_reserve}", f"--t_night={t_night}"
                ]
                
                full_output, return_code = run_subprocess_in_thread(optimizer_cmd, log_placeholder)
                
                if return_code == 0:
                    status.update(label="✅ Optimization Complete!", state="complete", expanded=False)
                    # Load Results
                    results_path = os.path.join(TEMP_DIR, "results.json")
                    time.sleep(1) # Small buffer for file write
                    if os.path.exists(results_path):
                        with open(results_path, 'r', encoding='utf-8') as f:
                            st.session_state.optimization_results = json.load(f)
                        st.rerun() # Refresh to show dashboard
                    else:
                        st.error("Results file not found.")
                else:
                    status.update(label="❌ Optimization Failed", state="error")
                    st.error("The optimization process encountered an error.")
                    st.stop()

        # 2. THE DASHBOARD VIEW
        if st.session_state.get('optimization_results'):
            results = st.session_state.optimization_results
            
            if not results.get("has_solution"):
                st.warning("⚠️ No feasible solution found under current constraints. Try increasing the budget.")
            else:
                # --- A. KPI ROW (The "Executive Summary") ---
                st.markdown("### 🎯 Executive Summary")
                kpi1, kpi2, kpi3, kpi4 = st.columns(4)
                
                # Attempt to extract numbers safely (Defaults to "N/A" if keys miss)
                total_cost = results.get("total_cost", 0)
                total_buses = results.get("total_buses", 0)
                routes_count = len(results.get("selected_routes", []))
                # If your backend sends these specific keys, use them. Otherwise, calculate or use defaults.
                co2_saved = results.get("total_emissions_reduction", "High") 
                equity_score = results.get("equity_impact", "Medium")

                kpi1.metric("Routes Electrified", f"{routes_count}", delta=f"Limit: {max_routes}")
                kpi2.metric("Budget Utilized", f"${total_cost:,.0f}", delta=f"of ${budget:,.0f}")
                kpi3.metric("Electric Buses Deployed", f"{total_buses}", delta="Active Fleet")
                kpi4.metric("Equity Impact", f"{equity_score}")

                st.markdown("---")

                # --- B. DETAILED CONTENT TABS ---
                tab_table, tab_maps, tab_notes = st.tabs(["📊 Selected Routes", "🗺️ Network Maps", "📝 Technical Notes"])

               # --- IN app_manual.py (Inside the 'results' block) ---

                with tab_table:
                    # Check if we have structured data
                    if "selected_routes" in results and isinstance(results["selected_routes"], list):
                        df_res = pd.DataFrame(results["selected_routes"])
                        
                        # RENAME columns for the display (Hide the raw score)
                        df_display = df_res.rename(columns={
                            "route_id": "Route",
                            "description": "Description",
                            "ridership": "Daily Passengers",
                            "cost": "Monthly Cost"
                        })
                        
                        # Configure the table
                        st.dataframe(
                            df_display, 
                            use_container_width=True, 
                            hide_index=True,
                            column_order=["Route", "Description", "Daily Passengers", "emissions_score", "equity_score", "Monthly Cost"],
                            column_config={
                                "Monthly Cost": st.column_config.NumberColumn(format="$%d"),
                                "Daily Passengers": st.column_config.NumberColumn(format="%d"),
                                
                                # HIDE raw CO2 number, show a BAR
                                "emissions_score": st.column_config.ProgressColumn(
                                    "Eco-Impact Rating",
                                    help="Relative score: Higher bar means replacing this route removes more pollution.",
                                    min_value=0,
                                    max_value=100,
                                    format=" " # This HIDES the number text inside the bar
                                ),
                                "equity_score": st.column_config.ProgressColumn(
                                    "Equity Rating",
                                    min_value=0,
                                    max_value=100,
                                    format=" " # Hides the number
                                ),
                            }
                        )
                    else:
                        st.text(results.get("summary_table", "No table data."))
                    
                    # Download Button
                    st.download_button(
                        label="📥 Download Full Report (JSON)",
                        data=json.dumps(results, indent=2),
                        file_name="yeraz_optimization_results.json",
                        mime="application/json"
                    )

                with tab_maps:
                    map_paths = results.get("map_paths", [])
                    if map_paths:
                        cols = st.columns(2) # Grid Layout
                        for i, map_path in enumerate(map_paths):
                            map_name = os.path.basename(map_path).replace('.html', '').replace('_', ' ').title()
                            with cols[i % 2]: # Alternating columns
                                st.markdown(f"**{map_name}**")
                                try:
                                    with open(map_path, 'r', encoding='utf-8') as f:
                                        html_content = f.read()
                                    components.html(html_content, height=400, scrolling=False)
                                except Exception as e:
                                    st.error(f"Error loading map: {e}")
                    else:
                        st.info("No route maps generated.")

                with tab_notes:
                    st.markdown("#### Solver Logs & Warnings")
                    st.info(results.get("summary_notes", "No operational notes provided."))
                    
                    with st.expander("View Raw Optimization Logic"):
                        st.json(results)

        # Restart Button
        st.markdown("---")
        if st.button("🔄 Start New Simulation", type="secondary"):
            for key in list(st.session_state.keys()): del st.session_state[key]
            st.rerun()