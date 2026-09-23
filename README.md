# Atriz opportunity pipeline

Every Sunday at 7:00 AM (Caracas time), this pipeline:

1. Runs the hiring.cafe Apify actor (`memo23/apify-hiring-cafe-scraper`) with `config/apify_input.json` and waits for it to finish.
2. Downloads the full dataset as JSON.
3. Scores it against the Atriz ICP with `scoring/score_opportunities.py`. See `scoring/rules.md` for the rules.
4. Uploads `atriz_opportunities_scored_MM-DD.csv` to a Google Drive folder.
5. Emails the CSV with a summary: priority counts, top ICP/Qualified rows, and items to check by hand.

If any step fails, you get an email titled "Atriz opportunity pipeline FAILED". The raw JSON and the CSV
are also kept for 30 days on each GitHub run ("Artifacts" section of the run page).

---

## One-time setup (about 30 minutes)

### 1. Create the GitHub repo

1. On github.com, click **New repository**. Name it `atriz-opportunity-pipeline` and set it to **Private**.
2. Click **uploading an existing file** and drag in everything from this folder.
3. The `.github` folder is hidden on Windows and often gets skipped during upload. If it's missing
   after the upload, click **Add file → Create new file**, type
   `.github/workflows/weekly-scoring.yml` as the name, and paste in the contents of that file.

### 2. Apify

1. Find your **API token** in Apify Console under **Settings → API & Integrations**.
2. The actor is already set to `memo23/apify-hiring-cafe-scraper`, so nothing else is needed from Apify.
   - Optional: save your search as an **Apify Task** and put its ID in the `APIFY_TASK_ID` variable.
     The task keeps its input inside Apify, so `config/apify_input.json` is ignored.

### 3. Google (Drive + Gmail), done once

1. Go to console.cloud.google.com and create a project, for example "Atriz Automation".
2. Open **APIs & Services → Library** and enable both the **Google Drive API** and the **Gmail API**.
3. Open **OAuth consent screen**, choose User type **Internal**, and fill in the app name and your email.
   - It has to be *Internal*. With "External / Testing", Google expires the token every 7 days and
     the pipeline stops.
4. Open **Credentials → Create credentials → OAuth client ID**, choose type **Web application**, and add this
   authorized redirect URI: `https://developers.google.com/oauthplayground`
   - Copy the **Client ID** and **Client secret**.
5. Open https://developers.google.com/oauthplayground and follow these steps:
   - Click the gear icon (top right), check **Use your own OAuth credentials**, and paste the Client ID and secret.
   - In the left box, paste these two scopes (space-separated) and click **Authorize APIs**:
     `https://www.googleapis.com/auth/drive https://www.googleapis.com/auth/gmail.send`
   - Sign in as anthony@hireatriz.com and allow access.
   - Click **Exchange authorization code for tokens** and copy the **Refresh token**.
6. Get the **Drive folder ID**: open the target folder in Drive. The ID is the last part of the URL
   (`drive.google.com/drive/folders/THIS_PART`).

### 4. Add the settings to GitHub

In the repo, go to **Settings → Secrets and variables → Actions**.

**Secrets** tab (these values stay hidden):

| Name | Value |
|---|---|
| `APIFY_TOKEN` | Apify API token |
| `GOOGLE_CLIENT_ID` | from step 3.4 |
| `GOOGLE_CLIENT_SECRET` | from step 3.4 |
| `GOOGLE_REFRESH_TOKEN` | from step 3.5 |

**Variables** tab:

| Name | Value | Required |
|---|---|---|
| `APIFY_ACTOR_ID` | only to switch actors. Default is `memo23/apify-hiring-cafe-scraper` | optional |
| `GDRIVE_FOLDER_ID` | Drive folder ID | for Drive |
| `EMAIL_TO` | `anthony@hireatriz.com` (comma-separate to add people) | for email |
| `APIFY_LOOKBACK_DAYS` | e.g. `7`. Overrides hiring.cafe's "posted in the past N days" filter (see below) | optional |
| `GDRIVE_AS_SHEET` | `true` to upload as a Google Sheet instead of a .csv | optional |
| `REMOVE_SALES_TITLES` | `true` to restore the old sales-title removal (it's paused by default) | optional |

### 5. Test it without spending Apify credits

1. Go to **Actions → Weekly opportunity scoring → Run workflow**.
2. In **dataset_id**, paste the dataset ID of an Apify run you've already done. You can find it on the
   run's **Storage** tab in Apify.
3. Click **Run workflow**. In a few minutes the CSV should show up in Drive and in your inbox.

After that, leave **dataset_id** empty to do a full live run, or just wait for Sunday.

---

## Day-to-day

- **New client signed**: edit `config/current_clients.txt` in GitHub and add one name per line.
- **Change the search** (titles, states): replace `config/apify_input.json` with a new export of the
  actor input from Apify. To get it, open the actor in Apify and go to **Input → JSON**.
- **Change a scoring rule**: edit `scoring/score_opportunities.py` and log the change and the reason in
  `scoring/rules.md`.
- **Run it now**: Actions → Run workflow.

## Worth knowing

- **Lookback window.** The current input asks hiring.cafe for jobs posted in the past **61 days**. On a
  weekly schedule, that means most rows repeat from last week. Setting `APIFY_LOOKBACK_DAYS=7` returns
  only the new postings each week.
- **GitHub Actions minutes.** Private repos get 2,000 free minutes a month. The job mostly waits on
  Apify, so a 60-minute run every week uses roughly 260 minutes a month.
- **Scheduled runs can start late.** GitHub sometimes starts scheduled runs 5–30 minutes late at busy times.
