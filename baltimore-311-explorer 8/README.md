# Baltimore 311 Infrastructure Explorer

A civic data tool for identifying **chronic vs. acute** infrastructure problems in Baltimore City — and surfacing gaps between what gets reported and what the neighborhood data suggests should be getting reported.

## What This Does

- Pulls Baltimore 311 service request data from the Open Baltimore API
- Pulls Reddit posts from r/baltimore mentioning infrastructure issues
- Identifies **chronic hotspots**: locations with repeated reports over time
- Identifies **potential gaps**: areas with social media signal but low 311 activity
- Generates an interactive HTML map dashboard

## The Core Idea

A pothole reported 12 times in 18 months is a fundamentally different problem than a new one. 311 systems treat them the same. This tool surfaces the difference.

**Chronic problems** = same location, repeated reports, possibly failed fixes  
**Acute problems** = new reports, first-time issues  
**Gap areas** = Reddit/social signal without corresponding 311 reports (possible reporting barriers)

---

## Setup

### Requirements
- Python 3.9+
- A free Reddit API account (for the social scraping piece)

### Install dependencies
```bash
pip install -r requirements.txt
```

### Reddit API Setup (optional but recommended)
1. Go to https://www.reddit.com/prefs/apps
2. Create a new "script" type app
3. Copy your `client_id` and `client_secret`
4. Create a `.env` file in the project root:
```
REDDIT_CLIENT_ID=your_client_id
REDDIT_CLIENT_SECRET=your_client_secret
REDDIT_USER_AGENT=baltimore311explorer:v0.1 (by /u/yourusername)
```

---

## Usage

### Step 1: Pull 311 data
```bash
python scripts/fetch_311.py
```
Downloads recent Baltimore 311 requests and saves to `data/311_requests.csv`

### Step 2: Pull Reddit data (optional)
```bash
python scripts/fetch_reddit.py
```
Searches r/baltimore for infrastructure complaints, saves to `data/reddit_posts.csv`

### Step 3: Run analysis
```bash
python scripts/analyze.py
```
Identifies chronic hotspots and gap areas, saves results to `data/analysis_results.json`

### Step 4: Generate dashboard
```bash
python scripts/generate_dashboard.py
```
Creates `output/dashboard.html` — open this in any browser

---

## Project Structure
```
baltimore-311-explorer/
├── README.md
├── requirements.txt
├── .env.example
├── data/               # generated data files (gitignored)
├── output/             # generated dashboard (gitignored)
└── scripts/
    ├── fetch_311.py        # pulls Open Baltimore 311 data
    ├── fetch_reddit.py     # pulls r/baltimore posts
    ├── analyze.py          # chronic/gap analysis
    └── generate_dashboard.py  # builds HTML map
```

---

## Extending This

Some directions worth exploring:
- Add more service request types (broken streetlights, illegal dumping, etc.)
- Pull from additional sources (Nextdoor public posts, local news)
- Track resolution times by neighborhood to surface inequity in response
- Add temporal analysis — does the same spot get reported more after rain?
- Connect to city budget/maintenance records to see if chronic spots ever get capital investment

---

## Data Sources
- **311 Data**: [Open Baltimore](https://data.baltimorecity.gov/) — public domain
- **Reddit**: r/baltimore via Reddit API — public posts only
