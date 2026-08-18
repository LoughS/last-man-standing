import os
from datetime import datetime, timezone, timedelta
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

# Adjust these if actual dates differ
PRESEASON_START = datetime(2026, 8, 8, tzinfo=timezone.utc)   # preseason week 1
REGULAR_START   = datetime(2026, 9, 10, tzinfo=timezone.utc)  # regular season week 1

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

# ---------- ESPN scoreboard with automatic week ranges ----------
def week_date_range(week: int, season_type: int):
    """
    season_type: 1 = preseason, 2 = regular
    Returns (start_date_str, end_date_str) as YYYYMMDD.
    """
    if season_type == 1:
        base = PRESEASON_START
    else:
        base = REGULAR_START
    start = base + timedelta(weeks=week - 1)
    end = start + timedelta(days=6)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

@st.cache_data(ttl=60)
def get_nfl_scoreboard(season_type: int, week: int, year: int = 2026):
    start_str, end_str = week_date_range(week, season_type)
    url = (
        "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
        f"?dates={start_str}-{end_str}&seasontype={season_type}&year={year}"
    )
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json()

def current_games(season_type: int, week: int):
    data = get_nfl_scoreboard(season_type, week)
    rows = []
    for event in data.get("events", []):
        # Filter to correct week and season type
        ev_season = event.get("season", {})
        ev_week = event.get("week", {})
        if ev_season.get("type") != season_type:
            continue
        if ev_week.get("number") != week:
            continue

        comp = event["competitions"][0]
        competitors = comp["competitors"]
        home = next(x for x in competitors if x["homeAway"] == "home")
        away = next(x for x in competitors if x["homeAway"] == "away")
        status = comp["status"]["type"]
        rows.append({
            "event_id": event["id"],
            "home": home["team"]["abbreviation"],
            "away": away["team"]["abbreviation"],
            "home_name": home["team"]["displayName"],
            "away_name": away["team"]["displayName"],
            "home_score": int(home.get("score", 0)),
            "away_score": int(away.get("score", 0)),
            "state": status["state"],          # "pre", "in", "post"
            "status": status["shortDetail"],
            "completed": status["completed"],
            "winner": next((x["team"]["abbreviation"] for x in competitors if x.get("winner")), None),
            "date": event["date"],
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

    games_df = current_games(season_type, week)
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
    else:
        st.info("No players have been created yet.")

    if st.session_state.player_name:
        st.success(f"Logged in as {st.session_state.player_name}")

    st.divider()
    st.caption(f"Current gameweek: **{week}**")
    if st.button("🔄 Refresh scores"):
        st.cache_data.clear()
        st.rerun()

# Tabs
tab1, tab2, tab3 = st.tabs(["📊 Picks & History", "🔴 Live This Week", "⚙️ Admin"])

# ---------- TAB 1: Picks & History ----------
with tab1:
    st.subheader("Game history")
    if not players:
        st.info("Add players in the Admin tab.")
    else:
        weeks = sorted({x["week"] for x in picks} | set(range(1, week + 1)))
        rows = []
        for p in players:
            row = {"Player": p["name"]}
            player_picks = {x["week"]: x for x in picks if x["player_id"] == p["id"]}
            for w in weeks:
                pk = player_picks.get(w)
                if not pk:
                    row[f"W{w}"] = "—"
                else:
                    team = NFL_TEAMS.get(pk["team"], pk["team"])
                    result = pk.get("result")
                    icon = "🟢" if result == "win" else "🔴" if result == "loss" else "🟡"
                    row[f"W{w}"] = f"{icon} {team}"
            row["Status"] = player_status(p["id"], picks)
            rows.append(row)
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.markdown("**Legend:** 🟢 survived · 🔴 eliminated · 🟡 pending")

        st.subheader("Current standings")
        standings = []
        for p in players:
            pp = [x for x in picks if x["player_id"] == p["id"]]
            wins = sum(x.get("result") == "win" for x in pp)
            standings.append({
                "Player": p["name"],
                "Status": player_status(p["id"], picks),
                "Weeks survived": wins,
                "Current pick": next((NFL_TEAMS.get(x["team"], x["team"]) for x in pp if x["week"] == week), "No pick"),
            })
        st.dataframe(
            pd.DataFrame(standings).sort_values(["Status", "Weeks survived"], ascending=[True, False]),
            use_container_width=True,
            hide_index=True,
        )

# ---------- TAB 2: Live + Picks ----------
with tab2:
    st.subheader(f"Live scores — Week {week}")
    if games_df.empty:
        st.info("No NFL games are currently on the scoreboard for this week.")
    else:
        week_picks = [x for x in picks if x["week"] == week]
        pick_by_team = {}
        for p in players:
            for pk in week_picks:
                if pk["player_id"] == p["id"]:
                    pick_by_team.setdefault(pk["team"], []).append(p["name"])

        display = []
        for g in games:
            picked_by = ", ".join(pick_by_team.get(g["home"], []) + pick_by_team.get(g["away"], []))
            display.append({
                "Game": f"{g['away_name']} @ {g['home_name']}",
                "Score": f"{g['away_score']} - {g['home_score']}",
                "Status": g["status"],
                "LMS Pick": picked_by or "—",
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

        # Only offer teams in non-completed games for this week
        for g in games:
            if g["completed"]:
                continue
            for team in (g["home"], g["away"]):
                if team not in used and team not in opposed:
                    available.append((team, NFL_TEAMS.get(team, team)))

        available = sorted(set(available), key=lambda x: x[1])
        if not available:
            st.warning("No eligible teams available for this week.")
        else:
            team_names = {label: code for code, label in available}
            choice = st.selectbox("Choose your team", list(team_names.keys()))
            if st.button("Submit pick", type="primary"):
                team = team_names[choice]
                ok, opponent = validate_pick(current_player["id"], team, week, picks, games)
                if not ok:
                    st.error(ok)
                else:
                    db().table("picks").insert({
                        "player_id": current_player["id"],
                        "week": week,
                        "team": team,
                        "opponent": opponent,
                        "result": "pending",
                    }).execute()
                    st.success(f"Pick saved: {choice}")
                    st.rerun()

# ---------- TAB 3: Admin ----------
with tab3:
    st.subheader("League administration")

    st.markdown("### Add a player")
    with st.form("add_player"):
        new_name = st.text_input("Player name")
        add = st.form_submit_button("Add player")
        if add and new_name.strip():
            if any(p["name"].lower() == new_name.strip().lower() for p in players):
                st.error("That player already exists.")
            else:
                db().table("players").insert({"name": new_name.strip()}).execute()
                st.success("Player added.")
                st.rerun()

    st.markdown("### Gameweek")
    new_week = st.number_input("Current gameweek", min_value=1, max_value=22, value=week, step=1)
    if st.button("Save gameweek"):
        set_gameweek(int(new_week))
        st.success(f"Current gameweek set to {int(new_week)}.")
        st.rerun()

    st.markdown("### Existing picks")
    if picks:
        admin_df = pd.DataFrame([{
            "Player": next((p["name"] for p in players if p["id"] == x["player_id"]), x["player_id"]),
            "Week": x["week"],
            "Team": NFL_TEAMS.get(x["team"], x["team"]),
            "Opponent": NFL_TEAMS.get(x["opponent"], x["opponent"]) if x["opponent"] else "Unknown",
            "Result": x.get("result"),
        } for x in picks])
        st.dataframe(admin_df, use_container_width=True, hide_index=True)

st.caption("Scores and fixtures are supplied by ESPN's public NFL scoreboard API with week-based date ranges.")
