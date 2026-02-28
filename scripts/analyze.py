"""
analyze.py

Core analysis engine for Baltimore 311 infrastructure data.

Key analyses:
1. Chronic Hotspots — locations with repeated 311 reports over time
2. Failed Fix Detection — spots that get re-reported after being closed
3. Neighborhood Report Rate — normalizes by area size for fair comparison
4. Gap Analysis — neighborhoods with Reddit signal but low 311 activity

Outputs: data/analysis_results.json (consumed by the dashboard generator)
"""

import os
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from sklearn.cluster import DBSCAN
from math import radians

# Paths
DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
INPUT_311 = os.path.join(DATA_DIR, '311_requests.csv')
INPUT_REDDIT = os.path.join(DATA_DIR, 'reddit_posts.csv')
OUTPUT_PATH = os.path.join(DATA_DIR, 'analysis_results.json')

# DBSCAN clustering parameters
# eps in degrees (~50 meters at Baltimore's latitude)
CLUSTER_EPS_METERS = 75
METERS_PER_DEGREE_LAT = 111000
EPS_DEGREES = CLUSTER_EPS_METERS / METERS_PER_DEGREE_LAT
MIN_CLUSTER_SAMPLES = 2  # minimum reports to form a cluster

# Thresholds
CHRONIC_MIN_REPORTS = 4        # minimum reports to flag as chronic
CHRONIC_MIN_DAYS_SPAN = 90     # must span at least 90 days
FAILED_FIX_WINDOW_DAYS = 120   # re-report within X days of closure = possible failed fix
HIGH_CHRONIC_THRESHOLD = 8     # reports to flag as "high priority chronic"


def load_data():
    """Load 311 and Reddit data."""
    print("Loading data...")
    
    df_311 = None
    df_reddit = None
    
    if os.path.exists(INPUT_311):
        df_311 = pd.read_csv(INPUT_311, parse_dates=['createddate', 'statusdate'])
        print(f"  311 requests: {len(df_311):,} records")
    else:
        print(f"  Warning: 311 data not found at {INPUT_311}")
        print(f"  Run: python scripts/fetch_311.py")
    
    if os.path.exists(INPUT_REDDIT):
        df_reddit = pd.read_csv(INPUT_REDDIT, parse_dates=['created_utc'])
        print(f"  Reddit posts: {len(df_reddit):,} records")
    else:
        print(f"  Note: Reddit data not found — gap analysis will be skipped")
    
    return df_311, df_reddit


def cluster_locations(df):
    """
    Use DBSCAN to cluster 311 reports by geographic proximity.
    Returns df with a 'cluster_id' column.
    
    Reports within ~75 meters of each other are grouped into a cluster.
    This is how we find "same location, multiple reports."
    """
    coords = df[['latitude', 'longitude']].values
    
    # Convert to radians for haversine metric
    coords_rad = np.radians(coords)
    
    # eps in radians
    eps_rad = EPS_DEGREES * (np.pi / 180)
    
    db = DBSCAN(
        eps=eps_rad,
        min_samples=MIN_CLUSTER_SAMPLES,
        algorithm='ball_tree',
        metric='haversine'
    ).fit(coords_rad)
    
    df = df.copy()
    df['cluster_id'] = db.labels_  # -1 = noise (singleton, not in any cluster)
    
    return df


def build_report_history(group):
    """
    Build a chronological list of individual reports for a cluster,
    flagging which ones are re-reports after a closure (possible failed fixes).
    Returns a list of dicts, one per report, sorted by date.
    """
    sorted_reports = group.sort_values('createddate').reset_index(drop=True)
    history = []
    last_closure_date = None

    for _, row in sorted_reports.iterrows():
        created = row.get('createddate')
        status_date = row.get('statusdate')
        status = row.get('srstatus', '') or ''
        srtype = row.get('srtype', '') or ''
        sr_num = row.get('servicerequestnum', '') or ''
        resolution = row.get('resolution_days')

        # Is this a re-report after a recent closure?
        is_rereport = False
        if last_closure_date is not None and pd.notna(created):
            days_since_closure = (created - last_closure_date).days
            if 0 < days_since_closure <= FAILED_FIX_WINDOW_DAYS:
                is_rereport = True

        history.append({
            'date': created.strftime('%Y-%m-%d') if pd.notna(created) else None,
            'status': status,
            'srtype': srtype,
            'sr_num': str(sr_num)[:20] if sr_num else None,
            'resolution_days': int(resolution) if pd.notna(resolution) and resolution == resolution else None,
            'is_rereport': is_rereport,
        })

        # Track most recent closure date
        if 'closed' in status.lower() and pd.notna(status_date):
            last_closure_date = status_date

    return history


def identify_chronic_hotspots(df):
    """
    Find locations with repeated reports over an extended time span.
    
    A 'chronic' hotspot is a cluster that:
    - Has >= CHRONIC_MIN_REPORTS reports
    - Spans >= CHRONIC_MIN_DAYS_SPAN days between first and most recent report
    """
    # Only work with clustered points (exclude singletons)
    clustered = df[df['cluster_id'] >= 0].copy()
    
    if clustered.empty:
        return pd.DataFrame()
    
    hotspots = []
    
    for cluster_id, group in clustered.groupby('cluster_id'):
        report_count = len(group)
        first_report = group['createddate'].min()
        last_report = group['createddate'].max()
        span_days = (last_report - first_report).days
        
        if report_count < CHRONIC_MIN_REPORTS or span_days < CHRONIC_MIN_DAYS_SPAN:
            continue
        
        # Centroid of the cluster
        centroid_lat = group['latitude'].mean()
        centroid_lon = group['longitude'].mean()
        
        # Most common service type in this cluster
        _srtype_mode = group['srtype'].mode() if 'srtype' in group.columns else pd.Series()
        primary_type = _srtype_mode.iloc[0] if not _srtype_mode.empty else 'Unknown'

        # Most common neighborhood
        _nbhd_mode = group['neighborhood'].dropna().mode() if 'neighborhood' in group.columns else pd.Series()
        neighborhood = _nbhd_mode.iloc[0] if not _nbhd_mode.empty else 'Unknown'
        
        # Status breakdown
        status_counts = group['srstatus'].value_counts().to_dict() if 'srstatus' in group.columns else {}
        
        # Avg resolution time (for closed requests)
        closed = group[group['srstatus'] == 'Closed']
        avg_resolution_days = None
        if not closed.empty and 'resolution_days' in closed.columns:
            avg_resolution_days = closed['resolution_days'].median()
        
        # Chronic severity score (higher = more concerning)
        # Formula: reports * (1 + log of span in months)
        months_span = max(span_days / 30, 1)
        severity_score = round(report_count * (1 + np.log(months_span)), 1)
        
        # Flag for possible failed fixes
        # Look for reports that came within FAILED_FIX_WINDOW_DAYS of a closure
        possible_failed_fixes = detect_failed_fixes(group)
        
        # Address hint — use most common street if available
        address_hint = None
        if 'street' in group.columns:
            _street_mode = group['street'].dropna().mode()
            address_hint = _street_mode.iloc[0] if not _street_mode.empty else None
        
        # Build per-report history for the timeline view
        # Include all reports sorted by date, flagging re-reports after closures
        history = build_report_history(group)

        hotspots.append({
            'cluster_id': int(cluster_id),
            'latitude': round(centroid_lat, 6),
            'longitude': round(centroid_lon, 6),
            'report_count': report_count,
            'first_report': first_report.isoformat(),
            'last_report': last_report.isoformat(),
            'span_days': span_days,
            'primary_type': primary_type,
            'neighborhood': neighborhood,
            'status_breakdown': status_counts,
            'avg_resolution_days': float(avg_resolution_days) if avg_resolution_days else None,
            'severity_score': severity_score,
            'possible_failed_fixes': possible_failed_fixes,
            'address_hint': address_hint,
            'is_high_priority': report_count >= HIGH_CHRONIC_THRESHOLD or possible_failed_fixes >= 2,
            'history': history,
        })
    
    hotspots_df = pd.DataFrame(hotspots)
    if not hotspots_df.empty:
        hotspots_df = hotspots_df.sort_values('severity_score', ascending=False)
    
    return hotspots_df


def detect_failed_fixes(group):
    """
    Count cases where a report was filed within FAILED_FIX_WINDOW_DAYS 
    after a previous report at the same location was marked Closed.
    This is a signal that the fix may not have worked.
    """
    if 'srstatus' not in group.columns or 'createddate' not in group.columns:
        return 0
    
    sorted_reports = group.sort_values('createddate')
    failed_fixes = 0
    
    for i, row in sorted_reports.iterrows():
        if row['srstatus'] != 'Closed' or pd.isna(row['statusdate']):
            continue
        
        closure_date = row['statusdate']
        window_end = closure_date + timedelta(days=FAILED_FIX_WINDOW_DAYS)
        
        # Were there new reports after this closure?
        subsequent = sorted_reports[
            (sorted_reports['createddate'] > closure_date) &
            (sorted_reports['createddate'] <= window_end)
        ]
        
        if not subsequent.empty:
            failed_fixes += 1
    
    return failed_fixes


def neighborhood_summary(df):
    """
    Aggregate statistics by neighborhood.
    Returns a dict keyed by neighborhood name.
    """
    if 'neighborhood' not in df.columns:
        return {}
    
    summary = {}
    
    for neighborhood, group in df.groupby('neighborhood'):
        if pd.isna(neighborhood) or neighborhood == '':
            continue
        
        total_reports = len(group)
        
        # Type breakdown
        type_breakdown = {}
        if 'srtype' in group.columns:
            type_breakdown = group['srtype'].value_counts().to_dict()
        
        # Resolution rate
        resolution_rate = None
        if 'srstatus' in group.columns:
            closed = (group['srstatus'] == 'Closed').sum()
            resolution_rate = round(closed / total_reports * 100, 1)
        
        # Average resolution time
        avg_res_days = None
        if 'resolution_days' in group.columns:
            closed_times = group[group['srstatus'] == 'Closed']['resolution_days']
            if not closed_times.empty:
                avg_res_days = round(closed_times.median(), 1)
        
        # Trend: reports in last 90 days vs previous 90 days
        recent_cutoff = datetime.now() - timedelta(days=90)
        prior_cutoff = datetime.now() - timedelta(days=180)
        recent = len(group[group['createddate'] >= recent_cutoff])
        prior = len(group[(group['createddate'] >= prior_cutoff) & (group['createddate'] < recent_cutoff)])
        
        trend = None
        if prior > 0:
            trend = round((recent - prior) / prior * 100, 1)
        elif recent > 0:
            trend = 100.0
        
        summary[neighborhood] = {
            'total_reports': total_reports,
            'type_breakdown': type_breakdown,
            'resolution_rate': resolution_rate,
            'avg_resolution_days': avg_res_days,
            'recent_90_days': recent,
            'prior_90_days': prior,
            'trend_pct': trend,
        }
    
    return summary


def gap_analysis(df_311, df_reddit):
    """
    Identify neighborhoods with Reddit signal but low 311 activity.
    
    This is the equity lens: places where people are clearly frustrated
    (Reddit) but not filing 311 requests — possibly due to reporting barriers,
    distrust, or lack of awareness.
    
    Returns a list of neighborhoods flagged as potential gaps.
    """
    if df_reddit is None or df_reddit.empty:
        return []
    
    # Count 311 reports per neighborhood
    report_counts = {}
    if df_311 is not None and 'neighborhood' in df_311.columns:
        report_counts = df_311['neighborhood'].value_counts().to_dict()
    
    # Extract neighborhood mentions from Reddit location hints
    reddit_neighborhood_signals = {}
    
    for _, row in df_reddit.iterrows():
        hints_raw = row.get('location_hints', '[]')
        try:
            hints = json.loads(hints_raw) if isinstance(hints_raw, str) else []
        except (json.JSONDecodeError, TypeError):
            hints = []
        
        for hint in hints:
            hint_lower = hint.lower()
            # Match against known neighborhoods (simple substring match)
            for neighborhood in report_counts.keys():
                if neighborhood.lower() in hint_lower or hint_lower in neighborhood.lower():
                    reddit_neighborhood_signals[neighborhood] = (
                        reddit_neighborhood_signals.get(neighborhood, 0) + 1
                    )
    
    # Identify gaps: high Reddit signal, low 311 count
    gaps = []
    avg_311_reports = np.mean(list(report_counts.values())) if report_counts else 0
    
    for neighborhood, reddit_count in reddit_neighborhood_signals.items():
        if reddit_count < 2:  # need at least 2 Reddit signals
            continue
        
        balt_311_count = report_counts.get(neighborhood, 0)
        
        # Flag if Reddit signal is relatively high but 311 is below average
        if balt_311_count < avg_311_reports * 0.5:
            gaps.append({
                'neighborhood': neighborhood,
                'reddit_signal': reddit_count,
                '311_reports': balt_311_count,
                'gap_score': round(reddit_count / max(balt_311_count, 1), 2),
                'note': 'High social signal, low 311 activity — possible reporting barrier'
            })
    
    gaps.sort(key=lambda x: x['gap_score'], reverse=True)
    return gaps


def category_fix_rates(df_311, hotspots_list):
    """
    For each broad request category, calculate:
    - Total requests in the raw 311 data
    - How many requests fall inside chronic hotspot clusters (repeated-report locations)
    - Of those hotspot requests, how many are flagged as re-reports after a closure
    - An overall "recurrence rate" = rereports / total requests in category

    This answers: "Out of all pothole requests, what % appear to be failed fixes
    at chronic locations?"
    """
    def categorize_type(srtype):
        if not srtype:
            return 'Other'
        t = str(srtype).lower()
        if 'pothole' in t: return 'Pothole'
        if 'light' in t or 'streetlight' in t: return 'Street Light'
        if 'alley' in t: return 'Alley'
        if 'sidewalk' in t: return 'Sidewalk'
        if 'water' in t or 'main' in t: return 'Water Main'
        if 'cave' in t or 'sinkhole' in t: return 'Cave-In / Sinkhole'
        if 'storm' in t or 'drain' in t or 'catch' in t: return 'Storm Drain'
        if 'curb' in t or 'bridge' in t or 'street' in t: return 'Street / Curb'
        return 'Other'

    if df_311 is None or df_311.empty:
        return {}

    # Count raw requests per category across all data
    if 'srtype' not in df_311.columns:
        return {}

    # Build a lookup: cluster_id -> (total_rereports, total_reports_in_cluster)
    cluster_rereports = {}
    cluster_total = {}
    for h in hotspots_list:
        cid = h.get('cluster_id')
        if cid is None:
            continue
        history = h.get('history', [])
        cluster_rereports[cid] = sum(1 for r in history if r.get('is_rereport'))
        cluster_total[cid] = len(history)

    # Map each 311 record to its category
    df = df_311.copy()
    df['_category'] = df['srtype'].apply(categorize_type)

    stats = {}
    for cat, group in df.groupby('_category'):
        total = len(group)

        # How many of these are in a chronic cluster? (unused directly, computed via hotspots below)
        clustered_count = 0

        # Sum rereports across all clusters of this category
        # We do this by summing from hotspots whose primary_type maps to this category
        cat_rereports = 0
        cat_cluster_reports = 0
        for h in hotspots_list:
            if categorize_type(h.get('primary_type', '')) != cat:
                continue
            history = h.get('history', [])
            cat_rereports += sum(1 for r in history if r.get('is_rereport'))
            cat_cluster_reports += len(history)

        recurrence_pct = round(cat_rereports / total * 100, 1) if total > 0 else 0
        cluster_pct = round(cat_cluster_reports / total * 100, 1) if total > 0 else 0

        stats[cat] = {
            'total_requests': total,
            'requests_at_chronic_locations': cat_cluster_reports,
            'rereports': cat_rereports,
            'recurrence_pct': recurrence_pct,   # % of all requests that are re-reports
            'chronic_location_pct': cluster_pct, # % of requests at chronic spots
        }

    # Sort by recurrence_pct descending
    stats = dict(sorted(stats.items(), key=lambda x: -x[1]['recurrence_pct']))
    return stats


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    
    df_311, df_reddit = load_data()
    
    if df_311 is None:
        print("\nCannot run analysis without 311 data. Run fetch_311.py first.")
        return
    
    print(f"\nRunning analysis on {len(df_311):,} records...\n")
    
    # Step 1: Cluster by location
    print("Step 1/4: Clustering by location...")
    df_clustered = cluster_locations(df_311)
    n_clusters = len(df_clustered[df_clustered['cluster_id'] >= 0]['cluster_id'].unique())
    print(f"  Found {n_clusters:,} location clusters")
    
    # Step 2: Identify chronic hotspots
    print("Step 2/4: Identifying chronic hotspots...")
    hotspots_df = identify_chronic_hotspots(df_clustered)
    n_hotspots = len(hotspots_df) if not hotspots_df.empty else 0
    n_high_priority = hotspots_df['is_high_priority'].sum() if not hotspots_df.empty else 0
    print(f"  Found {n_hotspots:,} chronic hotspots ({n_high_priority} high priority)")
    
    # Step 3: Neighborhood summary
    print("Step 3/4: Summarizing by neighborhood...")
    neighborhoods = neighborhood_summary(df_311)
    print(f"  Processed {len(neighborhoods):,} neighborhoods")
    
    # Step 4: Gap analysis
    print("Step 4/4: Running gap analysis...")
    gaps = gap_analysis(df_311, df_reddit)
    print(f"  Found {len(gaps):,} potential gap neighborhoods")

    # Build output
    hotspots_list = hotspots_df.to_dict('records') if not hotspots_df.empty else []

    # Step 5: Category fix rates
    print("Step 5/5: Calculating category recurrence rates...")
    cat_stats = category_fix_rates(df_311, hotspots_list)
    print(f"  Processed {len(cat_stats)} categories")
    if cat_stats:
        print(f"  Category recurrence rates:")
        for cat, s in cat_stats.items():
            print(f"    {cat:<25} {s['total_requests']:>6,} requests  "
                  f"{s['recurrence_pct']:>5.1f}% re-reports  "
                  f"{s['rereports']:>5,} failed fixes")
    
    # Summary stats for the dashboard header
    summary_stats = {
        'total_requests': len(df_311),
        'date_range': {
            'start': df_311['createddate'].min().isoformat(),
            'end': df_311['createddate'].max().isoformat(),
        },
        'chronic_hotspots': n_hotspots,
        'high_priority_hotspots': int(n_high_priority),
        'neighborhoods_analyzed': len(neighborhoods),
        'gap_neighborhoods': len(gaps),
        'generated_at': datetime.now().isoformat(),
    }
    
    output = {
        'summary': summary_stats,
        'hotspots': hotspots_list,
        'neighborhoods': neighborhoods,
        'gaps': gaps,
        'category_stats': cat_stats,
    }
    
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f"\nAnalysis saved to: {OUTPUT_PATH}")
    
    # Print top 10 hotspots
    if not hotspots_df.empty:
        print(f"\n{'='*60}")
        print(f"TOP 10 CHRONIC HOTSPOTS")
        print(f"{'='*60}")
        top10 = hotspots_df.head(10)
        for _, row in top10.iterrows():
            addr = row.get('address_hint', 'Unknown location')
            print(f"\n  {addr} ({row['neighborhood']})")
            print(f"  Type: {row['primary_type']}")
            print(f"  Reports: {row['report_count']} over {row['span_days']} days")
            if row['possible_failed_fixes'] > 0:
                print(f"  ⚠️  Possible failed fixes: {row['possible_failed_fixes']}")
            print(f"  Severity score: {row['severity_score']}")


if __name__ == "__main__":
    main()
