"""
generate_dashboard.py

Generates an interactive HTML dashboard from analysis results.
Uses Folium for the map + embedded HTML/CSS/JS for the panel UI.

Output: output/dashboard.html — open in any browser, no server needed.
"""

import os
import json
import folium
from folium.plugins import MarkerCluster, HeatMap
from datetime import datetime

DATA_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'analysis_results.json')
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'output')
OUTPUT_PATH = os.path.join(OUTPUT_DIR, 'dashboard.html')

# Baltimore city center
BALT_CENTER = [39.2904, -76.6122]
DEFAULT_ZOOM = 12

# Color scale for chronic severity
def severity_color(score):
    if score >= 30:
        return '#c0392b'  # deep red — very high
    elif score >= 20:
        return '#e74c3c'  # red
    elif score >= 12:
        return '#e67e22'  # orange
    elif score >= 6:
        return '#f39c12'  # amber
    else:
        return '#f1c40f'  # yellow


def severity_label(score):
    if score >= 30:
        return 'Critical'
    elif score >= 20:
        return 'High'
    elif score >= 12:
        return 'Elevated'
    elif score >= 6:
        return 'Moderate'
    else:
        return 'Low'


def make_hotspot_popup(hotspot):
    """Build HTML popup content for a hotspot marker."""
    addr = hotspot.get('address_hint') or 'Location'
    neighborhood = hotspot.get('neighborhood', 'Unknown')
    report_count = hotspot.get('report_count', 0)
    span_days = hotspot.get('span_days', 0)
    primary_type = hotspot.get('primary_type', 'Unknown')
    severity = hotspot.get('severity_score', 0)
    failed_fixes = hotspot.get('possible_failed_fixes', 0)
    first_report = hotspot.get('first_report', '')[:10]
    last_report = hotspot.get('last_report', '')[:10]
    avg_res = hotspot.get('avg_resolution_days')
    is_high = hotspot.get('is_high_priority', False)
    history = hotspot.get('history', [])
    cluster_id = hotspot.get('cluster_id', 0)

    status_html = ''
    for status, count in hotspot.get('status_breakdown', {}).items():
        status_html += f'<div class="status-row"><span>{status}</span><span>{count}</span></div>'

    failed_fix_html = ''
    if failed_fixes > 0:
        failed_fix_html = f'''
        <div class="failed-fix-warning">
            ⚠️ {failed_fixes} possible failed fix{"es" if failed_fixes > 1 else ""}
            <span class="failed-fix-note">Re-reported within 120 days of closure</span>
        </div>'''

    priority_badge = '<span class="priority-badge">HIGH PRIORITY</span>' if is_high else ''

    # Build history timeline rows
    history_rows = ''
    for entry in history:
        date = entry.get('date') or '—'
        status = entry.get('status') or '—'
        res = entry.get('resolution_days')
        is_rereport = entry.get('is_rereport', False)

        row_class = 'hist-rereport' if is_rereport else 'hist-normal'
        rereport_badge = '<span class="rereport-badge">↩ re-report</span>' if is_rereport else ''
        res_str = f'{res}d' if res is not None else '—'

        status_class = ''
        if 'closed' in status.lower() and 'duplicate' not in status.lower():
            status_class = 'hist-status-closed'
        elif 'open' in status.lower() or 'new' in status.lower():
            status_class = 'hist-status-open'

        history_rows += f'''<tr class="{row_class}">
            <td class="hist-date">{date}</td>
            <td class="hist-status {status_class}">{status} {rereport_badge}</td>
            <td class="hist-res">{res_str}</td>
        </tr>'''

    return f"""
    <div class="popup-card">
        <div class="popup-header">
            <div class="popup-title">{addr}</div>
            <div class="popup-neighborhood">{neighborhood} {priority_badge}</div>
        </div>
        <div class="popup-type">{primary_type}</div>
        {failed_fix_html}
        <div class="popup-stats">
            <div class="stat-box">
                <div class="stat-num">{report_count}</div>
                <div class="stat-label">total reports</div>
            </div>
            <div class="stat-box">
                <div class="stat-num">{span_days}</div>
                <div class="stat-label">days active</div>
            </div>
            <div class="stat-box">
                <div class="stat-num">{round(severity, 1)}</div>
                <div class="stat-label">severity score</div>
            </div>
        </div>
        <div class="popup-timeline-range">
            <span>First: {first_report}</span>
            <span>→</span>
            <span>Last: {last_report}</span>
        </div>
        {f'<div class="popup-resolution">Median resolution: {round(avg_res)} days</div>' if avg_res and avg_res == avg_res else ''}
        <div class="status-breakdown">
            <div class="status-heading">Status breakdown</div>
            {status_html}
        </div>
        <div class="history-section">
            <button class="history-toggle" onclick="toggleHistory('hist-{cluster_id}', this)">
                ▶ Show full history ({report_count} reports)
            </button>
            <div id="hist-{cluster_id}" class="history-table-wrap" style="display:none">
                <table class="history-table">
                    <thead><tr>
                        <th>Date</th><th>Status</th><th>Resolved</th>
                    </tr></thead>
                    <tbody>{history_rows}</tbody>
                </table>
            </div>
        </div>
    </div>
    """


def categorize_type(srtype):
    """Map a raw srtype string to a broad filter category."""
    if not srtype:
        return 'Other'
    t = str(srtype).lower()
    if 'pothole' in t:
        return 'Pothole'
    if 'light' in t or 'streetlight' in t:
        return 'Street Light'
    if 'alley' in t:
        return 'Alley'
    if 'sidewalk' in t:
        return 'Sidewalk'
    if 'water' in t or 'main' in t:
        return 'Water Main'
    if 'cave' in t or 'sinkhole' in t:
        return 'Cave-In / Sinkhole'
    if 'storm' in t or 'drain' in t or 'catch' in t:
        return 'Storm Drain'
    if 'curb' in t or 'bridge' in t or 'street' in t:
        return 'Street / Curb'
    return 'Other'


# Category → color for filter chips and markers
CATEGORY_COLORS = {
    'Pothole':           '#e74c3c',
    'Street Light':      '#f1c40f',
    'Alley':             '#9b59b6',
    'Sidewalk':          '#3498db',
    'Water Main':        '#1abc9c',
    'Cave-In / Sinkhole':'#e67e22',
    'Storm Drain':       '#2980b9',
    'Street / Curb':     '#e67e22',
    'Other':             '#7f8c8d',
}


def build_map(data):
    """Build the Folium map — markers are driven by embedded JS data for live filtering."""

    m = folium.Map(
        location=BALT_CENTER,
        zoom_start=DEFAULT_ZOOM,
        tiles='CartoDB dark_matter',
        prefer_canvas=True
    )

    hotspots = data.get('hotspots', [])

    # Embed all hotspot data as a JS variable so the filter can work client-side.
    # We build a compact array: [lat, lon, category, severity, address, neighborhood,
    #                             report_count, failed_fixes, is_high, popup_html]
    hotspot_js_data = []
    for h in hotspots:
        lat = h.get('latitude')
        lon = h.get('longitude')
        if not lat or not lon:
            continue
        cat = categorize_type(h.get('primary_type', ''))
        popup_html = make_hotspot_popup(h).replace('`', "'").replace('\n', ' ')
        hotspot_js_data.append({
            'lat': lat, 'lon': lon,
            'cat': cat,
            'score': h.get('severity_score', 0),
            'addr': h.get('address_hint') or 'Unknown',
            'hood': h.get('neighborhood', ''),
            'count': h.get('report_count', 0),
            'failed': h.get('possible_failed_fixes', 0),
            'high': h.get('is_high_priority', False),
            'popup': popup_html,
        })

    # Inject the data + rendering engine as a custom JS element
    hotspot_json = json.dumps(hotspot_js_data)
    category_colors_json = json.dumps(CATEGORY_COLORS)

    js_engine = f"""
    <script id="hotspot-data" type="application/json">{hotspot_json}</script>
    <script id="category-colors" type="application/json">{category_colors_json}</script>
    """
    m.get_root().html.add_child(folium.Element(js_engine))

    # Add a thin base heatmap using just the top 200 hotspots (perf)
    if hotspots:
        heat_data = [
            [h['latitude'], h['longitude'], min(h['severity_score'], 50)]
            for h in hotspots[:200]
            if h.get('latitude') and h.get('longitude')
        ]
        HeatMap(
            heat_data,
            name='Severity Heatmap',
            min_opacity=0.2,
            max_zoom=16,
            radius=30,
            blur=25,
            gradient={0.2: '#3498db', 0.5: '#f39c12', 0.8: '#e74c3c', 1.0: '#c0392b'}
        ).add_to(m)

    return m


def build_sidebar_html(data):
    """Generate the sidebar stats panel HTML."""
    summary = data.get('summary', {})
    hotspots = data.get('hotspots', [])
    gaps = data.get('gaps', [])
    neighborhoods = data.get('neighborhoods', {})

    # Build filter chips from actual categories present in data
    cats_present = {}
    for h in hotspots:
        cat = categorize_type(h.get('primary_type', ''))
        cats_present[cat] = cats_present.get(cat, 0) + 1

    filter_chips = ''
    for cat, cnt in sorted(cats_present.items(), key=lambda x: -x[1]):
        color = CATEGORY_COLORS.get(cat, '#7f8c8d')
        filter_chips += f'''
        <div class="filter-chip" data-cat="{cat}"
             style="--chip-color:{color}"
             onclick="toggleFilter(\'{cat}\')">
            <div class="chip-dot"></div>{cat} <span style="opacity:.5;font-size:10px">({cnt})</span>
        </div>'''

    filter_section = f'''
    <div class="filter-section">
        <div class="filter-title">Filter by Type</div>
        <div class="filter-chips">
            {filter_chips}
            <div class="filter-chip failed-fix-chip" id="failed-fixes-toggle"
                 style="--chip-color:#e74c3c"
                 onclick="toggleFailedFixes()">
                <div class="chip-dot"></div>⚠️ Failed fixes only
            </div>
        </div>
        <button id="clear-filters" onclick="clearFilters()">Clear filters</button>
    </div>
    '''

    # Top hotspots for sidebar list
    top_hotspots = sorted(hotspots, key=lambda x: x.get('severity_score', 0), reverse=True)[:8]

    hotspot_rows = ''
    for h in top_hotspots:
        color = severity_color(h.get('severity_score', 0))
        label = severity_label(h.get('severity_score', 0))
        addr = h.get('address_hint') or 'Unknown location'
        hood = h.get('neighborhood', '')
        count = h.get('report_count', 0)
        failed = h.get('possible_failed_fixes', 0)
        cat = categorize_type(h.get('primary_type', ''))
        cat_color = CATEGORY_COLORS.get(cat, color)
        failed_html = f'<span class="failed-tag">⚠️ {failed} failed fix{"es" if failed > 1 else ""}</span>' if failed else ''

        hotspot_rows += f"""
        <div class="hotspot-row" data-cat="{cat}" data-failed="{failed}" onclick="focusHotspot({h['latitude']}, {h['longitude']})">
            <div class="hotspot-dot" style="background:{cat_color}"></div>
            <div class="hotspot-info">
                <div class="hotspot-addr">{addr[:35]}{'...' if len(addr) > 35 else ''}</div>
                <div class="hotspot-meta">{hood} · {count} reports · <span class="severity-tag" style="color:{color}">{label}</span> {failed_html}</div>
            </div>
        </div>"""
    
    # Top neighborhoods by volume
    top_neighborhoods = sorted(
        [(k, v) for k, v in neighborhoods.items() if v.get('total_reports', 0) > 0],
        key=lambda x: x[1].get('total_reports', 0),
        reverse=True
    )[:8]
    
    max_reports = top_neighborhoods[0][1]['total_reports'] if top_neighborhoods else 1
    
    neighborhood_rows = ''
    for name, stats in top_neighborhoods:
        pct = stats['total_reports'] / max_reports * 100
        trend = stats.get('trend_pct')
        trend_html = ''
        if trend is not None:
            arrow = '↑' if trend > 5 else '↓' if trend < -5 else '→'
            color = '#e74c3c' if trend > 10 else '#2ecc71' if trend < -10 else '#95a5a6'
            trend_html = f'<span style="color:{color};font-size:11px">{arrow} {abs(round(trend))}%</span>'
        
        neighborhood_rows += f"""
        <div class="nbhd-row">
            <div class="nbhd-name">{name[:22]}{'...' if len(name) > 22 else ''} {trend_html}</div>
            <div class="nbhd-bar-wrap">
                <div class="nbhd-bar" style="width:{pct}%"></div>
            </div>
            <div class="nbhd-count">{stats['total_reports']}</div>
        </div>"""
    
    # Gap neighborhoods
    gap_rows = ''
    for gap in gaps[:5]:
        gap_rows += f"""
        <div class="gap-row">
            <div class="gap-name">{gap['neighborhood']}</div>
            <div class="gap-meta">Reddit signal: {gap['reddit_signal']} · 311 reports: {gap['311_reports']}</div>
        </div>"""

    if not gap_rows:
        gap_rows = '<div class="no-data">Run fetch_reddit.py to enable gap analysis</div>'

    # Category recurrence rate rows
    cat_stats = data.get('category_stats', {})
    cat_rows = ''
    for cat, s in cat_stats.items():
        total = s.get('total_requests', 0)
        rereports = s.get('rereports', 0)
        pct = s.get('recurrence_pct', 0)
        color = CATEGORY_COLORS.get(cat, '#7f8c8d')

        # Bar fill: scale 0-100% but visually cap at 80 so even high values fit
        bar_pct = min(pct, 100)
        # Color the bar red if high recurrence, amber if medium, green if low
        bar_color = '#e74c3c' if pct >= 40 else '#e67e22' if pct >= 20 else '#2ecc71'

        cat_rows += f"""
        <div class="cat-row">
            <div class="cat-header">
                <div class="cat-dot" style="background:{color}"></div>
                <div class="cat-name">{cat}</div>
                <div class="cat-pct" style="color:{bar_color}">{pct}%</div>
            </div>
            <div class="cat-bar-wrap">
                <div class="cat-bar" style="width:{bar_pct}%;background:{bar_color}"></div>
            </div>
            <div class="cat-detail">{total:,} requests · {rereports:,} apparent re-reports</div>
        </div>"""
    if not cat_rows:
        cat_rows = '<div class="no-data">Re-run analyze.py to generate category stats</div>'
    
    date_range = ''
    if 'date_range' in summary:
        start = summary['date_range'].get('start', '')[:10]
        end = summary['date_range'].get('end', '')[:10]
        date_range = f'{start} → {end}'
    
    generated = summary.get('generated_at', '')[:16].replace('T', ' ')
    
    return f"""
    <div id="sidebar">
        <div class="sidebar-header">
            <div class="sidebar-title">Baltimore 311</div>
            <div class="sidebar-subtitle">Infrastructure Intelligence</div>
            <div class="sidebar-meta">{date_range}</div>
        </div>
        
        {filter_section}
        <div class="summary-cards">
            <div class="summary-card">
                <div class="card-num">{summary.get('total_requests', 0):,}</div>
                <div class="card-label">total requests</div>
            </div>
            <div class="summary-card alert">
                <div class="card-num">{summary.get('chronic_hotspots', 0)}</div>
                <div class="card-label">chronic hotspots</div>
            </div>
            <div class="summary-card danger">
                <div class="card-num">{summary.get('high_priority_hotspots', 0)}</div>
                <div class="card-label">high priority</div>
            </div>
            <div class="summary-card gap">
                <div class="card-num">{summary.get('gap_neighborhoods', 0)}</div>
                <div class="card-label">gap areas</div>
            </div>
        </div>
        
        <div class="section">
            <div class="section-title">Chronic Hotspots</div>
            <div class="section-subtitle">Locations with repeated reports over time</div>
            {hotspot_rows or '<div class="no-data">No chronic hotspots found</div>'}
        </div>
        
        <div class="section">
            <div class="section-title">By Neighborhood</div>
            <div class="section-subtitle">Report volume (last {(summary.get('date_range', {}) or {}).get('start', '')[:4] or '2'} years)</div>
            {neighborhood_rows or '<div class="no-data">No neighborhood data</div>'}
        </div>
        
        <div class="section">
            <div class="section-title">Fix Effectiveness by Category</div>
            <div class="section-subtitle">% of all requests that appear to be re-reports after a closure</div>
            {cat_rows}
        </div>

        <div class="section">
            <div class="section-title">Gap Analysis</div>
            <div class="section-subtitle">Social signal without 311 activity</div>
            {gap_rows}
        </div>
        
        <div class="sidebar-footer">
            Generated {generated} · Open Baltimore + r/baltimore
        </div>
    </div>
    """


def inject_ui(map_html, sidebar_html):
    """Inject the sidebar and custom styles into the Folium map HTML."""
    
    custom_css = """
    <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600&display=swap" rel="stylesheet">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        
        body { 
            font-family: 'IBM Plex Sans', sans-serif;
            background: #0d0f12;
            color: #e0e0e0;
        }
        
        #map { 
            position: fixed !important;
            top: 0; left: 320px; right: 0; bottom: 0;
        }
        
        #sidebar {
            position: fixed;
            top: 0; left: 0;
            width: 320px; height: 100vh;
            background: #0d0f12;
            border-right: 1px solid #1e2328;
            overflow-y: auto;
            z-index: 1000;
            scrollbar-width: thin;
            scrollbar-color: #2a2f36 transparent;
        }
        
        #sidebar::-webkit-scrollbar { width: 4px; }
        #sidebar::-webkit-scrollbar-track { background: transparent; }
        #sidebar::-webkit-scrollbar-thumb { background: #2a2f36; border-radius: 2px; }
        
        .sidebar-header {
            padding: 20px 18px 16px;
            border-bottom: 1px solid #1e2328;
            background: linear-gradient(180deg, #111418 0%, #0d0f12 100%);
        }
        
        .sidebar-title {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 18px;
            font-weight: 600;
            color: #ffffff;
            letter-spacing: 0.5px;
        }
        
        .sidebar-subtitle {
            font-size: 11px;
            color: #5a6370;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            margin-top: 3px;
        }
        
        .sidebar-meta {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 10px;
            color: #3a4048;
            margin-top: 8px;
        }
        
        .summary-cards {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 1px;
            background: #1a1e24;
            border-bottom: 1px solid #1e2328;
        }
        
        .summary-card {
            background: #0d0f12;
            padding: 14px 14px 12px;
            text-align: center;
        }
        
        .summary-card.alert { background: #100d08; }
        .summary-card.danger { background: #100808; }
        .summary-card.gap { background: #081010; }
        
        .card-num {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 24px;
            font-weight: 600;
            color: #ffffff;
            line-height: 1;
        }
        
        .summary-card.alert .card-num { color: #e67e22; }
        .summary-card.danger .card-num { color: #e74c3c; }
        .summary-card.gap .card-num { color: #3498db; }
        
        .card-label {
            font-size: 10px;
            color: #4a5260;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            margin-top: 4px;
        }
        
        .section {
            padding: 16px 18px;
            border-bottom: 1px solid #1a1e24;
        }
        
        .section-title {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 11px;
            font-weight: 600;
            color: #8899aa;
            text-transform: uppercase;
            letter-spacing: 1.2px;
        }
        
        .section-subtitle {
            font-size: 11px;
            color: #3a4048;
            margin-top: 2px;
            margin-bottom: 12px;
        }
        
        .hotspot-row {
            display: flex;
            align-items: flex-start;
            gap: 10px;
            padding: 8px 0;
            border-bottom: 1px solid #13171c;
            cursor: pointer;
            transition: background 0.15s;
            border-radius: 4px;
            padding: 8px 6px;
            margin: 0 -6px;
        }
        
        .hotspot-row:hover { background: #131820; }
        .hotspot-row:last-child { border-bottom: none; }
        
        .hotspot-dot {
            width: 9px; height: 9px;
            border-radius: 50%;
            flex-shrink: 0;
            margin-top: 4px;
        }
        
        .hotspot-info { flex: 1; min-width: 0; }
        
        .hotspot-addr {
            font-size: 13px;
            color: #d0d8e0;
            font-weight: 500;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        
        .hotspot-meta {
            font-size: 11px;
            color: #4a5260;
            margin-top: 2px;
        }
        
        .severity-tag { font-weight: 600; }
        
        .failed-tag {
            font-size: 10px;
            color: #e74c3c;
            background: rgba(231, 76, 60, 0.1);
            padding: 1px 5px;
            border-radius: 3px;
            font-weight: 600;
        }
        
        .nbhd-row {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 5px 0;
        }
        
        .nbhd-name {
            font-size: 11px;
            color: #8899aa;
            width: 110px;
            flex-shrink: 0;
        }
        
        .nbhd-bar-wrap {
            flex: 1;
            height: 4px;
            background: #1a1e24;
            border-radius: 2px;
            overflow: hidden;
        }
        
        .nbhd-bar {
            height: 100%;
            background: linear-gradient(90deg, #3498db, #2980b9);
            border-radius: 2px;
        }
        
        .nbhd-count {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 11px;
            color: #4a5260;
            width: 32px;
            text-align: right;
        }
        
        .gap-row {
            padding: 8px 0;
            border-bottom: 1px solid #13171c;
        }
        
        .gap-row:last-child { border-bottom: none; }
        
        .gap-name {
            font-size: 13px;
            color: #3498db;
            font-weight: 500;
        }
        
        .gap-meta {
            font-size: 11px;
            color: #4a5260;
            margin-top: 2px;
        }
        
        .no-data {
            font-size: 12px;
            color: #3a4048;
            font-style: italic;
            padding: 8px 0;
        }
        
        .sidebar-footer {
            padding: 12px 18px;
            font-size: 10px;
            color: #2a3038;
            border-top: 1px solid #1a1e24;
        }
        
        /* Popup styles */
        .popup-card {
            font-family: 'IBM Plex Sans', sans-serif;
            min-width: 260px;
            max-width: 300px;
        }
        
        .popup-header {
            margin-bottom: 6px;
        }
        
        .popup-title {
            font-size: 15px;
            font-weight: 600;
            color: #1a1a2e;
        }
        
        .popup-neighborhood {
            font-size: 11px;
            color: #7f8c8d;
            margin-top: 2px;
        }
        
        .priority-badge {
            background: #e74c3c;
            color: white;
            font-size: 9px;
            padding: 2px 5px;
            border-radius: 3px;
            font-weight: 700;
            letter-spacing: 0.5px;
            margin-left: 4px;
        }
        
        .popup-type {
            font-size: 12px;
            color: #3498db;
            font-weight: 500;
            margin-bottom: 8px;
        }
        
        .failed-fix-warning {
            background: #fdf3f2;
            border-left: 3px solid #e74c3c;
            padding: 6px 8px;
            font-size: 12px;
            color: #c0392b;
            font-weight: 600;
            margin-bottom: 8px;
            border-radius: 0 3px 3px 0;
        }
        
        .failed-fix-note {
            display: block;
            font-size: 10px;
            color: #e74c3c;
            font-weight: 400;
            margin-top: 2px;
        }
        
        .popup-stats {
            display: flex;
            gap: 6px;
            margin-bottom: 8px;
        }
        
        .stat-box {
            flex: 1;
            background: #f5f7fa;
            border-radius: 4px;
            padding: 6px;
            text-align: center;
        }
        
        .stat-num {
            font-size: 20px;
            font-weight: 700;
            color: #2c3e50;
            line-height: 1;
        }
        
        .stat-label {
            font-size: 9px;
            color: #95a5a6;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-top: 2px;
        }
        
        .popup-timeline {
            display: flex;
            gap: 6px;
            font-size: 11px;
            color: #7f8c8d;
            margin-bottom: 6px;
        }
        
        .popup-resolution {
            font-size: 11px;
            color: #7f8c8d;
            margin-bottom: 6px;
        }
        
        .status-breakdown {
            border-top: 1px solid #ecf0f1;
            padding-top: 6px;
        }
        
        .status-heading {
            font-size: 10px;
            text-transform: uppercase;
            color: #95a5a6;
            letter-spacing: 0.8px;
            margin-bottom: 4px;
        }
        
        .status-row {
            display: flex;
            justify-content: space-between;
            font-size: 12px;
            color: #34495e;
            padding: 2px 0;
        }
        
        /* Fix Folium popup styling */
        .leaflet-popup-content-wrapper {
            border-radius: 6px !important;
            box-shadow: 0 4px 20px rgba(0,0,0,0.3) !important;
            padding: 0 !important;
        }
        
        .leaflet-popup-content {
            margin: 14px !important;
        }

        /* History timeline */
        .history-section { margin-top: 10px; border-top: 1px solid #ecf0f1; padding-top: 8px; }

        .history-toggle {
            background: #f0f4f8;
            border: 1px solid #dde3ea;
            border-radius: 4px;
            padding: 5px 10px;
            font-size: 12px;
            color: #34495e;
            cursor: pointer;
            width: 100%;
            text-align: left;
            font-family: 'IBM Plex Sans', sans-serif;
            transition: background 0.15s;
        }
        .history-toggle:hover { background: #e2eaf2; }
        .history-toggle.open { background: #2c3e50; color: #fff; border-color: #2c3e50; }

        .history-table-wrap {
            margin-top: 8px;
            max-height: 220px;
            overflow-y: auto;
            border: 1px solid #ecf0f1;
            border-radius: 4px;
        }

        .history-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 11px;
        }

        .history-table thead tr {
            background: #f5f7fa;
            position: sticky;
            top: 0;
        }

        .history-table th {
            padding: 5px 6px;
            text-align: left;
            color: #7f8c8d;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            font-size: 10px;
            border-bottom: 1px solid #ecf0f1;
        }

        .history-table td { padding: 4px 6px; border-bottom: 1px solid #f5f7fa; color: #34495e; }
        .history-table tr:last-child td { border-bottom: none; }

        .hist-rereport { background: #fff8f0 !important; }
        .hist-rereport td { color: #c0392b !important; }

        .rereport-badge {
            background: #e74c3c;
            color: white;
            font-size: 9px;
            padding: 1px 4px;
            border-radius: 3px;
            font-weight: 600;
            white-space: nowrap;
        }

        .hist-status-closed { color: #27ae60 !important; font-weight: 500; }
        .hist-status-open { color: #e67e22 !important; font-weight: 500; }
        .hist-date { white-space: nowrap; color: #7f8c8d !important; }
        .hist-res { white-space: nowrap; text-align: right; color: #95a5a6 !important; }

        .popup-timeline-range {
            display: flex;
            gap: 6px;
            font-size: 11px;
            color: #7f8c8d;
            margin-bottom: 6px;
        }

        /* Category fix rate rows */
        .cat-row { padding: 8px 0; border-bottom: 1px solid #13171c; }
        .cat-row:last-child { border-bottom: none; }

        .cat-header {
            display: flex;
            align-items: center;
            gap: 7px;
            margin-bottom: 5px;
        }

        .cat-dot {
            width: 8px; height: 8px;
            border-radius: 50%;
            flex-shrink: 0;
        }

        .cat-name {
            flex: 1;
            font-size: 12px;
            color: #c0c8d0;
            font-weight: 500;
        }

        .cat-pct {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 13px;
            font-weight: 700;
            min-width: 40px;
            text-align: right;
        }

        .cat-bar-wrap {
            height: 4px;
            background: #1a1e24;
            border-radius: 2px;
            overflow: hidden;
            margin-bottom: 4px;
        }

        .cat-bar {
            height: 100%;
            border-radius: 2px;
            transition: width 0.4s ease;
        }

        .cat-detail {
            font-size: 10px;
            color: #3a4048;
        }

        /* Filter chips */
        .filter-section {
            padding: 14px 18px;
            border-bottom: 1px solid #1a1e24;
        }

        .filter-title {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 11px;
            font-weight: 600;
            color: #8899aa;
            text-transform: uppercase;
            letter-spacing: 1.2px;
            margin-bottom: 10px;
        }

        .filter-chips {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
        }

        .filter-chip {
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 4px 9px;
            border-radius: 20px;
            font-size: 11px;
            font-weight: 500;
            cursor: pointer;
            border: 1px solid transparent;
            background: #1a1e24;
            color: #6a7580;
            transition: all 0.15s;
            user-select: none;
        }

        .filter-chip:hover {
            color: #c0c8d0;
            border-color: #2a3040;
        }

        .filter-chip.active {
            color: #fff;
            border-color: var(--chip-color);
            background: color-mix(in srgb, var(--chip-color) 20%, #0d0f12);
            box-shadow: 0 0 0 1px var(--chip-color) inset;
        }

        .chip-dot {
            width: 7px; height: 7px;
            border-radius: 50%;
            background: var(--chip-color);
            flex-shrink: 0;
        }

        #clear-filters {
            display: none;
            margin-top: 8px;
            font-size: 11px;
            color: #4a5260;
            cursor: pointer;
            text-decoration: underline;
            background: none;
            border: none;
            padding: 0;
            font-family: 'IBM Plex Sans', sans-serif;
        }

        #clear-filters:hover { color: #8899aa; }
    </style>
    """
    
    custom_js = """
    <script>
    // ── State ──────────────────────────────────────────────────────────
    var _map = null;
    var _markers = [];          // {marker, cat, score} for each hotspot
    var _activeFilters = new Set(); // empty = show all

    // ── Get the Leaflet map instance ───────────────────────────────────
    function getMap() {
        if (_map) return _map;
        var candidates = Object.values(window).filter(function(v) {
            return v && typeof v === 'object' && v._leaflet_id && typeof v.addLayer === 'function';
        });
        _map = candidates[0] || null;
        return _map;
    }

    // ── Severity → color ───────────────────────────────────────────────
    function severityColor(score) {
        if (score >= 30) return '#c0392b';
        if (score >= 20) return '#e74c3c';
        if (score >= 12) return '#e67e22';
        if (score >= 6)  return '#f39c12';
        return '#f1c40f';
    }

    // ── Build all markers from embedded JSON ───────────────────────────
    function buildMarkers() {
        var map = getMap();
        if (!map) { setTimeout(buildMarkers, 400); return; }

        var raw = document.getElementById('hotspot-data');
        if (!raw) return;
        var hotspots = JSON.parse(raw.textContent);
        var catColors = JSON.parse(document.getElementById('category-colors').textContent);

        hotspots.forEach(function(h) {
            var color = catColors[h.cat] || severityColor(h.score);
            var radius = h.high ? 10 : 7;

            var marker = L.circleMarker([h.lat, h.lon], {
                radius: radius,
                color: color,
                weight: h.high ? 2 : 1,
                fill: true,
                fillColor: color,
                fillOpacity: 0.85,
            });

            // Popup
            var popup = L.popup({ maxWidth: 320 });
            popup.setContent(h.popup);
            marker.bindPopup(popup);
            marker.bindTooltip(h.addr + ' — ' + h.count + ' reports', { sticky: true });

            marker.addTo(map);
            _markers.push({ marker: marker, cat: h.cat, score: h.score, failed: h.failed || 0, map: map });
        });

        console.log('Built ' + _markers.length + ' markers');
        updateVisibility();
    }

    // ── Show/hide markers based on active filters ──────────────────────
    function updateVisibility() {
        _markers.forEach(function(m) {
            var show = _activeFilters.size === 0 || _activeFilters.has(m.cat);
            if (show) {
                if (!m.map.hasLayer(m.marker)) m.marker.addTo(m.map);
            } else {
                if (m.map.hasLayer(m.marker)) m.map.removeLayer(m.marker);
            }
        });

        // Update sidebar hotspot list visibility
        document.querySelectorAll('.hotspot-row').forEach(function(row) {
            var cat = row.getAttribute('data-cat');
            var show = _activeFilters.size === 0 || _activeFilters.has(cat);
            row.style.display = show ? '' : 'none';
        });
    }

    // ── Toggle a filter chip ───────────────────────────────────────────
    function toggleFilter(cat) {
        if (_activeFilters.has(cat)) {
            _activeFilters.delete(cat);
        } else {
            _activeFilters.add(cat);
        }

        // Update chip appearance
        document.querySelectorAll('.filter-chip').forEach(function(chip) {
            if (chip.getAttribute('data-cat') === cat) {
                chip.classList.toggle('active', _activeFilters.has(cat));
            }
        });

        // Update "clear" button visibility
        document.getElementById('clear-filters').style.display =
            _activeFilters.size > 0 ? 'block' : 'none';

        updateVisibility();
    }

    function clearFilters() {
        _activeFilters.clear();
        _failedFixesOnly = false;
        document.querySelectorAll('.filter-chip').forEach(function(c) {
            c.classList.remove('active');
        });
        var ffBtn = document.getElementById('failed-fixes-toggle');
        if (ffBtn) ffBtn.textContent = '⚠️ Failed fixes only';
        document.getElementById('clear-filters').style.display = 'none';
        updateVisibility();
    }

    // ── Fly to a hotspot from sidebar ──────────────────────────────────
    function focusHotspot(lat, lon) {
        var map = getMap();
        if (map) map.setView([lat, lon], 17, { animate: true, duration: 1.0 });
    }

    // ── Toggle history panel inside popup ─────────────────────────────
    function toggleHistory(id, btn) {
        var el = document.getElementById(id);
        if (!el) return;
        var open = el.style.display === 'none' || el.style.display === '';
        el.style.display = open ? 'block' : 'none';
        btn.classList.toggle('open', open);
        btn.textContent = (open ? '▼ Hide history' : '▶ Show full history') +
            btn.textContent.replace(/[▼▶] (Hide|Show full) history/, '').trim();
        // Re-extract report count from original text
        var m = btn.textContent.match(/[(](\\d+) reports[)]/);
        var cnt = m ? ' (' + m[1] + ' reports)' : '';
        btn.textContent = (open ? '▼ Hide history' : '▶ Show full history') + cnt;
    }

    // ── Failed-fixes-only toggle ───────────────────────────────────────
    var _failedFixesOnly = false;

    function toggleFailedFixes() {
        _failedFixesOnly = !_failedFixesOnly;
        var btn = document.getElementById('failed-fixes-toggle');
        if (btn) {
            btn.classList.toggle('active', _failedFixesOnly);
            btn.textContent = _failedFixesOnly ? '⚠️ Failed fixes (on)' : '⚠️ Failed fixes only';
        }
        updateVisibility();
    }

    // Patch updateVisibility to respect failed-fixes filter
    var _origUpdateVisibility = null;
    function updateVisibility() {
        _markers.forEach(function(m) {
            var catMatch = _activeFilters.size === 0 || _activeFilters.has(m.cat);
            var failedMatch = !_failedFixesOnly || m.failed > 0;
            var show = catMatch && failedMatch;
            if (show) {
                if (!m.map.hasLayer(m.marker)) m.marker.addTo(m.map);
            } else {
                if (m.map.hasLayer(m.marker)) m.map.removeLayer(m.marker);
            }
        });
        document.querySelectorAll('.hotspot-row').forEach(function(row) {
            var cat = row.getAttribute('data-cat');
            var failed = parseInt(row.getAttribute('data-failed') || '0');
            var catMatch = _activeFilters.size === 0 || _activeFilters.has(cat);
            var failedMatch = !_failedFixesOnly || failed > 0;
            row.style.display = (catMatch && failedMatch) ? '' : 'none';
        });
    }

    // ── Init ───────────────────────────────────────────────────────────
    window.addEventListener('load', function() {
        setTimeout(function() {
            var map = getMap();
            if (map) map.invalidateSize();
            buildMarkers();
        }, 350);
    });
    </script>
    """
    
    # Inject into the map HTML
    map_html = map_html.replace('</head>', custom_css + '</head>')
    map_html = map_html.replace('</body>', sidebar_html + custom_js + '</body>')
    
    return map_html


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    if not os.path.exists(DATA_PATH):
        print(f"Analysis results not found at {DATA_PATH}")
        print("Run: python scripts/analyze.py")
        return
    
    print("Loading analysis results...")
    with open(DATA_PATH) as f:
        data = json.load(f)
    
    summary = data.get('summary', {})
    print(f"  {summary.get('chronic_hotspots', 0)} chronic hotspots")
    print(f"  {summary.get('neighborhoods_analyzed', 0)} neighborhoods")
    
    print("Building map...")
    m = build_map(data)
    
    print("Building sidebar...")
    sidebar_html = build_sidebar_html(data)
    
    print("Assembling dashboard...")

    # Use folium.save() to get a proper standalone HTML file
    import tempfile as _tempfile
    import os as _os
    _tmp = _tempfile.mktemp(suffix=".html")
    m.save(_tmp)
    with open(_tmp, "r", encoding="utf-8") as f:
        full_html = f.read()
    _os.unlink(_tmp)

    # Override map div positioning to leave room for 320px sidebar
    _map_css = """
    <style>
    body { margin: 0; padding: 0; overflow: hidden; background: #0d0f12; }
    .folium-map {
        position: fixed !important;
        top: 0 !important; left: 320px !important;
        right: 0 !important; bottom: 0 !important;
        width: calc(100vw - 320px) !important;
        height: 100vh !important;
        z-index: 1;
    }
    </style>
    """
    # After Folium JS initializes the map, invalidate its size so it fills the div correctly
    _fix_js = """
    <script>
    window.addEventListener('load', function() {
        setTimeout(function() {
            // Find the Leaflet map instance and tell it to recalculate its size
            var maps = Object.values(window).filter(function(v) {
                return v && typeof v === 'object' && v._leaflet_id && typeof v.invalidateSize === 'function';
            });
            maps.forEach(function(m) { m.invalidateSize(); });
        }, 300);
    });
    </script>
    """
    full_html = full_html.replace("</head>", _map_css + "</head>")
    full_html = full_html.replace("</body>", _fix_js + "</body>")
    full_html = inject_ui(full_html, sidebar_html)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(full_html)

    print(f"\nDashboard saved to: {OUTPUT_PATH}")
    print("Open in any browser — no server needed.")

if __name__ == "__main__":
    main()
