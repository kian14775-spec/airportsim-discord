# Setup (about 20 minutes, once)

Everything here is free. Only you can do these steps, because they need your accounts
and the bot token. After this, layout changes are: tell Claude, Claude does it.

## 1. Make the bot (Discord, 5 min)
1. Go to https://discord.com/developers/applications > **New Application** > name it.
2. **Bot** tab:
   - Turn on **Server Members Intent** and **Message Content Intent**. Save.
   - **Reset Token**, copy it, and paste it after `DISCORD_TOKEN=` in
     `Documents\airportsim-discord\.env`. Never paste the token in chat.
3. **OAuth2 > URL Generator**: tick `bot` and `applications.commands`, then
   permission **Administrator**. Open the link and add the bot to your server.
4. In Discord: **Settings > Advanced > Developer Mode** on. Right-click your server
   icon > **Copy Server ID**, and paste it after `GUILD_ID=` in `.env`.
5. Server Settings > **Enable Community**, if it's off. Forums and announcement
   channels need it.
6. Server Settings > **Roles**: drag the bot's role to the top (just under yours).

## 2. Try it from your PC
```
cd %USERPROFILE%\Documents\airportsim-discord
.venv\Scripts\python layout.py preview
```
This only shows what it would do. Nothing changes until `apply`.
Tip: test on an empty test server first (make one, invite the bot, use its ID).

## 3. Free 24/7 server (Oracle Cloud, 10 min)
1. Sign up at https://www.oracle.com/cloud/free/. It asks for a card to check you're
   a real person; Always Free resources aren't charged.
2. **Upgrade the account to Pay As You Go** (Billing > Upgrade). Still £0 as long as
   you stay on Always Free shapes, but it stops Oracle from deleting "idle" free
   servers, which a quiet bot would count as. Then set a **budget alert at £1** so
   you'd get an email if anything ever cost money.
3. **Create a VM instance**: image **Ubuntu 24.04**, shape **VM.Standard.A1.Flex**
   with 1 OCPU and 6 GB (says "Always Free eligible"). If it says out of capacity,
   use **VM.Standard.E2.1.Micro** instead.
4. **Save the private key** it offers (e.g. to `Documents\airportsim-discord\oracle.key`).
5. Copy the instance's **public IP**. In `.env` set:
   `DEPLOY_HOST=ubuntu@<that IP>` and `DEPLOY_KEY=<path to the .key file>`.
6. Tell Claude "server's ready". Claude runs `deploy\deploy.ps1 -FirstTime`, which
   installs everything and starts the bot as a service.

## Why it stays online
- Runs as a system service: if it crashes it restarts in 5 s, and it starts on boot.
- discord.py reconnects by itself after network drops.
- A watchdog restarts the bot if it's been disconnected for 5 minutes.
- Ubuntu installs security updates automatically.
- `/ping` in Discord shows latency and uptime.
Nothing can promise 100 %: Discord outages take every bot offline.
