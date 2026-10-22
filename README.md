---
title: FC Mobile Tournament Bot
emoji: ⚽
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
---

# FC Mobile Discord Tournament Bot

A Discord bot built with `discord.py` to automate and run tournaments for **FC Mobile** (FIFA Mobile), complete with knockout bracket progression, fuzzy player name matching, and automated match result extraction via OCR on multi-leg match screenshots.

---

## 🚀 Free 24/7 Hosting on Hugging Face Spaces (Docker)

Hugging Face Spaces provides **2 dedicated vCPUs + 16GB RAM for $0** with zero credit card required. Follow these steps to host:

### 1. Create your Space on Hugging Face
1. Sign up / Log in to [Hugging Face](https://huggingface.co).
2. Go to **Spaces** → Click **Create new Space**.
3. Choose a Space name (e.g. `fc-mobile-tournament-bot`).
4. Select **Docker** (Blank) as the Space SDK.
5. Set Space hardware to **CPU basic (2 vCPU · 16GB RAM - Free)**.
6. Set visibility to **Public** or **Private** and click **Create Space**.

### 2. Add Your Bot Token Secret
1. In your newly created Space, navigate to **Settings** → **Variables and secrets**.
2. Under **New secret**, add:
   - **Name**: `BOT_TOKEN`
   - **Value**: *(Your Discord Bot Token from the Discord Developer Portal)*
3. *(Optional)* Add other configuration secrets if desired:
   - `OCR_CONFIDENCE_THRESHOLD`: `70`
   - `FUZZY_MATCH_THRESHOLD`: `65`

### 3. Deploy the Code
You can push the files to your Hugging Face Space repository using Git:

```bash
# In your local project folder:
git add .
git commit -m "Deploy FC Mobile Tournament Bot to Hugging Face Spaces"
git branch -M main
git remote add space https://huggingface.co/spaces/<YOUR_USERNAME>/<YOUR_SPACE_NAME>
git push -u space main --force
```
*(When prompted for credentials, use your Hugging Face username and an Access Token from huggingface.co/settings/tokens as your password).*

Once pushed, Hugging Face will automatically build the Docker image, pre-cache the OCR models, and launch your bot!

### 4. Keep the Bot Awake 24/7 (Prevent Sleep)
Free Hugging Face Spaces enter sleep mode after 48 hours of inactivity if no web requests are received. Your bot includes a built-in health check web server on port `7860` (`/health`).

To keep your bot running 24/7 forever:
1. Copy the Direct URL of your space:
   - In Hugging Face, click the three dots (`...`) at top right → **Embed this Space** or check your space URL (format: `https://<username>-<space-name>.hf.space`).
2. Go to [UptimeRobot.com](https://uptimerobot.com) (100% Free).
3. Create a free monitor:
   - **Monitor Type**: `HTTP(s)`
   - **Friendly Name**: `FC Mobile Bot`
   - **URL (or IP)**: `https://<username>-<space-name>.hf.space/health`
   - **Monitoring Interval**: Every 5 or 10 minutes.
4. Done! UptimeRobot will ping the bot periodically, ensuring Hugging Face never puts it to sleep.

---

## 🎮 Features

- **General Commands (Available to Everyone)**:
  - `/help` — Overview of all available commands, usage guides, and instructions.
  - `/tournament_info` — Overview of active tournament (name, status, teams).
  - `/add_member team_number:<int> player:@mention ign:<str>` — Register players and their FC Mobile In-Game Name (IGN) to a team.
  - `/view_team team_number:<int>` — View roster and seed of a team.
  - `/view_all_teams` — View all teams and their registered players.
  - `/brackets` / `/view_bracket` — View knockout rounds with both visual graphic brackets and embedded match tables.

- **Automated Results via OCR**:
  - Mention two players in chat with match screenshots attached:
    ```
    @Player1 vs @Player2
    [Attachment: Screenshot Leg 1]
    [Attachment: Screenshot Leg 2]
    ...
    ```
  - Supports 2, 4, 6, 8+ screenshots dynamically for 1v1, 2v2, 3v3 or multi-leg sets!
  - Automatically crops regions for player names and central score banners.
  - Extracts text via **EasyOCR** and fuzzy matches to registered IGNs using `rapidfuzz`.
  - Calculates aggregate scores and awards wins automatically.
  - Prompts with ✅ / ❌ reaction verification if OCR confidence is low.

- **Admin Management & Overrides**:
  - `/add_admin user:@mention` — Add a user as a tournament admin.
  - `/remove_admin user:@mention` — Revoke tournament admin permissions.
  - `/list_admins` — View list of appointed tournament admins.
  - `/create_tournament name:<str> num_teams:<int> team_size:<int>` — Initialize tournament slots (power of 2).
  - `/cancel_tournament` — Cancel and delete the active tournament.
  - `/start_tournament format:Knockout` — Seed and generate opening round with randomized pairings.
  - `/next_round` — Generate next round of fixtures when all current matches finish.
  - `/update_result match_id:<int> leg:<int> home_score:<int> away_score:<int>` — Update a leg score.
  - `/manual_result match_id:<int> leg1_home:<int> leg1_away:<int> leg2_home:<int> leg2_away:<int>` — Manually record both legs.
  - `/force_winner match_id:<int> team_number:<int>` — Force a winner (disqualifications / ties).
  - `/remove_result match_id:<int>` — Reset a match result.
  - `/remove_member player:@mention` — Remove a player from a team.

---

## 🛠 Local Setup & Running (Alternative)

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure Environment Variables**:
   Copy `.env.example` to `.env`:
   ```env
   BOT_TOKEN=your_bot_token_here
   OCR_CONFIDENCE_THRESHOLD=70
   FUZZY_MATCH_THRESHOLD=65
   ```
   *(Ensure the bot has `Message Content Intent` and `Server Members Intent` enabled in the Discord Developer Portal).*

3. **Start the Bot**:
   ```bash
   python bot.py
   ```
