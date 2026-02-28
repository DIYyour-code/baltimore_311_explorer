"""
fetch_reddit.py

Pulls posts and comments from r/baltimore mentioning infrastructure problems.
Uses PRAW (Python Reddit API Wrapper).

Requires a .env file with Reddit API credentials.
See .env.example for setup instructions.
"""

import os
import re
import json
import pandas as pd
import praw
from datetime import datetime
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'reddit_posts.csv')

# --- Search configuration ---

# Keywords to search for in r/baltimore
# Organized by infrastructure type
SEARCH_QUERIES = {
    "pothole": [
        "pothole",
        "potholes",
        "pothole damage",
        "hit a pothole",
        "tire pothole",
        "rim pothole",
    ],
    "streetlight": [
        "street light out",
        "streetlight out",
        "light out",
        "no street lights",
        "dark street",
    ],
    "sidewalk": [
        "broken sidewalk",
        "cracked sidewalk",
        "sidewalk damage",
        "tripped on sidewalk",
    ],
    "flooding": [
        "flooding street",
        "street flooding",
        "flooded road",
        "water main break",
        "water main",
    ],
    "cave_in": [
        "cave in",
        "sinkhole",
        "road collapsed",
        "street collapsed",
    ],
    "alley": [
        "broken alley",
        "alley damage",
        "alley flooding",
    ]
}

# Baltimore-specific location signals to help filter relevant posts
BALTIMORE_SIGNALS = [
    "baltimore", "bmore", "charm city",
    # Neighborhoods
    "canton", "fells point", "federal hill", "hampden", "charles village",
    "waverly", "reservoir hill", "bolton hill", "mount vernon", "roland park",
    "guilford", "homeland", "remington", "pigtown", "cherry hill", "brooklyn",
    "dundalk", "catonsville", "towson", "parkville", "overlea",
    # Streets
    "northern pkwy", "cold spring", "reisterstown rd", "edmondson", "pulaski",
    "belair rd", "harford rd", "york rd", "falls rd", "roland ave",
    "charles st", "maryland ave", "calvert st", "st paul st",
    "eastern ave", "O'donnell", "boston st",
    # General city signals
    "city council", "bpw", "dpw", "dot baltimore", "mayor",
]

# Patterns for extracting street/location mentions from text
STREET_PATTERNS = [
    r'\b(\d+\s+(?:block\s+of\s+)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s+(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|Way|Pkwy|Parkway|Ct|Court|Pl|Place))\b',
    r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s+(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|Way|Pkwy|Parkway))\b',
    r'\b((?:corner|intersection|near|at|on)\s+(?:of\s+)?[A-Z][a-z]+(?:\s+(?:and|&|\/)\s+[A-Z][a-z]+)?)\b',
]


def get_reddit_client():
    """Initialize Reddit client from environment variables."""
    client_id = os.getenv('REDDIT_CLIENT_ID')
    client_secret = os.getenv('REDDIT_CLIENT_SECRET')
    user_agent = os.getenv('REDDIT_USER_AGENT', 'baltimore311explorer:v0.1')
    
    if not client_id or not client_secret:
        raise ValueError(
            "Reddit API credentials not found.\n"
            "Copy .env.example to .env and fill in your credentials.\n"
            "Get credentials at: https://www.reddit.com/prefs/apps"
        )
    
    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
        read_only=True
    )


def extract_location_hints(text):
    """Try to extract street names or neighborhood mentions from post text."""
    locations = []
    
    for pattern in STREET_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        locations.extend(matches)
    
    # Also check for neighborhood name mentions
    text_lower = text.lower()
    for signal in BALTIMORE_SIGNALS:
        if signal in text_lower and signal not in ['baltimore', 'bmore', 'charm city']:
            locations.append(signal)
    
    return list(set(locations))


def score_damage_intensity(text):
    """
    Simple heuristic scoring for how severe the described problem seems.
    Returns 1-5.
    
    This is intentionally naive — a starting point for refinement.
    """
    text_lower = text.lower()
    score = 1
    
    # Damage indicators
    damage_words = ['damage', 'damaged', 'destroyed', 'broke', 'broken', 'bent', 
                    'flat tire', 'blowout', 'bent rim', 'alignment', 'suspension',
                    'repair bill', 'mechanic', 'tow truck', 'totaled']
    
    # Severity amplifiers  
    severe_words = ['horrible', 'terrible', 'dangerous', 'hazard', 'years', 'months',
                    'again', 'still', 'never fixed', 'keeps', 'every time', 'always']
    
    # Repetition signals (chronic problem indicators)
    chronic_words = ['years', 'every year', 'same pothole', 'same spot', 'been reported',
                     'reported before', 'nothing done', 'ignore', 'unfixed', 'still there']
    
    for word in damage_words:
        if word in text_lower:
            score += 0.5
    
    for word in severe_words:
        if word in text_lower:
            score += 0.5
            
    for word in chronic_words:
        if word in text_lower:
            score += 1  # chronic signals get extra weight
    
    return min(round(score), 5)


def is_baltimore_relevant(text):
    """Quick check that a post is actually about Baltimore."""
    text_lower = text.lower()
    return any(signal in text_lower for signal in ['baltimore', 'bmore', 'charm city', 'balt', ' md '])


def fetch_posts_for_query(reddit, subreddit, query, category, limit=100):
    """Fetch posts matching a search query."""
    posts = []
    
    try:
        results = subreddit.search(query, sort='new', time_filter='year', limit=limit)
        
        for post in results:
            full_text = f"{post.title} {post.selftext}"
            
            if not is_baltimore_relevant(full_text):
                continue
            
            location_hints = extract_location_hints(full_text)
            intensity = score_damage_intensity(full_text)
            
            posts.append({
                'post_id': post.id,
                'category': category,
                'search_query': query,
                'title': post.title,
                'text': post.selftext[:1000],  # truncate long posts
                'url': f"https://reddit.com{post.permalink}",
                'score': post.score,
                'num_comments': post.num_comments,
                'created_utc': datetime.utcfromtimestamp(post.created_utc),
                'location_hints': json.dumps(location_hints),
                'location_hint_count': len(location_hints),
                'damage_intensity_score': intensity,
                'is_chronic_signal': any(
                    w in full_text.lower() 
                    for w in ['years', 'months', 'same spot', 'again', 'still broken', 'never fixed']
                )
            })
    
    except Exception as e:
        print(f"  Warning: Error fetching '{query}': {e}")
    
    return posts


def main():
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    
    try:
        reddit = get_reddit_client()
    except ValueError as e:
        print(f"Error: {e}")
        return
    
    subreddit = reddit.subreddit('baltimore')
    all_posts = []
    
    print("Fetching r/baltimore posts...\n")
    
    for category, queries in SEARCH_QUERIES.items():
        print(f"Category: {category}")
        for query in tqdm(queries, desc=f"  searching", leave=False):
            posts = fetch_posts_for_query(reddit, subreddit, query, category)
            all_posts.extend(posts)
    
    if not all_posts:
        print("No posts fetched.")
        return
    
    df = pd.DataFrame(all_posts)
    
    # Deduplicate (same post might match multiple queries)
    df = df.drop_duplicates(subset='post_id')
    
    print(f"\n{'='*50}")
    print(f"REDDIT FETCH COMPLETE")
    print(f"{'='*50}")
    print(f"Total unique posts: {len(df):,}")
    print(f"\nBy category:")
    for cat, count in df['category'].value_counts().items():
        print(f"  {cat:<20} {count:>5,}")
    print(f"\nWith location hints: {(df['location_hint_count'] > 0).sum():,}")
    print(f"Chronic signals: {df['is_chronic_signal'].sum():,}")
    print(f"High intensity (4-5): {(df['damage_intensity_score'] >= 4).sum():,}")
    print(f"{'='*50}\n")
    
    df.to_csv(OUTPUT_PATH, index=False)
    print(f"Saved to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
