# 🏈 Last Man Standing — Streamlit + Hugging Face + Supabase

A private NFL Last Man Standing league app.

## Architecture

- Hugging Face Spaces: hosts the Streamlit app
- Supabase: stores players, picks and settings
- ESPN public scoreboard endpoint: live/current NFL scores

## Rules

- One NFL team per player per week.
- Win = survive.
- Loss = eliminated.
- Overtime is included automatically because the final game winner is used.
- A team cannot be picked more than once by the same player.
- A team cannot be opposed more than once by the same player.
- A team cannot be selected if its opponent has previously been picked or opposed.
- Picks are locked once submitted.
- Results are automatically settled after the game is final.

## Setup

### 1. Supabase

Create a project at Supabase and open SQL Editor.

Run `schema.sql`.

Supabase's Python client uses the Data API to query and mutate Postgres data, so the tables need to be available through the Data API.

### 2. Hugging Face

Create a new Space.

Choose:

- SDK: Docker
- Hardware: free/default CPU
- Visibility: Public

Hugging Face no longer offers Streamlit as a built-in Space SDK; Streamlit apps should use the Docker template.

Upload:

- app.py
- Dockerfile
- requirements.txt
- schema.sql
- README.md

### 3. Hugging Face Secrets

In Space Settings → Secrets, create:

SUPABASE_URL = your Supabase project URL
SUPABASE_KEY = your Supabase publishable/anon key
NFL_SEASON = 2026

Do NOT put these values in the source code.

### 4. First launch

Open the Space URL.

Select a player name in the sidebar.

To configure yourself as admin:

1. Add your name as a player.
2. Select your name.
3. Open Admin.
4. Enter your name into Admin name.
5. Set the current week.
6. Save.

### 5. Important note about the score source

The app currently uses ESPN's public scoreboard endpoint. This is suitable for a private hobby project, but check the source's terms if you later turn the app into a commercial product.

## Troubleshooting

If the Space fails to start:

- Check the Build Logs.
- Confirm the Space is using Docker.
- Confirm `app.py` and `Dockerfile` are in the repository root.
- Confirm `SUPABASE_URL` and `SUPABASE_KEY` are set as Space Secrets.
- Confirm the Supabase tables were created from schema.sql.

If no games appear, check that the admin current week and `NFL_SEASON` match the NFL season/week you intend to run.
