# -*- coding: utf-8 -*-
"""
Created on Sat Aug 16 16:03:09 2025

Yeraz Interactive Route Creator

This script provides a GUI to manually draw bus routes on a map
and saves them to the initial_routes.csv file.

@author: artok
"""
import tkinter as tk
from tkinter import messagebox, filedialog
import os
import pandas as pd
import folium
from folium.plugins import Draw
import webbrowser
import math
import json
import requests
import polyline
import time

# --- Elevation API Functions ---
BATCH_SIZE = 100
def get_elevations(coords):
    if not coords:
        return []
    
    elevations = []
    
    # Split coordinates into batches
    for i in range(0, len(coords), BATCH_SIZE):
        batch = coords[i:i + BATCH_SIZE]
        locations = "|".join([f"{c[0]},{c[1]}" for c in batch])
        url = f"https://api.open-elevation.com/api/v1/lookup?locations={locations}"
        
        try:
            response = requests.get(url)
            response.raise_for_status()
            results = response.json().get('results', [])
            elevations.extend([res['elevation'] for res in results])
            time.sleep(0.2) # Pause to respect API rate limits
        except Exception as e:
            messagebox.showerror("Elevation API Error", f"Failed to get elevations: {e}")
            return [0] * len(coords)
            
    return elevations

def compute_avg_uphill_gradient(coords):
    if len(coords) < 2:
        return 0
    elevations = get_elevations(coords)
    if not elevations:
        return 0

    uphill_gain = 0
    total_dist = 0
    
    for i in range(len(coords) - 1):
        dist_segment = haversine_distance(coords[i][0], coords[i][1], coords[i+1][0], coords[i+1][1])
        elev_diff = elevations[i+1] - elevations[i]
        
        total_dist += dist_segment
        if elev_diff > 0:
            uphill_gain += elev_diff
            
    if total_dist > 0:
        avg_uphill_gradient = (uphill_gain / (total_dist * 1000)) * 100
        return round(avg_uphill_gradient, 3)
    else:
        return 0

# --- General Helper Functions ---
def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371  # Radius of Earth in km
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)
    dlon = lon2_rad - lon1_rad
    dlat = lat2_rad - lat1_rad
    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def get_osrm_route(coords):
    if len(coords) < 2:
        return [], 0
    
    osrm_url = "http://router.project-osrm.org/route/v1/driving/"
    
    coordinates_str = ";".join([f"{c[1]},{c[0]}" for c in coords])
    url = f"{osrm_url}{coordinates_str}?geometries=polyline&overview=full"
    
    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            encoded_polyline = data['routes'][0]['geometry']
            distance_km = data['routes'][0]['distance'] / 1000
            decoded_coords = polyline.decode(encoded_polyline)
            return decoded_coords, distance_km
        else:
            return [], 0
    except Exception:
        return [], 0

class RouteCreatorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Yeraz Interactive Route Creator")
        self.root.geometry("600x600")
        self.root.resizable(False, False)

        self.project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.data_dir = os.path.join(self.project_root, "data", "raw")
        self.temp_dir = os.path.join(self.project_root, "data", "temp")
        os.makedirs(self.temp_dir, exist_ok=True)
        
        self.route_id_var = tk.StringVar()
        self.route_number_var = tk.StringVar()
        self.route_description_var = tk.StringVar()
        self.min_headway_var = tk.DoubleVar()
        self.avg_speed_var = tk.DoubleVar(value=20)
        self.service_hours_var = tk.DoubleVar(value=18)
        
        self.stops_dir_0 = []
        self.stops_dir_1 = []
        self.osrm_route_0 = []
        self.osrm_route_1 = []
        self.length_dir_0 = 0
        self.length_dir_1 = 0
        self.trip_time_0_min = 0
        self.trip_time_1_min = 0
        self.road_gradient_0 = 0
        self.road_gradient_1 = 0

        self._create_widgets()
    
    def _create_widgets(self):
        main_frame = tk.Frame(self.root, padx=20, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        main_frame.columnconfigure(1, weight=1)

        tk.Label(main_frame, text="Route Creator Tool", font=("Segoe UI", 16, "bold")).grid(row=0, column=0, columnspan=2, pady=(0, 10))
        
        row_idx = 1
        tk.Label(main_frame, text="Route ID:").grid(row=row_idx, column=0, sticky="w", padx=(5, 5))
        tk.Entry(main_frame, textvariable=self.route_id_var).grid(row=row_idx, column=1, sticky="ew", padx=(5, 5), pady=2)
        row_idx += 1

        tk.Label(main_frame, text="Route Number:").grid(row=row_idx, column=0, sticky="w", padx=(5, 5))
        tk.Entry(main_frame, textvariable=self.route_number_var).grid(row=row_idx, column=1, sticky="ew", padx=(5, 5), pady=2)
        row_idx += 1
        
        tk.Label(main_frame, text="Description:").grid(row=row_idx, column=0, sticky="w", padx=(5, 5))
        tk.Entry(main_frame, textvariable=self.route_description_var).grid(row=row_idx, column=1, sticky="ew", padx=(5, 5), pady=2)
        row_idx += 1
        
        tk.Label(main_frame, text="Min. Headway (min):").grid(row=row_idx, column=0, sticky="w", padx=(5, 5))
        tk.Entry(main_frame, textvariable=self.min_headway_var).grid(row=row_idx, column=1, sticky="ew", padx=(5, 5), pady=2)
        row_idx += 1
        
        tk.Label(main_frame, text="Avg Speed (km/h):").grid(row=row_idx, column=0, sticky="w", padx=(5, 5))
        tk.Entry(main_frame, textvariable=self.avg_speed_var).grid(row=row_idx, column=1, sticky="ew", padx=(5, 5), pady=2)
        row_idx += 1
        
        tk.Label(main_frame, text="Service Hours:").grid(row=row_idx, column=0, sticky="w", padx=(5, 5))
        tk.Entry(main_frame, textvariable=self.service_hours_var).grid(row=row_idx, column=1, sticky="ew", padx=(5, 5), pady=2)
        row_idx += 1

        tk.Label(main_frame, text="Draw Stops on Map:", font=("Segoe UI", 12, "bold")).grid(row=row_idx, column=0, columnspan=2, pady=(20, 5))
        row_idx += 1
        
        self.draw_btn_0 = tk.Button(main_frame, text="Draw Direction 0", command=lambda: self._draw_route(0))
        self.draw_btn_0.grid(row=row_idx, column=0, columnspan=2, pady=5)
        row_idx += 1
        
        self.draw_btn_1 = tk.Button(main_frame, text="Draw Direction 1", command=lambda: self._draw_route(1))
        self.draw_btn_1.grid(row=row_idx, column=0, columnspan=2, pady=5)
        row_idx += 1
        
        tk.Label(main_frame, text="Save Route:").grid(row=row_idx, column=0, columnspan=2, pady=(20, 5))
        row_idx += 1
        
        self.save_button = tk.Button(main_frame, text="Save Route to CSV", command=self._save_route)
        self.save_button.grid(row=row_idx, column=0, columnspan=2, pady=5)
        row_idx += 1
        
        self.reset_button = tk.Button(main_frame, text="Start New Route", command=self._reset_form)
        self.reset_button.grid(row=row_idx, column=0, columnspan=2, pady=5)
        
    def _draw_route(self, direction):
        map_html_path = os.path.join(self.temp_dir, "interactive_map.html")
        geojson_path = os.path.join(self.temp_dir, "drawn_routes.geojson")
        if os.path.exists(geojson_path):
            os.remove(geojson_path)

        m = folium.Map(location=[48.2082, 16.3738], zoom_start=12, tiles="CartoDB Dark_Matter")
        Draw(export=True, filename='drawn_routes.geojson', draw_options={'marker':True, 'polyline':False, 'polygon':False, 'rectangle':False, 'circle':False, 'circlemarker':False}).add_to(m)
        m.save(map_html_path)

        webbrowser.open(map_html_path)

        messagebox.showinfo("Draw Stops", f"Map opened in browser. Please use the marker tool to place stops for Direction {direction}. Click the save button on the map (the down arrow icon) to download the GeoJSON file. Click OK here when you are ready to select the downloaded file.", parent=self.root)

        time.sleep(1) # Wait for browser to handle download

        geojson_path = filedialog.askopenfilename(title="Select GeoJSON file", filetypes=[("GeoJSON files", "*.geojson")], parent=self.root)
        
        if geojson_path:
            try:
                with open(geojson_path, 'r') as f:
                    geojson_data = json.load(f)
                
                stops_coords_latlon = []
                if geojson_data['features']:
                    for feature in geojson_data['features']:
                        if feature['geometry']['type'] == 'Point':
                            coords = feature['geometry']['coordinates']
                            stops_coords_latlon.append([coords[1], coords[0]])
                
                osrm_coords, osrm_length = get_osrm_route(stops_coords_latlon)
                
                if not osrm_coords:
                     messagebox.showerror("OSRM Error", "Could not get an OSRM route for the given stops.", parent=self.root)
                     return

                # Calculate gradient for the OSRM route
                avg_uphill_gradient = compute_avg_uphill_gradient(osrm_coords)

                preview_map_html_path = os.path.join(self.temp_dir, "osrm_preview.html")
                preview_m = folium.Map(location=stops_coords_latlon[0], zoom_start=12, tiles="CartoDB Dark_Matter")
                
                if direction == 0:
                    self.stops_dir_0 = stops_coords_latlon
                    self.osrm_route_0 = osrm_coords
                    self.length_dir_0 = osrm_length
                    self.road_gradient_0 = avg_uphill_gradient
                    folium.PolyLine(self.osrm_route_0, color='blue', weight=5, tooltip=f"Direction 0: {self.length_dir_0:.2f} km").add_to(preview_m)
                    for stop in self.stops_dir_0:
                        folium.Marker(stop, icon=folium.Icon(color='green', icon='bus')).add_to(preview_m)
                    messagebox.showinfo("Stops Saved", f"Direction 0 stops saved: {len(self.stops_dir_0)} stops. OSRM route length: {self.length_dir_0:.2f} km. Average uphill gradient: {self.road_gradient_0}%.")
                else:
                    self.stops_dir_1 = stops_coords_latlon
                    self.osrm_route_1 = osrm_coords
                    self.length_dir_1 = osrm_length
                    self.road_gradient_1 = avg_uphill_gradient
                    folium.PolyLine(self.osrm_route_1, color='red', weight=5, tooltip=f"Direction 1: {self.length_dir_1:.2f} km").add_to(preview_m)
                    for stop in self.stops_dir_1:
                        folium.Marker(stop, icon=folium.Icon(color='red', icon='bus')).add_to(preview_m)
                    messagebox.showinfo("Stops Saved", f"Direction 1 stops saved: {len(self.stops_dir_1)} stops. OSRM route length: {self.length_dir_1:.2f} km. Average uphill gradient: {self.road_gradient_1}%.")
                
                preview_m.save(preview_map_html_path)
                webbrowser.open(preview_map_html_path)

            except Exception as e:
                messagebox.showerror("Error", f"Could not process GeoJSON file. Reason: {e}", parent=self.root)
        else:
            messagebox.showerror("Error", "No GeoJSON file selected.")

    def _save_route(self):
        try:
            route_id = self.route_id_var.get().strip()
            route_number = self.route_number_var.get().strip()
            route_description = self.route_description_var.get().strip()
            min_headway_min = self.min_headway_var.get()
            avg_speed_kmh = self.avg_speed_var.get()
            service_hours = self.service_hours_var.get()
            
            if not all([route_id, route_number, route_description]) or not self.osrm_route_0:
                messagebox.showerror("Validation Error", "Please provide all route details and draw at least Direction 0 stops.")
                return

            headway_h = min_headway_min / 60 if min_headway_min > 0 else 1
            base_buses = math.ceil((self.length_dir_0 + self.length_dir_1) / (avg_speed_kmh * headway_h))
            if base_buses == 0: base_buses = 1

            total_service_mins = service_hours * 60
            round_trip_time_min = (self.length_dir_0 + self.length_dir_1) / avg_speed_kmh * 60
            number_of_daily_roundtrips = math.ceil(total_service_mins / min_headway_min) if round_trip_time_min > 0 else 1
            if number_of_daily_roundtrips == 0: number_of_daily_roundtrips = 1

            self.trip_time_0_min = round(self.length_dir_0 / avg_speed_kmh * 60, 2)
            self.trip_time_1_min = round(self.length_dir_1 / avg_speed_kmh * 60, 2)
            
            osrm_route_0_list = [list(c) for c in self.osrm_route_0]
            osrm_route_1_list = [list(c) for c in self.osrm_route_1]
            
            new_route_data = {
                'route_id': route_id,
                'route_number': route_number,
                'route_description': route_description,
                'distance_dir_0_km': round(self.length_dir_0, 2),
                'distance_dir_1_km': round(self.length_dir_1, 2),
                'base_buses': base_buses,
                'number_of_daily_roundtrips_0': number_of_daily_roundtrips,
                'number_of_daily_roundtrips_1': number_of_daily_roundtrips,
                'stop_coordinates_array_0': str(osrm_route_0_list),
                'stop_coordinates_array_1': str(osrm_route_1_list),
                'reduction_CO2_kg': None,
                'passenger_daily_demand': None,
                'equity_score': None,
                'min_headway_min': min_headway_min,
                'avg_speed_kmh': avg_speed_kmh,
                'service_hours': service_hours,
                'trip_time_0_min': self.trip_time_0_min,
                'trip_time_1_min': self.trip_time_1_min,
                'road_gradient_0_%': self.road_gradient_0,
                'road_gradient_1_%': self.road_gradient_1,
            }
            
            csv_path = os.path.join(self.data_dir, "initial_routes.csv")
            
            df = pd.DataFrame([new_route_data])
            if os.path.exists(csv_path):
                df_existing = pd.read_csv(csv_path)
                df = pd.concat([df_existing, df], ignore_index=True)
            
            df.to_csv(csv_path, index=False)
            
            messagebox.showinfo("Success", f"Route {route_number} saved successfully!", parent=self.root)
            self._reset_form()

        except Exception as e:
            messagebox.showerror("Error", f"An error occurred while saving: {e}", parent=self.root)

    def _reset_form(self):
        self.route_id_var.set("")
        self.route_number_var.set("")
        self.route_description_var.set("")
        self.min_headway_var.set(0)
        self.avg_speed_var.set(20)
        self.service_hours_var.set(18)
        self.stops_dir_0 = []
        self.stops_dir_1 = []
        self.osrm_route_0 = []
        self.osrm_route_1 = []
        self.length_dir_0 = 0
        self.length_dir_1 = 0
        self.trip_time_0_min = 0
        self.trip_time_1_min = 0
        self.road_gradient_0 = 0
        self.road_gradient_1 = 0

def main():
    root = tk.Tk()
    app = RouteCreatorApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()