# Yeraz Electric Transit Optimizer

## Overview

The Yeraz Electric Transit Optimizer is a comprehensive, data-driven decision support tool designed to help city planners strategically electrify their bus fleet. It empowers cities to make objective decisions that balance environmental impact, social equity, and financial & operational constraints.

IMPORTANT LICENSING NOTICE 

The original code (v. 1.0) was licensed under the Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0) license.
Starting from 13.12.25, the code in this repository is governed by the GNU Affero General Public License v3.0 (AGPL-3.0).

A key innovation of this tool is its **dual-workflow design**, making it scalable for:
1.  **Data-Rich Cities:** A full pipeline that processes GTFS and geospatial data (`app.py`).
2.  **Data-Scarce Cities:** A fully interactive tool for manually creating and analyzing routes where no GTFS data exists (`app_manual.py`).

## Features

- **Multi-Criteria Scoring:** Routes are scored on three core pillars:
    - **Ridership Potential:** Predicted using a machine learning model.
    - **Emissions Impact:** A physics-informed score based on route length, gradient, and service frequency.
    - **Equity Score:** Identifies routes that best serve the community by analyzing population and building density within a walkable radius of every stop.
- **Configurable Optimization:** Planners can adjust weights and constraints (budget, etc.) and use their own fleet and depot data to align the tool's recommendations with their city's specific goals.
- **Interactive Outputs:** The tool generates a clear summary table and detailed, interactive per-route maps showing the recommended solution directly in the user interface.

## How to Run

### 1. Setup
Clone this repository and navigate into the project directory. It is recommended to use a Python virtual environment.
```bash
git clone [your-repo-url]
cd Yeraz-Electric-Transit-Optimizer
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 2. Install Dependencies
Install all required libraries from the `requirements.txt` file.
```bash
pip install -r requirements.txt
```

### 3. Prepare the ML Model
The application uses a pre-trained model. To generate the necessary model file (`ridership.joblib`), you first need to run the training script (`ridership_model_trainer.py`).
*(Note: This requires a training dataset like `cta_initial_routes.csv` and `cta_ridership.csv` in the `data/training` folder).*
```bash
python src/ridership_model_training.py
```

### 4. Prepare Data
- **GTFS Data:** Can be found for many cities at the [Mobility Database](https://mobilitydatabase.org/).
- **Geospatial Data:** Population (GHS-POP) and building (GHS-BUILT-S) rasters can be downloaded from the [Copernicus Global Human Settlement Layer](https://human-settlement.emergency.copernicus.eu/download.php).

### 5. Run the Application
You can now run either of the two applications.

**For the main GTFS-based application:**
```bash
python -m streamlit run src/app.py
```

**For the manual route creator (for cities without GTFS):**
```bash
python -m streamlit run src/app_manual.py
```
