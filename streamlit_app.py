import os
from datetime import datetime, timezone
import requests
import pandas as pd
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="Last Man Standing", page_icon="🏈", layout="wide")

NFL_TEAMS = {
    "ARI":"Arizona Cardinals","ATL":"Atlanta Falcons","BAL":"Baltimore Ravens",
    "BUF":"Buffalo Bills","CAR":"Carolina Panthers","CHI":"Chicago Bears",
    "CIN":"Cincinnati Bengals","CLE":"Cleveland Browns","DAL":"Dallas Cowboys",
    "DEN":"Denver Broncos","DET":"Detroit Lions","GB":"Green Bay Packers",
    "HOU":"Houston Texans","IND":"Indianapolis Colts","JAX":"Jacksonville Jaguars",
    "KC":"Kansas City Chiefs","LV":"Las Vegas Raiders","LAC":"Los Angeles Chargers",
    "LAR":"Los Angeles Rams","MIA":"Miami Dolphins","MIN":"Minnesota Vikings",
    "NE":"New England Patriots","NO":"New Orleans Saints","NYG":"New York Giants",
    "NYJ":"New York Jets","PHI":"Philadelphia Eagles","PIT":"Pittsburgh Steelers",
    "SF":"San Francisco 49ers","SEA":"Seattle Seahawks","TB":"Tampa Bay Buccaneers",
    "TEN":"Tennessee Titans","WSH":"Washington Commanders",
}

# ---------- Database ----------
@st.cache_resource
def get_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

def db():
    return get_supabase()

def get_players():
    return db().table("players").select("*").order("name").execute().data

def get_picks():
    return db().table("picks").select("*").order("week").execute().data

def get_gameweek():
    row = db().table("settings").select("*").eq("key", "current_week").single().execute().data
    return int(row["value"])

def set_gameweek(week):
    db().table("settings").update({"value": str(week)}).eq("key", "current_week").execute()

# ---------- ESPN schedule scraping ----------
@st.cache_data(ttl=60)
def get_nfl_schedule(week: int, season_type: int, year: int = 2026):
    """Scrape ESPN schedule page for fixtures."""
    url = f"https://www.espn.co.uk/nfl/schedule/_/week/{week}/year/{year}/seasontype/{season_type}"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    tables = pd.read_html(r.text)
    if not tables:
        return pd.DataFrame()
    df = tables[0]
    df.columns = [c if not isinstance(c, tuple) else c[1] for c in df.columns]
    return df

def current_games(week: int, season_type: int):
    """Convert ESPN schedule table to structured game list."""
    df = get_nfl_schedule(week, season_type)
    rows = []
    for _, row in df.iterrows():
        if "@" not in row["MATCHUP"]:
            continue
        away, home = [x.strip() for x in row["MATCHUP"].split("@")]
        rows.append({
            "home_name": home,
            "away_name": away,
            "home": next((k for k, v in NFL_TEAMS.items() if v == home), home[:3].upper()),
            "away": next((k for k, v in NFL_TEAMS.items() if v == away), away[:3].upper()),
            "state": "pre",
            "status": "Scheduled",
            "completed": False,
            "date": str(datetime.now(timezone.utc).date()),
        })
    return pd.DataFrame(rows)

# ---------- Game logic ----------
def player_status(player_id, picks):
    p = [x for x in picks if x["player_id"] == player_id]
    for pick in sorted(p, key=lambda x: x["week"]):
        if pick.get("result") == "loss":
            return "Out"
    return "Active"

def validate_pick(player_id, team, week, picks, games):
    player_picks = [x for x in picks if x["player_id"] == player_id]
    if any(x["week"] == week for x in player_picks):
        return False, "You already have a pick for this week."
    if any(x["team"] == team for x in player_picks):
        return False, "You have already picked this team."
    if any(x["opponent"] == team for x in player_picks if x["opponent"]):
        return False, "You have already opposed this team."
    opponent = None
    for g in games:
        if g["home"] == team:
            opponent = g["away"]
        elif g["away"] == team:
            opponent = g["home"]
    return True, opponent

def settle_completed_picks(picks, games):
    game_by_team = {}
    for g in games:
        game_by_team[g["home"]] = g
        game_by_team[g["away"]] = g
    for p in picks:
        if p.get("result") not in (None, "pending"):
            continue
        g = game_by_team.get(p["team"])
        if not g or not g["completed"]:
            continue
        result = "win" if g["winner"] == p["team"] else "loss"
        db().table("picks").update({
            "result": result,
            "settled_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", p["id"]).execute()

# ---------- UI ----------
st.title("🏈 Last Man Standing")
st.caption("Pick an NFL team each week. Win and you advance. Lose and you're out.")

try:
    players = get_players()
    picks = get_picks()
    week = get_gameweek()

    # Preseason = 1, Regular season = 2
    season_type = 1 if week < 4 else 2
    games_df = current_games(week, season_type)
    games = games_df.to_dict("records")

    settle_completed_picks(picks, games)
    if any(x.get("result") in (None, "pending") for x in picks):
        picks = get_picks()

except Exception as e:
    st.error("The app could not connect to the database. Check your Supabase secrets and run schema.sql.")
    st.exception(e)
    st.stop()

# Sidebar
names = [p["name"] for p in players]
if "player_name" not in st.session_state:
    st.session_state.player_name = None

with st.sidebar:
    st.header("Player")
    if names:
        selected = st.selectbox("Who are you?", ["— Select —"] + names)
        if selected != "— Select —":
            st.session_state.player_name = selected
    if st.session_state.player_name:
        st.success(f"Logged in as {st.session_state.player_name}")
    st.divider()
    st.caption(f"Current gameweek: **{week}**")
    if st.button("🔄 Refresh schedule"):
        st.cache_data.clear()
        st.rerun()

# Tabs
tab1, tab2, tab3 = st.tabs(["📊 Picks & History", "📅 Upcoming Games", "⚙️ Admin"])

# ---------- TAB 2: Upcoming + Picks ----------
with tab2:
    st.subheader(f"Week {week} schedule")
    if games_df.empty:
        st.info("No games found for this week.")
    else:
        display = []
        for g in games:
            display.append({
                "Game": f"{g['away_name']} @ {g['home_name']}",
                "Status": g["status"],
            })
        st.dataframe(pd.DataFrame(display), use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Your pick")

    current_player = next((p for p in players if p["name"] == st.session_state.player_name), None)
    if not current_player:
        st.info("Select your name in the sidebar first.")
    elif player_status(current_player["id"], picks) == "Out":
        st.error("You are eliminated from the game.")
    elif any(x["week"] == week and x["player_id"] == current_player["id"] for x in picks):
        pk = next(x for x in picks if x["week"] == week and x["player_id"] == current_player["id"])
        st.success(f"Your Week {week} pick is **{NFL_TEAMS.get(pk['team'], pk['team'])}**.")
    else:
        available = []
        player_picks = [x for x in picks if x["player_id"] == current_player["id"]]
        used = {x["team"] for x in player_picks}
        opposed = {x["opponent"] for x in player_picks if x["opponent"]}
        for g in games:
            for team in (g["home"], g["away"]):
                if team not in used and team not in opposed:
                    available.append((team, NFL_TEAMS[team]))
        available = sorted(set(available), key
