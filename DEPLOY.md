# EP Monitor — Deployment Guide

## Option A: Run Locally on Mac (simplest)

### 1. Prerequisites
- Python 3.11+ (`python3 --version`)
- pip

### 2. Install dependencies
```bash
cd ep_monitor
pip install -r requirements.txt
python -m playwright install chromium
```

### 3. Set environment variables
```bash
cp .env.example .env
# Edit .env with your real NOTION_TOKEN and NOTION_DATABASE_ID
```

Then load them before running:
```bash
export $(cat .env | xargs)
```

Or add to your `~/.zshrc` / `~/.bash_profile` to make them permanent.

### 4. Set up the Notion database

In Notion, create a database called **"EliteProspects Monitoring Log"** with these properties (exact names + types):

| Property Name | Type |
|---|---|
| Search Phrase | Title |
| Date | Date |
| Time | Text |
| Traffic Source | Text |
| Search Engine | Text |
| Search Location | Text |
| Profile Found | Checkbox |
| EliteProspects URL | URL |
| Profile Analytics Opened | Checkbox |
| Blocked/Paywall | Checkbox |
| Site Visit Count | Text |
| Session Duration | Number |
| Run Type | Select (options: US, Non-US, Summary, Alert) |
| Result | Select (options: Success, Failed, Error, EP Link Not Found, Player Not Found, Info, Alert, Unknown) |
| Notes | Text |

Then share the database with your Notion integration (click Share → invite your integration).

### 5. Test a single run
```bash
python runner.py US
```

Check your Notion database for the new row.

### 6. Start the scheduler
```bash
python scheduler.py
```

Leave this terminal open (or run it in the background with `nohup python scheduler.py &`).

To keep it running after closing Terminal:
```bash
nohup python scheduler.py > ep_monitor.log 2>&1 &
echo $! > scheduler.pid   # saves the process ID
```

To stop it later:
```bash
kill $(cat scheduler.pid)
```

---

## Option B: Deploy to Railway (cloud, always-on)

### 1. Create a Railway account
Go to railway.app and sign up (free tier available).

### 2. Install Railway CLI
```bash
brew install railway
railway login
```

### 3. Initialize project
```bash
cd ep_monitor
railway init
```

### 4. Add a Procfile
Create a file called `Procfile` (no extension) in the ep_monitor folder:
```
worker: python scheduler.py
```

### 5. Add a nixpacks.toml for Playwright
Create `nixpacks.toml`:
```toml
[phases.setup]
nixPkgs = ["chromium", "nss", "nspr", "atk", "cups", "libdrm", "dbus", "libxkbcommon", "gtk3", "pango", "cairo", "glib", "gdk-pixbuf2"]

[phases.install]
cmds = ["pip install -r requirements.txt", "python -m playwright install chromium"]
```

### 6. Set environment variables in Railway
In the Railway dashboard → your project → Variables:
```
NOTION_TOKEN=secret_xxx...
NOTION_DATABASE_ID=xxx...
```

### 7. Deploy
```bash
railway up
```

Railway will build and start your worker. View logs in the Railway dashboard.

---

## Troubleshooting

**EP link not found:** The MHR roster page may use JavaScript rendering. Try setting `headless=False` in runner.py temporarily to watch what happens, or increase the `wait_for_timeout` after `goto`.

**Notion 400 error:** One of your database property names doesn't match exactly. Check for extra spaces or capitalization differences.

**Playwright not finding Chromium on Railway:** Make sure your `nixpacks.toml` is in place and redeploy.

**Scheduler fires at wrong times:** Confirm your system timezone is correct. The scheduler always uses `America/New_York` (ET) explicitly via pytz.
