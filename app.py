import os
from datetime import datetime, timezone
import requests
import pandas as pd
import streamlit as st
from supabase import create_client

# ----------------------------
# App configuration
# ----------------------------
st.set_page_config(
    page_title="Last Man Standing",
    page_icon="🏈",
    layout="wide",
    initial_sidebar_state="expanded",
)

SEASON = int(st.secrets.get("NFL_SEASON", os.getenv("NFL_SEASON", "2026")))
SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"

TEAMS = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons",
    "BAL": "Baltimore Ravens", "BUF": "Buffalo Bills",
    "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns",
    "DAL": "Dallas Cowboys", "DEN": "Denver Broncos",
    "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts",
    "JAX": "Jacksonville Jaguars", "KC": "Kansas City Chiefs",
    "LV": "Las Vegas Raiders", "LAC": "Los Angeles Chargers",
    "LAR": "Los Angeles Rams", "MIA": "Miami Dolphins",
    "MIN": "Minnesota Vikings", "NE": "New England Patriots",
    "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles",
    "PIT": "Pittsburgh Steelers", "SF": "San Francisco 49ers",
    "SEA": "Seattle Seahawks", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}

@st.cache_resource
def supabase_client():
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_KEY"],
    )

db = supabase_client()

# ----------------------------
# Database helpers
# ----------------------------
def get_setting(key, default=None):
    result = db.table("settings").select("value").eq("key", key).maybe_single().execute()
    return result.data["value"] if result.data else default

def set_setting(key, value):
    db.table("settings").upsert({"key": key, "value": str(value)}).execute()

def current_week():
    return int(get_setting("current_week", "1"))

def is_admin_name(name):
    return name and name.lower() == get_setting("admin_name", "").lower()

def get_players():
    return db.table("players").select("*").order("name").execute().data or []

def get_picks():
    return db.table("picks").select("*").order("week").order("created_at").execute().data or []

def add_player(name):
    db.table("players").insert({"name": name.strip()}).execute()

def delete_player(player_id):
    db.table("players").delete().eq("id", player_id).execute()

def insert_pick(player_id, week, team, opponent):
    db.table("picks").insert({
        "player_id": player_id,
        "week": week,
        "team": team,
        "opponent": opponent,
        "result": "pending",
    }).execute()

def update_pick_result(pick_id, result):
    db.table("picks").update({
        "result": result,
        "settled_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", pick_id).execute()

# ----------------------------
# ESPN data
# ----------------------------
@st.cache_data(ttl=30)
def fetch_week_games(season, week):
    params = {
        "dates": season,
        "seasontype": 2,
        "week": week,
    }
    response = requests.get(SCOREBOARD_URL, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()

    games = []
    for event in data.get("events", []):
        comp = event["competitions"][0]
        competitors = comp["competitors"]
        home = next(x for x in competitors if x["homeAway"] == "home")
        away = next(x for x in competitors if x["homeAway"] == "away")
        status = comp["status"]["type"]

        winner = None
        for c in competitors:
            if c.get("winner"):
                winner = c["team"]["abbreviation"]

        games.append({
            "event_id": event["id"],
            "kickoff": event["date"],
            "home": home["team"]["abbreviation"],
            "away": away["team"]["abbreviation"],
            "home_name": home["team"]["displayName"],
            "away_name": away["team"]["displayName"],
            "home_score": int(home.get("score", 0) or 0),
            "away_score": int(away.get("score", 0) or 0),
            "state": status["state"],
            "status": status["shortDetail"],
            "completed": bool(status["completed"]),
            "winner": winner,
        })
    return games

# ----------------------------
# LMS logic
# ----------------------------
def player_status(player_id, picks):
    player_picks = [p for p in picks if p["player_id"] == player_id]
    if any(p["result"] == "loss" for p in player_picks):
        return "OUT"
    return "ACTIVE"

def player_week_pick(player_id, week, picks):
    return next((p for p in picks if p["player_id"] == player_id and p["week"] == week), None)

def used_teams(player_id, picks):
    return {p["team"] for p in picks if p["player_id"] == player_id}

def opposed_teams(player_id, picks):
    return {p["opponent"] for p in picks if p["player_id"] == player_id and p["opponent"]}

def settle_picks(picks, games):
    game_by_team = {}
    for game in games:
        game_by_team[game["home"]] = game
        game_by_team[game["away"]] = game

    changed = False
    for pick in picks:
        if pick["result"] != "pending":
            continue
        game = game_by_team.get(pick["team"])
        if not game or not game["completed"]:
            continue

        result = "win" if game["winner"] == pick["team"] else "loss"
        update_pick_result(pick["id"], result)
        changed = True

    return changed

def pick_eligible(player_id, team, week, picks, games):
    player_picks = [p for p in picks if p["player_id"] == player_id]

    if any(p["week"] == week for p in player_picks):
        return False, "You already have a pick for this week.", None

    if team in used_teams(player_id, picks):
        return False, "You have already picked this team in an earlier week.", None

    if team in opposed_teams(player_id, picks):
        return False, "You have already opposed this team in an earlier week.", None

    game = next((g for g in games if team in (g["home"], g["away"])), None)
    if not game:
        return False, "That team does not have a game in the selected week.", None

    opponent = game["away"] if game["home"] == team else game["home"]

    if opponent in used_teams(player_id, picks):
        return False, f"You previously picked {TEAMS[opponent]}, so you cannot pick {TEAMS[team]} this week.", opponent

    if opponent in opposed_teams(player_id, picks):
        return False, f"You previously opposed {TEAMS[opponent]}, so you cannot pick {TEAMS[team]} this week.", opponent

    return True, "", opponent

def game_state_label(game):
    if game["completed"]:
        return "FINAL"
    if game["state"] == "in":
        return "LIVE"
    return game["status"]

def format_score(game):
    return f'{game["away_score"]} - {game["home_score"]}'

# ----------------------------
# Styling
# ----------------------------
st.markdown("""
<style>
.block-container { padding-top: 1.5rem; }
.lms-card {
    border: 1px solid rgba(128,128,128,.25);
    border-radius: 14px;
    padding: 18px;
    margin-bottom: 12px;
}
.lms-title { font-size: 2.2rem; font-weight: 750; }
.lms-muted { opacity: .72; }
.lms-green { color: #188038; font-weight: 700; }
.lms-red { color: #d93025; font-weight: 700; }
.lms-yellow { color: #b06000; font-weight: 700; }
</style>
""", unsafe_allow_html=True)

# ----------------------------
# Load state
# ----------------------------
players = get_players()
picks = get_picks()
week = current_week()
games = fetch_week_games(SEASON, week)

if settle_picks(picks, games):
    picks = get_picks()

# ----------------------------
# Sidebar / player selection
# ----------------------------
st.sidebar.markdown("## 🏈 Last Man Standing")
st.sidebar.caption(f"NFL {SEASON} · Week {week}")

names = [p["name"] for p in players]
default_index = 0
if st.session_state.get("player_name") in names:
    default_index = names.index(st.session_state["player_name"]) + 1

selected = st.sidebar.selectbox(
    "Who are you?",
    ["Select your name"] + names,
    index=default_index,
)
if selected != "Select your name":
    st.session_state["player_name"] = selected

if st.sidebar.button("🔄 Refresh"):
    st.cache_data.clear()
    st.rerun()

current_player = next(
    (p for p in players if p["name"] == st.session_state.get("player_name")),
    None
)

st.sidebar.divider()
st.sidebar.caption("Scores refresh automatically when the page is refreshed.")

# ----------------------------
# Header
# ----------------------------
active_count = sum(player_status(p["id"], picks) == "ACTIVE" for p in players)
out_count = len(players) - active_count

st.markdown('<div class="lms-title">🏈 LAST MAN STANDING</div>', unsafe_allow_html=True)
st.caption(f"NFL {SEASON} · Week {week}")

m1, m2, m3 = st.columns(3)
m1.metric("Players remaining", active_count)
m2.metric("Eliminated", out_count)
m3.metric("Games this week", len(games))

tabs = st.tabs(["🏠 Dashboard", "🎯 Make Pick", "📊 History", "🔴 Live Scores", "⚙️ Admin"])

# ----------------------------
# Dashboard
# ----------------------------
with tabs[0]:
    st.subheader(f"Week {week}")

    if not players:
        st.info("No players have been added yet. Use Admin to add the league members.")
    else:
        standings = []
        for p in players:
            pp = player_week_pick(p["id"], week, picks)
            status = player_status(p["id"], picks)
            total_wins = sum(
                x["result"] == "win" for x in picks if x["player_id"] == p["id"]
            )
            standings.append({
                "Player": p["name"],
                "Pick": TEAMS.get(pp["team"], pp["team"]) if pp else "—",
                "Result": pp["result"].upper() if pp else "NO PICK",
                "Status": status,
                "Weeks survived": total_wins,
            })

        df = pd.DataFrame(standings)

        def colour_status(value):
            if value == "ACTIVE":
                return "color: #188038; font-weight: 700"
            if value == "OUT":
                return "color: #d93025; font-weight: 700"
            return ""

        st.dataframe(
            df.style.map(colour_status, subset=["Status"]),
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("This week's games")
    if games:
        game_cols = st.columns(2)
        for i, game in enumerate(games):
            with game_cols[i % 2]:
                picks_on_game = [
                    p for p in picks
                    if p["week"] == week and p["team"] in (game["home"], game["away"])
                ]
                pick_names = [
                    next((x["name"] for x in players if x["id"] == p["player_id"]), "?")
                    for p in picks_on_game
                ]
                st.markdown('<div class="lms-card">', unsafe_allow_html=True)
                st.markdown(f"**{game['away_name']} @ {game['home_name']}**")
                st.markdown(f"### {format_score(game)}")
                st.caption(game_state_label(game))
                if pick_names:
                    st.markdown("🎯 **LMS:** " + ", ".join(pick_names))
                else:
                    st.caption("No LMS picks")
                st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.info("No games were returned for this week.")

# ----------------------------
# Make Pick
# ----------------------------
with tabs[1]:
    st.subheader(f"🎯 Make your Week {week} pick")

    if not current_player:
        st.info("Select your name in the sidebar.")
    elif player_status(current_player["id"], picks) == "OUT":
        st.error("You are eliminated and cannot make another pick.")
    elif player_week_pick(current_player["id"], week, picks):
        existing = player_week_pick(current_player["id"], week, picks)
        result = existing["result"].upper()
        icon = "🟢" if result == "WIN" else "🔴" if result == "LOSS" else "🟡"
        st.success(f"{icon} Your pick is locked: **{TEAMS.get(existing['team'], existing['team'])}**")
    elif not games:
        st.warning("There are no games available for the current gameweek.")
    else:
        used = used_teams(current_player["id"], picks)
        opposed = opposed_teams(current_player["id"], picks)

        available = []
        for game in games:
            for team in (game["home"], game["away"]):
                if team in used or team in opposed:
                    continue
                opponent = game["away"] if game["home"] == team else game["home"]
                if opponent in used or opponent in opposed:
                    continue
                available.append({
                    "team": team,
                    "opponent": opponent,
                    "game": game,
                })

        available = sorted(available, key=lambda x: TEAMS[x["team"]])

        if not available:
            st.warning("You have no eligible teams remaining this week.")
        else:
            labels = [
                f"{TEAMS[x['team']]} — vs {TEAMS[x['opponent']]}"
                for x in available
            ]
            choice = st.selectbox("Available teams", labels)
            selected_game = available[labels.index(choice)]

            st.markdown('<div class="lms-card">', unsafe_allow_html=True)
            st.markdown(f"### {TEAMS[selected_game['team']]}")
            st.write(
                f"**Opponent:** {TEAMS[selected_game['opponent']]}  \n"
                f"**Status:** {game_state_label(selected_game['game'])}  \n"
                f"**Score:** {format_score(selected_game['game'])}"
            )
            st.markdown("</div>", unsafe_allow_html=True)

            st.warning("Your pick is permanent once submitted.")

            if st.button("🔒 CONFIRM PICK", type="primary", use_container_width=True):
                ok, message, opponent = pick_eligible(
                    current_player["id"],
                    selected_game["team"],
                    week,
                    picks,
                    games,
                )
                if not ok:
                    st.error(message)
                else:
                    insert_pick(
                        current_player["id"],
                        week,
                        selected_game["team"],
                        opponent,
                    )
                    st.success(f"Pick locked: {TEAMS[selected_game['team']]}")
                    st.rerun()

# ----------------------------
# History
# ----------------------------
with tabs[2]:
    st.subheader("📊 Pick history")

    if picks:
        all_weeks = list(range(1, max([week] + [p["week"] for p in picks]) + 1))
        history_rows = []

        for player in players:
            row = {"Player": player["name"]}
            player_picks = {p["week"]: p for p in picks if p["player_id"] == player["id"]}

            for w in all_weeks:
                p = player_picks.get(w)
                if not p:
                    row[f"W{w}"] = "—"
                else:
                    result = p["result"]
                    icon = "🟢" if result == "win" else "🔴" if result == "loss" else "🟡"
                    row[f"W{w}"] = f"{icon} {TEAMS.get(p['team'], p['team'])}"

            row["Status"] = player_status(player["id"], picks)
            history_rows.append(row)

        st.dataframe(
            pd.DataFrame(history_rows),
            use_container_width=True,
            hide_index=True,
        )

        st.caption("🟢 survived · 🔴 eliminated · 🟡 pending")

        st.subheader("Team usage")
        usage = []
        for code, name in TEAMS.items():
            picked = sum(p["team"] == code for p in picks)
            opposed = sum(p["opponent"] == code for p in picks)
            if picked or opposed:
                usage.append({
                    "Team": name,
                    "Picked": picked,
                    "Opposed": opposed,
                    "Total usage": picked + opposed,
                })
        if usage:
            st.dataframe(
                pd.DataFrame(usage).sort_values(
                    ["Total usage", "Team"], ascending=[False, True]
                ),
                use_container_width=True,
                hide_index=True,
            )
    else:
        st.info("No picks have been submitted yet.")

# ----------------------------
# Live scores
# ----------------------------
with tabs[3]:
    st.subheader(f"🔴 Live Scores — Week {week}")
    st.caption("The score feed refreshes every 30 seconds when the page requests fresh data.")

    week_picks = [p for p in picks if p["week"] == week]

    if not games:
        st.info("No games are currently returned for this week.")
    else:
        for game in games:
            game_picks = [
                p for p in week_picks
                if p["team"] in (game["home"], game["away"])
            ]

            st.markdown('<div class="lms-card">', unsafe_allow_html=True)
            c1, c2, c3 = st.columns([3, 2, 3])

            with c1:
                st.markdown(f"**{game['away_name']}**")
                st.markdown(f"## {game['away_score']}")

            with c2:
                state = game_state_label(game)
                if state == "LIVE":
                    st.markdown("### 🔴 LIVE")
                elif state == "FINAL":
                    st.markdown("### 🏁 FINAL")
                else:
                    st.markdown(f"### {state}")

            with c3:
                st.markdown(f"**{game['home_name']}**")
                st.markdown(f"## {game['home_score']}")

            if game_picks:
                for p in game_picks:
                    pname = next((x["name"] for x in players if x["id"] == p["player_id"]), "?")
                    if p["result"] == "win":
                        label = "🟢 WIN"
                    elif p["result"] == "loss":
                        label = "🔴 OUT"
                    elif game["completed"]:
                        label = "🔴 OUT"
                    elif game["state"] == "in":
                        label = "🟡 LIVE"
                    else:
                        label = "🟡 PENDING"
                    st.write(f"**{pname}** — {TEAMS[p['team']]} — {label}")

            st.markdown("</div>", unsafe_allow_html=True)

# ----------------------------
# Admin
# ----------------------------
with tabs[4]:
    admin_name = st.session_state.get("player_name")
    if not is_admin_name(admin_name):
        st.info("Admin controls are available to the league administrator.")
        st.caption("Select the configured admin name in the sidebar.")
    else:
        st.subheader("⚙️ League Admin")

        st.markdown("### League settings")
        new_week = st.number_input(
            "Current gameweek",
            min_value=1,
            max_value=22,
            value=week,
            step=1,
        )
        admin_name_input = st.text_input(
            "Admin name",
            value=get_setting("admin_name", ""),
        )

        if st.button("Save league settings"):
            set_setting("current_week", int(new_week))
            set_setting("admin_name", admin_name_input.strip())
            st.success("Settings saved.")
            st.rerun()

        st.divider()
        st.markdown("### Add player")
        with st.form("add_player"):
            new_name = st.text_input("Player name")
            submitted = st.form_submit_button("Add player")
            if submitted:
                clean = new_name.strip()
                if not clean:
                    st.error("Enter a name.")
                elif any(p["name"].lower() == clean.lower() for p in players):
                    st.error("That player already exists.")
                else:
                    add_player(clean)
                    st.success(f"Added {clean}.")
                    st.rerun()

        st.markdown("### Players")
        for p in players:
            c1, c2 = st.columns([5, 1])
            c1.write(p["name"])
            if c2.button("Remove", key=f"remove_{p['id']}"):
                delete_player(p["id"])
                st.rerun()

        st.divider()
        st.markdown("### Manual result correction")
        st.caption("Use this only if the external score feed needs correcting.")
        for p in picks:
            pname = next((x["name"] for x in players if x["id"] == p["player_id"]), "?")
            c1, c2, c3, c4 = st.columns([2, 1, 3, 2])
            c1.write(pname)
            c2.write(f"W{p['week']}")
            c3.write(TEAMS.get(p["team"], p["team"]))
            if c4.button("Set loss", key=f"loss_{p['id']}"):
                update_pick_result(p["id"], "loss")
                st.rerun()

        st.divider()
        st.markdown("### Data")
        st.json({
            "season": SEASON,
            "current_week": week,
            "players": len(players),
            "picks": len(picks),
            "games_returned": len(games),
        })
