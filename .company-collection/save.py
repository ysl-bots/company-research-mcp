import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--watch', action='store_true')
parser.add_argument('--db', type=Path, default=root.parent / 'dart.sqlite3')
args = parser.parse_args()
with closing(sqlite3.connect(args.db,timeout=20)) as db:
    db.execute("""CREATE TABLE IF NOT EXISTS company_source_collection (
        corp_code TEXT PRIMARY KEY REFERENCES companies(corp_code),
        discovery TEXT NOT NULL,
        playwright TEXT,
        CHECK(json_valid(discovery)),
        CHECK(playwright IS NULL OR json_valid(playwright))
    )""")
    db.execute('PRAGMA foreign_keys = ON')
    seen = {}
    while True:
        with db:
            for candidate in sorted((root/'discovery').glob('*.json')):
                collected = root/'playwright'/candidate.name
                stamp = (candidate.stat().st_mtime_ns, collected.stat().st_mtime_ns if collected.is_file() else None)
                if seen.get(candidate.name) == stamp:
                    continue
                discovery = json.loads(candidate.read_text())
                browser = json.loads(collected.read_text()) if collected.is_file() else None
                url_fields = ('homepage_url', 'company_overview_url', 'careers_url', 'talent_url')
                if browser and any(browser.get(key) != discovery.get(key) for key in url_fields):
                    browser = None
                db.execute("""INSERT INTO company_source_collection(corp_code,discovery,playwright)
                              VALUES (?,?,?) ON CONFLICT(corp_code) DO UPDATE SET
                              discovery=excluded.discovery, playwright=excluded.playwright
                              WHERE discovery IS NOT excluded.discovery OR playwright IS NOT excluded.playwright""",
                           (discovery['corp_code'], json.dumps(discovery,ensure_ascii=False),
                            json.dumps(browser,ensure_ascii=False) if browser else None))
                seen[candidate.name] = stamp
        total = db.execute('SELECT count(*) FROM companies').fetchone()[0]
        discovered, collected = db.execute('SELECT count(*),count(playwright) FROM company_source_collection').fetchone()
        print(f'URL 조사 {discovered}/{total} · Playwright 처리 {collected}/{total}',flush=True)
        if not args.watch or collected == total:
            break
        time.sleep(30)
