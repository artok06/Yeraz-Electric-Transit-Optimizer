# app.py
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
import zipfile
import shutil
import streamlit.components.v1 as components
import webbrowser
from html import escape

# --- Configuration ---
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SRC_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
INPUT_GTFS_DIR = os.path.join(DATA_DIR, "input_gtfs")
INPUT_GEO_DIR = os.path.join(DATA_DIR, "input_geospatial")
FILTERED_DIR = os.path.join(DATA_DIR, "filtered_routes")
SOLUTION_DIR = os.path.join(PROJECT_ROOT, "solution")
TEMP_DIR = os.path.join(PROJECT_ROOT, "temp") # Add temp directory

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
st.set_page_config(layout="wide", page_title="Yeraz Optimizer")
st.title("Yeraz Electric Transit Optimizer")

# --- Session State Management ---
if 'stage' not in st.session_state: st.session_state.stage = 'upload'
if 'files_saved' not in st.session_state: st.session_state.files_saved = False


# --- Sidebar ---
st.sidebar.title("Optimization Parameters")
st.sidebar.subheader("1. Objective Weights")
w_env = st.sidebar.slider("Environmental Weight", 0.0, 1.0, 0.0, 0.05)
w_equity = st.sidebar.slider("Equity Weight", 0.0, 1.0, 0.5, 0.05)
w_ridership = st.sidebar.slider("Ridership Weight", 0.0, 1.0, 0.5, 0.05)
st.sidebar.subheader("2. Financial Constraints")
budget = st.sidebar.number_input("Monthly Budget ($)", min_value=100000, value=500000)
energy_cost = st.sidebar.number_input("Energy Cost ($/kWh)", min_value=0.01, value=0.11, format="%.2f")
st.sidebar.subheader("3. Operational Constraints")
max_routes = st.sidebar.number_input("Max Routes to Electrify", min_value=1, value=25, step=1)
w_weather = st.sidebar.number_input("Weather Factor", min_value=1.0, value=1.10, format="%.2f")
gamma_safety = st.sidebar.number_input("Battery Safety Factor", min_value=1.0, value=1.15, format="%.2f")
beta_reserve = st.sidebar.number_input("Reserve Fleet Factor", min_value=0.0, value=0.12, format="%.2f")
t_night = st.sidebar.number_input("Night Charging Time (minutes)", min_value=60, value=480, step=30)

# --- Main App Body ---

# Stage 0: File Upload
if st.session_state.stage == 'upload':
    st.header("Step 1: Upload Data")
    gtfs_file = st.file_uploader("Upload GTFS Zip File", type=['zip'])
    pop_files = st.file_uploader("Upload Population Raster(s) (.tif)", type=['tif', 'tiff'], accept_multiple_files=True)
    bld_files = st.file_uploader("Upload Building Raster(s) (.tif)", type=['tif', 'tiff'], accept_multiple_files=True)
    st.markdown("---")

    if not st.session_state.files_saved:
        if st.button("Confirm and Save Uploaded Files"):
            if gtfs_file and pop_files and bld_files:
                try:
                    with st.spinner("Cleaning and preparing files..."):
                        clean_directory(INPUT_GTFS_DIR); clean_directory(INPUT_GEO_DIR)
                        st.write("[INFO] Staging directories cleared.")
                        gtfs_save_path = os.path.join(INPUT_GTFS_DIR, gtfs_file.name)
                        with open(gtfs_save_path, "wb") as f: f.write(gtfs_file.getbuffer())
                        with zipfile.ZipFile(gtfs_save_path, 'r') as z: z.extractall(INPUT_GTFS_DIR)
                        os.remove(gtfs_save_path)
                        st.write("[INFO] GTFS file saved and unzipped.")
                        if len(pop_files) == 1:
                            with open(os.path.join(INPUT_GEO_DIR, "population.tif"), "wb") as f: f.write(pop_files[0].getbuffer())
                        else:
                            for i, file in enumerate(pop_files, 1):
                                with open(os.path.join(INPUT_GEO_DIR, f"population_{i}.tif"), "wb") as f: f.write(file.getbuffer())
                        st.write("[INFO] Population file(s) saved.")
                        if len(bld_files) == 1:
                            with open(os.path.join(INPUT_GEO_DIR, "building.tif"), "wb") as f: f.write(bld_files[0].getbuffer())
                        else:
                            for i, file in enumerate(bld_files, 1):
                                with open(os.path.join(INPUT_GEO_DIR, f"building_{i}.tif"), "wb") as f: f.write(file.getbuffer())
                        st.write("[INFO] Building file(s) saved.")
                    st.session_state.files_saved = True
                    st.rerun()
                except Exception as e:
                    st.error(f"[ERROR] A critical error occurred: {e}")
            else:
                st.error("[ERROR] Please upload all required files before saving.")

    if st.session_state.files_saved:
        st.success("[SUCCESS] All input files are ready.")
        if st.button("Filter bus routes from given GTFS data", type="primary"):
            st.session_state.stage = 'initial_processing'
            st.rerun()

# Stage 1: Run Initial GTFS Processing
if st.session_state.stage == 'initial_processing':
    st.header("Step 2: Initial Route Filtering")
    st.info("Running the first phase of the data pipeline to identify all available bus routes...")
    with st.expander("Show Live Log", expanded=True):
        log_placeholder = st.empty()
    orchestrator_cmd = [sys.executable, os.path.join(SRC_DIR, "gtfs_orchestrator.py"), "--phase=initial"]
    full_output, return_code = run_subprocess_in_thread(orchestrator_cmd, log_placeholder)
    if return_code == 0:
        st.success("Initial processing complete!")
        st.session_state.stage = 'route_selection'
        st.rerun()
    else:
        st.error("The backend data processing script failed. Please review the log above for errors.")
        if st.button("Start Over"):
            for key in list(st.session_state.keys()): del st.session_state[key]
            st.rerun()

# Stage 2: Interactive Route Selection (MODIFIED)
if st.session_state.stage == 'route_selection':
    st.header("Step 3: Select Routes for Analysis")
    try:
        routes_to_select_df = pd.read_csv(os.path.join(FILTERED_DIR, "filtered_routes.csv"))
        routes_to_select_df['route_id'] = routes_to_select_df['route_id'].astype(str)
        route_ids = routes_to_select_df['route_id'].tolist()
    except FileNotFoundError:
        st.error("[ERROR] Could not find 'filtered_routes.csv'. Please go back to Step 1 and re-run.")
    else:
        def sync_all_routes_toggle():
            is_checked = st.session_state.select_all_toggle
            for r_id in route_ids: st.session_state[f"route_{r_id}"] = is_checked
        def sync_individual_routes():
            all_checked = all(st.session_state[f"route_{r_id}"] for r_id in route_ids)
            st.session_state.select_all_toggle = all_checked
        
        for r_id in route_ids:
            if f"route_{r_id}" not in st.session_state: st.session_state[f"route_{r_id}"] = True
        if 'select_all_toggle' not in st.session_state: st.session_state.select_all_toggle = True
        
        st.checkbox("Select All / Deselect All", key='select_all_toggle', on_change=sync_all_routes_toggle)
        st.markdown("---")
        
        # --- MODIFIED: Removed columns for top-to-bottom layout ---
        for i, row in routes_to_select_df.iterrows():
            route_id = row['route_id']
            route_name = row.get('route_long_name') or row.get('route_short_name') or route_id
            label = f"{route_id} - {route_name}"
            st.checkbox(label, key=f"route_{route_id}", on_change=sync_individual_routes)

        st.markdown("---")
        if st.button("Confirm Selection and Run Scoring", type="primary"):
            selected_routes = [r_id for r_id in route_ids if st.session_state[f"route_{r_id}"]]
            if not selected_routes:
                st.warning("Please select at least one route.")
            else:
                # ... (rest of the logic is unchanged)
                st.session_state.selected_routes_str = ",".join(selected_routes)
                st.session_state.stage = 'scoring'
                st.rerun()

# Stage 3: Run Scoring on Selected Routes
if st.session_state.stage == 'scoring':
    st.header("Step 4: Calculating Scores")
    st.info(f"Running the main scoring pipeline for {len(st.session_state.selected_routes_str.split(','))} selected route(s)...")
    with st.expander("Show Live Log", expanded=True):
        log_placeholder = st.empty()
    orchestrator_cmd = [sys.executable, os.path.join(SRC_DIR, "gtfs_orchestrator.py"), "--phase=scoring", f"--routes={st.session_state.selected_routes_str}"]
    full_output, return_code = run_subprocess_in_thread(orchestrator_cmd, log_placeholder)
    if return_code == 0:
        st.success("Scoring complete! Ready for operational data.")
        st.session_state.stage = 'operational_data'
        st.rerun()
    else:
        st.error("The backend scoring script failed. Please review the log above for errors.")
        if st.button("Start Over"):
            for key in list(st.session_state.keys()): del st.session_state[key]
            st.rerun()

# Stage 4: Input Operational Data (MODIFIED)
if st.session_state.stage == 'operational_data':
    st.header("Step 5: Enter Operational Data")
    
    st.subheader("Electric Bus Models")
    bus_data = [{"model_id":1, "model_name": "Proterra ZX5", "fleet_size": 20, "range_km": 400, "energy_use_kWh_per_km": 1.1, "battery_capacity_kWh": 440, "full_charging_time_h": 4, "max_charging_power_kW": 125, "monthly_om_cost_$": 5000}]
    edited_bus_df = st.data_editor(pd.DataFrame(bus_data), num_rows="dynamic", key="bus_editor")

    st.subheader("Charging Depots")
    
    # --- THIS SECTION IS NOW CORRECTED ---
    st.info("For the 'location' column, please use a JSON-style list of lists, e.g., [[lat, lon]] -> [[41.88, -87.62]]")
    depot_data = [
        {
            "depot_id": 1,
            "depot_name": "Central Depot", 
            "number_of_charging_points": 15, 
            "max_power_per_point_kW": 150, 
            "monthly_om_cost_$": 10000,
            "location": "[[41.88, -87.62]]" # Added example location
        }
    ]
    edited_depot_df = st.data_editor(pd.DataFrame(depot_data), num_rows="dynamic", key="depot_editor")
    # ------------------------------------

    if st.button("Save and Proceed to Final Step", type="primary"):
        with st.spinner("Saving data..."):
            edited_bus_df.to_csv(os.path.join(DATA_DIR, "raw", "electric_buses.csv"), index=False)
            edited_depot_df.to_csv(os.path.join(DATA_DIR, "raw", "charging_depots.csv"), index=False)
        st.session_state.stage = 'optimize'
        st.rerun()
    
# --- Stage 5 & 6: Optimize and Display Results ---
if st.session_state.stage in ['optimize', 'results']:
    st.header("Step 6: Final Optimization")
    
    if st.session_state.stage == 'optimize':
        st.info("All data is ready. Click the button to run the optimization model.")
        if st.button("Recommend Routes", type="primary"):
            st.session_state.stage = 'results'
            st.rerun()
    
    if st.session_state.stage == 'results':
        with st.spinner("Running optimization model... please wait."):
            with st.expander("Show Live Log", expanded=True):
                log_placeholder = st.empty()
            
            optimizer_cmd = [
                sys.executable, os.path.join(SRC_DIR, "mip_heuristic.py"),
                "--w_env", str(w_env),
                "--w_equity", str(w_equity),
                "--w_ridership", str(w_ridership),
                "--budget", str(budget),
                "--energy_cost", str(energy_cost),
                "--max_routes", str(max_routes),
                "--w_weather", str(w_weather),
                "--gamma_safety", str(gamma_safety),
                "--beta_reserve", str(beta_reserve),
                "--t_night", str(t_night)
            ]
            full_output, return_code = run_subprocess_in_thread(optimizer_cmd, log_placeholder)

            if return_code == 0:
                results_path = os.path.join(TEMP_DIR, "results.json")
                
                # Wait-and-retry loop to handle file system latency (e.g., with OneDrive)
                max_wait_time, start_time, file_found = 5, time.time(), False
                while time.time() - start_time < max_wait_time:
                    if os.path.exists(results_path):
                        file_found = True
                        break
                    time.sleep(0.2)
                    
                if file_found:
                    try:
                        with open(results_path, 'r', encoding='utf-8') as f:
                            st.session_state.optimization_results = json.load(f)
                    except Exception as e:
                        st.error(f"[ERROR] Could not read or parse the results file. Error: {repr(e)}")
                        st.session_state.optimization_results = None
                else:
                    st.error(f"[ERROR] Optimization script finished, but the results file was not found.")
                    st.session_state.optimization_results = None
            else:
                st.error("The optimization script failed. Please review the log for errors.")
                st.session_state.optimization_results = None

        if st.session_state.optimization_results and st.session_state.optimization_results.get("has_solution"):
            results = st.session_state.optimization_results
            st.success("Optimization Complete!")
            
            st.subheader("Selected Route Summary")
            st.code(results.get("summary_table", "No summary table provided."), language=None)
            
            st.subheader("Notes")
            st.markdown(results.get("summary_notes", "No notes provided."))
            
            st.subheader("Interactive Route Maps")
            map_paths = results.get("map_paths", [])
            if map_paths:
                for map_path in map_paths:
                    map_name = os.path.basename(map_path).replace('.html', '').replace('_', ' ').title()
                    with st.expander(f"Show Map for {map_name}"):
                        try:
                            with open(map_path, 'r', encoding='utf-8') as f:
                                html_content = f.read()
                            components.html(html_content, height=500, scrolling=True)
                        except Exception as e:
                            st.error(f"Could not display map. Error: {e}")
            else:
                st.write("No map files were generated.")
        else:
            st.error("[ERROR] Optimization failed or found no solution with the given constraints.")

        if st.button("Start Over"):
            # Clear session state for a clean restart
            for key in list(st.session_state.keys()): del st.session_state[key]
            st.rerun()

