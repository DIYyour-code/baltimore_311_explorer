"""
fetch_311.py

Pulls Baltimore City 311 service requests from the Open Baltimore ArcGIS API.
Data is split by calendar year, each with its own FeatureServer endpoint.

URL pattern (confirmed from Open Baltimore API Explorer):
  https://services1.arcgis.com/UWYHeuuJISiGmgXx/arcgis/rest/services/
    311_Customer_Service_Requests_YYYY/FeatureServer/0

Org ID: UWYHeuuJISiGmgXx
Datasets available: 2010–2026 (we default to 2020–2026 for chronic analysis)
"""

import requests
import pandas as pd
from datetime import datetime
from tqdm import tqdm
import os
import time

# --- Configuration ---

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', '311_requests.csv')

ARCGIS_ORG = "UWYHeuuJISiGmgXx"
BASE_URL = f"https://services1.arcgis.com/{ARCGIS_ORG}/arcgis/rest/services"

def endpoint_for_year(year):
    return f"{BASE_URL}/311_Customer_Service_Requests_{year}/FeatureServer/0"

# Years to fetch. 2020–present gives ~5 years for solid chronic analysis.
# Add earlier years (e.g. 2018, 2019) if you want longer history — just slower.
YEARS_TO_FETCH = [2026, 2025, 2024, 2023, 2022, 2021, 2020]

# ArcGIS max records per page
PAGE_SIZE = 2000

# Infrastructure-related service request types (SQL LIKE matching on SRType field)
INFRASTRUCTURE_KEYWORDS = [
    "Pothole",
    "Street Light",
    "Streetlight",
    "Sidewalk",
    "Alley",
    "Cave-In",
    "Sinkhole",
    "Water Main",
    "Storm Drain",
    "Catch Basin",
    "Bridge",
    "Curb",
    "Street - Damaged",
    "Street Light Out",
]


def build_where_clause():
    conditions = [f"SRType LIKE '%{kw}%'" for kw in INFRASTRUCTURE_KEYWORDS]
    return "(" + " OR ".join(conditions) + ")"


def get_field_names(year):
    """Fetch the field schema for a year's endpoint."""
    url = f"{endpoint_for_year(year)}?f=json"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            return None, data["error"].get("message", "ArcGIS error")
        fields = [f["name"] for f in data.get("fields", [])]
        return fields, None
    except Exception as e:
        return None, str(e)


def fetch_year(year):
    """Paginate through all matching records for a given year."""
    query_url = f"{endpoint_for_year(year)}/query"
    where_clause = build_where_clause()
    all_features = []
    offset = 0

    with tqdm(desc=f"  {year}", unit=" rec", leave=False) as pbar:
        while True:
            params = {
                "where": where_clause,
                "outFields": "*",
                "returnGeometry": "true",
                "outSR": "4326",
                "f": "json",
                "resultOffset": offset,
                "resultRecordCount": PAGE_SIZE,
            }
            try:
                resp = requests.get(query_url, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as e:
                print(f"\n  Network error at offset {offset}: {e}")
                break
            except ValueError as e:
                print(f"\n  Bad JSON at offset {offset}: {e}")
                break

            if "error" in data:
                print(f"\n  ArcGIS error: {data['error']}")
                break

            features = data.get("features", [])
            if not features:
                break

            all_features.extend(features)
            pbar.update(len(features))

            if not data.get("exceededTransferLimit", False):
                break

            offset += PAGE_SIZE
            time.sleep(0.15)

    return all_features


def features_to_df(features, year):
    rows = []
    for f in features:
        row = dict(f.get("attributes", {}))
        geom = f.get("geometry", {})
        row["longitude"] = geom.get("x")
        row["latitude"] = geom.get("y")
        row["_source_year"] = year
        rows.append(row)
    return pd.DataFrame(rows)


def normalize_df(df, year):
    if df.empty:
        return df

    col_lower = {c.lower(): c for c in df.columns}

    # Normalize field names to a consistent schema
    aliases = {
        "srrecordid":          "sr_record_id",
        "servicerequestnum":   "servicerequestnum",
        "srtype":              "srtype",
        "methodreceived":      "methodreceived",
        "createddate":         "createddate",
        "statusdate":          "statusdate",
        "srstatus":            "srstatus",
        "priority":            "priority",
        "streetaddress":       "street",
        "street":              "street",
        "address":             "street",
        "crossstreet":         "crossstreet",
        "neighborhood":        "neighborhood",
        "councildistrict":     "councildistrict",
        "policedistrict":      "policeDistrict",
        "zipcode":             "zipcode",
        "latitude":            "latitude",
        "longitude":           "longitude",
    }

    rename = {}
    for alias, standard in aliases.items():
        if alias in col_lower and standard not in df.columns:
            rename[col_lower[alias]] = standard

    df = df.rename(columns=rename)

    # ArcGIS date fields are Unix millisecond timestamps
    for col in ["createddate", "statusdate"]:
        if col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                df[col] = pd.to_datetime(df[col], unit='ms', errors='coerce')
            else:
                df[col] = pd.to_datetime(df[col], errors='coerce')

    for col in ["latitude", "longitude"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    before = len(df)
    df = df.dropna(subset=["latitude", "longitude"])
    # Sanity-check: keep only points inside Baltimore's bounding box
    df = df[df["latitude"].between(39.1, 39.5) & df["longitude"].between(-76.9, -76.4)]
    dropped = before - len(df)
    if dropped:
        print(f"  Dropped {dropped} rows (no coords or outside Baltimore)")

    if "createddate" in df.columns and "statusdate" in df.columns:
        df["resolution_days"] = (df["statusdate"] - df["createddate"]).dt.days
        df["days_since_created"] = (datetime.now() - df["createddate"]).dt.days

    if "neighborhood" in df.columns:
        df["neighborhood"] = df["neighborhood"].astype(str).str.strip().str.title()

    return df


def main():
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

    print("Baltimore 311 Infrastructure Data Fetcher")
    print("=" * 55)
    print(f"Years:    {YEARS_TO_FETCH}")
    print(f"Keywords: {len(INFRASTRUCTURE_KEYWORDS)} infrastructure types")
    print(f"Endpoint: {BASE_URL}/311_Customer_Service_Requests_YYYY/FeatureServer/0")
    print()

    all_dfs = []

    for year in YEARS_TO_FETCH:
        print(f"[{year}] Checking fields...")
        fields, err = get_field_names(year)

        if err:
            print(f"  ⚠️  Could not reach {year}: {err}\n")
            continue

        print(f"  ✓ {len(fields)} fields — fetching records...")
        features = fetch_year(year)

        if not features:
            print(f"  No records returned\n")
            continue

        df = features_to_df(features, year)
        df = normalize_df(df, year)
        print(f"  → {len(df):,} usable records\n")
        all_dfs.append(df)

    if not all_dfs:
        print("❌ No data fetched from any year.")
        return

    combined = pd.concat(all_dfs, ignore_index=True)

    # Deduplicate on service request number
    id_col = next((c for c in ["servicerequestnum", "sr_record_id", "OBJECTID"] if c in combined.columns), None)
    if id_col:
        before = len(combined)
        combined = combined.drop_duplicates(subset=id_col)
        if before - len(combined):
            print(f"Removed {before - len(combined):,} duplicates on {id_col}")

    print("=" * 55)
    print("FETCH COMPLETE")
    print("=" * 55)
    print(f"Total records: {len(combined):,}")

    if "srtype" in combined.columns:
        print(f"\nTop request types:")
        for t, n in combined["srtype"].value_counts().head(12).items():
            print(f"  {str(t):<48} {n:>6,}")

    if "createddate" in combined.columns:
        dates = combined["createddate"].dropna()
        if not dates.empty:
            print(f"\nDate range: {dates.min().date()} → {dates.max().date()}")

    combined.to_csv(OUTPUT_PATH, index=False)
    print(f"\n✓ Saved: {OUTPUT_PATH}")
    print(f"  Next: python scripts/analyze.py")


if __name__ == "__main__":
    main()
