#!/usr/bin/env python3
"""
Atriz weekly opportunity pipeline.

  1. Start the hiring.cafe Apify actor (or a saved Apify task) and wait for it to finish
     - or, if APIFY_DATASET_ID is set, skip the run and re-use an existing dataset
  2. Download every dataset item as JSON
  3. Score it with scoring/score_opportunities.py (the Atriz ICP rules)
  4. Upload the CSV to Google Drive and email it with a summary

Configuration is entirely via environment variables (GitHub secrets/variables):

  APIFY_TOKEN            required  Apify API token
  APIFY_ACTOR_ID         optional  default memo23/apify-hiring-cafe-scraper (runs with config/apify_input.json)
  APIFY_TASK_ID          one of    a saved Apify task (uses the input saved in Apify)
  APIFY_DATASET_ID       optional  skip the run and score this existing dataset instead
  APIFY_INPUT_FILE       optional  default config/apify_input.json
  APIFY_LOOKBACK_DAYS    optional  overwrite dateFetchedPastNDays in every hiring.cafe start URL
  APIFY_MAX_WAIT_MIN     optional  default 300

  GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN   for Drive + Gmail
  GDRIVE_FOLDER_ID       Drive folder for the CSV (skip upload if empty)
  GDRIVE_AS_SHEET        "true" to upload as a Google Sheet instead of a .csv file
  EMAIL_TO               comma-separated recipients (skip email if empty)
  DELIVER                "false" to skip Drive + email (test runs)
  RUN_LABEL              optional  label for filenames, default MM-DD in America/Caracas
"""
import base64, csv, html, io, json, os, sys, time, traceback, urllib.parse
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scoring'))
import score_opportunities  # noqa: E402

APIFY = 'https://api.apify.com/v2'
DEFAULT_ACTOR_ID = 'memo23/apify-hiring-cafe-scraper'
OUT_DIR = os.path.join(ROOT, 'output')


def env(name, default=''):
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else default


def log(msg):
    print(f'[pipeline] {msg}', flush=True)


# ------------------------------------------------------------------ Apify
def apify_session():
    token = env('APIFY_TOKEN')
    if not token:
        raise SystemExit('APIFY_TOKEN is not set')
    s = requests.Session()
    s.headers['Authorization'] = f'Bearer {token}'
    # Retry transient failures (network blips, 429 rate limits, 5xx) with exponential backoff
    # (2s, 4s, 8s, 16s, 32s). GET only: retrying the POST that STARTS a run could launch a
    # second, paid Apify run, so POSTs are never retried.
    retry = Retry(total=5, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=['GET'], respect_retry_after_header=True, raise_on_status=False)
    s.mount('https://', HTTPAdapter(max_retries=retry))
    return s


def load_actor_input():
    path = env('APIFY_INPUT_FILE', os.path.join(ROOT, 'config', 'apify_input.json'))
    with open(path) as fh:
        data = json.load(fh)
    lookback = env('APIFY_LOOKBACK_DAYS')
    if lookback:
        n = int(lookback)
        for su in data.get('startUrls', []):
            parts = urllib.parse.urlparse(su['url'])
            q = urllib.parse.parse_qs(parts.query)
            if 'searchState' in q:
                st = json.loads(q['searchState'][0])
                st['dateFetchedPastNDays'] = n
                q['searchState'] = [json.dumps(st, separators=(',', ':'))]
                su['url'] = urllib.parse.urlunparse(parts._replace(query=urllib.parse.urlencode(q, doseq=True)))
        log(f'lookback overridden to {n} days on {len(data.get("startUrls", []))} start URLs')
    return data


def start_run(s):
    task_id, actor_id = env('APIFY_TASK_ID'), env('APIFY_ACTOR_ID', DEFAULT_ACTOR_ID)
    if task_id:
        url = f'{APIFY}/actor-tasks/{task_id.replace("/", "~")}/runs'
        r = s.post(url, timeout=60)  # uses the input saved on the task
    elif actor_id:
        url = f'{APIFY}/acts/{actor_id.replace("/", "~")}/runs'
        r = s.post(url, json=load_actor_input(), timeout=60)
    else:
        raise SystemExit('No Apify actor configured')
    if r.status_code >= 400:
        raise RuntimeError(f'Apify refused to start the run ({r.status_code}): {r.text[:500]}')
    run = r.json()['data']
    log(f'started Apify run {run["id"]} - https://console.apify.com/view/runs/{run["id"]}')
    return run


def wait_for_run(s, run):
    deadline = time.time() + 60 * int(env('APIFY_MAX_WAIT_MIN', '300'))
    while True:
        r = s.get(f'{APIFY}/actor-runs/{run["id"]}', params={'waitForFinish': 60}, timeout=120)
        r.raise_for_status()
        run = r.json()['data']
        status = run['status']
        if status == 'SUCCEEDED':
            log(f'run finished: {status}')
            return run
        if status in ('FAILED', 'ABORTED', 'TIMED-OUT', 'TIMED_OUT'):
            raise RuntimeError(f'Apify run {run["id"]} ended with status {status}')
        if time.time() > deadline:
            raise RuntimeError(f'Apify run {run["id"]} still {status} after APIFY_MAX_WAIT_MIN; giving up')
        log(f'run status: {status} ...')


def download_dataset(s, dataset_id):
    items, offset, limit = [], 0, 1000
    while True:
        r = s.get(f'{APIFY}/datasets/{dataset_id}/items',
                  params={'format': 'json', 'clean': 'true', 'offset': offset, 'limit': limit}, timeout=300)
        r.raise_for_status()
        page = r.json()
        if not isinstance(page, list):
            raise RuntimeError(f'Apify did not return a list of items for dataset "{dataset_id}" - '
                               f'check the dataset ID (got: {str(page)[:200]})')
        items.extend(page)
        if len(page) < limit:
            break
        offset += limit
    log(f'downloaded {len(items)} items from dataset {dataset_id}')
    return items


# ------------------------------------------------------------------ Google
def google_token():
    cid, secret, refresh = env('GOOGLE_CLIENT_ID'), env('GOOGLE_CLIENT_SECRET'), env('GOOGLE_REFRESH_TOKEN')
    if not (cid and secret and refresh):
        return None
    r = requests.post('https://oauth2.googleapis.com/token', data={
        'client_id': cid, 'client_secret': secret, 'refresh_token': refresh, 'grant_type': 'refresh_token'}, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f'Google token refresh failed ({r.status_code}): {r.text[:300]}')
    return r.json()['access_token']


def clean_folder_id(value):
    # Accept a bare ID or a full Drive folder URL (.../folders/<id>?usp=...)
    v = value.strip()
    if '/folders/' in v:
        v = v.split('/folders/', 1)[1]
    return v.split('?')[0].split('/')[0].strip()


def drive_upload(token, path, folder_id, as_sheet):
    folder_id = clean_folder_id(folder_id)
    name = os.path.basename(path)
    meta = {'name': name if not as_sheet else os.path.splitext(name)[0], 'parents': [folder_id]}
    if as_sheet:
        meta['mimeType'] = 'application/vnd.google-apps.spreadsheet'
    boundary = 'atriz-boundary-7c1f'
    with open(path, 'rb') as fh:
        content = fh.read()
    body = (f'--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n'
            f'--{boundary}\r\nContent-Type: text/csv\r\n\r\n').encode() + content + f'\r\n--{boundary}--'.encode()
    r = requests.post('https://www.googleapis.com/upload/drive/v3/files',
                      params={'uploadType': 'multipart', 'supportsAllDrives': 'true', 'fields': 'id,webViewLink'},
                      headers={'Authorization': f'Bearer {token}',
                               'Content-Type': f'multipart/related; boundary={boundary}'},
                      data=body, timeout=300)
    if r.status_code >= 400:
        raise RuntimeError(f'Drive upload failed ({r.status_code}): {r.text[:300]}')
    link = r.json().get('webViewLink', '')
    log(f'uploaded to Drive: {link}')
    return link


def gmail_send(token, to, subject, html_body, attachment=None):
    msg = EmailMessage()
    msg['To'] = to
    msg['Subject'] = subject
    msg.set_content('This email needs an HTML-capable client.')
    msg.add_alternative(html_body, subtype='html')
    if attachment:
        with open(attachment, 'rb') as fh:
            msg.add_attachment(fh.read(), maintype='text', subtype='csv', filename=os.path.basename(attachment))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    r = requests.post('https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
                      headers={'Authorization': f'Bearer {token}'}, json={'raw': raw}, timeout=120)
    if r.status_code >= 400:
        raise RuntimeError(f'Gmail send failed ({r.status_code}): {r.text[:300]}')
    log(f'email sent to {to}')


# ------------------------------------------------------------------ report
def summary_html(label, summary, drive_link, apify_url, partial_note=''):
    p = summary['priority']
    rows = ''.join(
        f'<tr><td>{html.escape(str(t["company_name"]))}</td><td>{html.escape(str(t["title"]))}</td>'
        f'<td>{html.escape(str(t["niche"]))}</td><td style="text-align:right">{t["score"]}</td>'
        f'<td>{html.escape(str(t["priority"]))}</td></tr>' for t in summary['top'])
    niche = ', '.join(f'{k}: {v}' for k, v in sorted(summary['niche'].items(), key=lambda x: -x[1]))
    sales_line = ('Sales titles: kept and flagged (sales-title removal is paused)'
                  if summary['sales_removal_paused'] else f'Removed (sales titles): {summary["removed_sales"]}')
    return f"""<div style="font-family:Arial,sans-serif;font-size:14px;color:#222">
{partial_note}<p><b>Scored opportunities - {label}</b></p>
<p><b>{p.get('ICP', 0)} ICP</b> &middot; <b>{p.get('Qualified', 0)} Qualified</b> &middot;
{p.get('Marginal', 0)} Marginal &middot; {p.get('Ignore', 0)} Ignore</p>
<ul>
<li>Raw records: {summary['raw_total']} &rarr; after dedup: {summary['after_dedup_1']} &rarr; final rows: {summary['final_rows']}</li>
<li>Removed (non-profit/government): {summary['removed_nonprofit_gov']}</li>
<li>{sales_line}</li>
<li>Hard-passed as restaurants: {summary['excluded_restaurant']} &middot; as senior care: {summary['excluded_senior_care']}</li>
<li>Niches: {niche}</li>
<li><b>Manual check:</b> {summary['niche_none_qualified_plus']} Qualified/ICP rows have niche = None (strong company, off-niche role)</li>
</ul>
{f'<p>Google Drive: <a href="{drive_link}">{drive_link}</a></p>' if drive_link else ''}
{f'<p>Apify run: <a href="{apify_url}">{apify_url}</a></p>' if apify_url else ''}
<p><b>Top ICP / Qualified</b></p>
<table cellpadding="4" style="border-collapse:collapse;font-size:13px" border="1">
<tr><th>Company</th><th>Title</th><th>Niche</th><th>Score</th><th>Priority</th></tr>{rows}</table>
<p style="color:#777;font-size:12px">Full list attached. Generated by the atriz-opportunity-pipeline GitHub Action.</p>
</div>"""


def main():
    label = env('RUN_LABEL', datetime.now(ZoneInfo('America/Caracas')).strftime('%m-%d'))
    os.makedirs(OUT_DIR, exist_ok=True)
    raw_path = os.path.join(OUT_DIR, f'Original_search_{label}.json')
    csv_path = os.path.join(OUT_DIR, f'atriz_opportunities_scored_{label}.csv')
    summary_path = os.path.join(OUT_DIR, f'summary_{label}.json')

    s = apify_session()
    apify_url = ''
    # Accept the ID as Apify Console shows it ("#abc123"), with spaces, or as a full dataset URL
    dataset_id = env('APIFY_DATASET_ID').strip().lstrip('#').rstrip('/').split('/')[-1]
    if not dataset_id:
        run = wait_for_run(s, start_run(s))
        dataset_id = run['defaultDatasetId']
        apify_url = f'https://console.apify.com/view/runs/{run["id"]}'
    items = download_dataset(s, dataset_id)
    if not items:
        raise RuntimeError(f'Apify dataset {dataset_id} is empty - nothing to score')
    with open(raw_path, 'w') as fh:
        json.dump(items, fh)

    os.environ['SUMMARY_JSON'] = summary_path
    summary = score_opportunities.run([raw_path], csv_path)

    if env('DELIVER', 'true').lower() == 'false':
        log('DELIVER=false - skipping Drive and email')
        return
    token = google_token()
    if not token:
        log('Google credentials not set - skipping Drive and email')
        return
    drive_link = ''
    if env('GDRIVE_FOLDER_ID'):
        drive_link = drive_upload(token, csv_path, env('GDRIVE_FOLDER_ID'), env('GDRIVE_AS_SHEET').lower() == 'true')
    if env('EMAIL_TO'):
        p = summary['priority']
        subject = f'Atriz opportunities {label}: {p.get("ICP", 0)} ICP, {p.get("Qualified", 0)} Qualified'
        min_items = int(env('MIN_EXPECTED_ITEMS', '300'))
        partial_note = ''
        if summary['raw_total'] < min_items:
            subject = '[PARTIAL SCRAPE] ' + subject
            partial_note = (f'<p style="background:#fbf1dc;padding:8px;border-radius:4px"><b>Warning:</b> '
                            f'the scraper returned only {summary["raw_total"]} jobs (a normal week is well over '
                            f'{min_items}). hiring.cafe probably rate-limited the run. Check the Apify log for '
                            f'"status 429" or "403" and re-run later.</p>')
        gmail_send(token, env('EMAIL_TO'), subject, summary_html(label, summary, drive_link, apify_url, partial_note), csv_path)


def notify_failure(err_text):
    try:
        token = google_token()
        if token and env('EMAIL_TO') and env('DELIVER', 'true').lower() != 'false':
            gmail_send(token, env('EMAIL_TO'), 'Atriz opportunity pipeline FAILED',
                       f'<pre style="font-size:12px">{html.escape(err_text[-4000:])}</pre>'
                       f'<p>Check the GitHub Actions log for details.</p>')
    except Exception as e:  # never mask the original error
        log(f'could not send failure email: {e}')


if __name__ == '__main__':
    try:
        main()
    except BaseException as e:
        if isinstance(e, SystemExit) and e.code in (0, None):
            raise
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        notify_failure(tb)
        sys.exit(1)
