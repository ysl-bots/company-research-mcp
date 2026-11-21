"""Save Claude follow-up and prepare changed official URLs for Playwright."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

WORK = Path(__file__).resolve().parent
ROOT = WORK.parent
HAIKU = WORK / 'haiku'

def read(path):
    return json.loads(path.read_text()) if path.is_file() else None

def packed(value):
    return json.dumps(value, ensure_ascii=False) if value is not None else None

def stage(path, doc):
    if read(path) == doc:
        return
    with NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as handle:
        json.dump(doc, handle, ensure_ascii=False)
    Path(handle.name).replace(path)

def save(db, finish):
    db.execute('PRAGMA foreign_keys=ON')
    db.execute("""CREATE TABLE IF NOT EXISTS company_source_followup (
                corp_code TEXT PRIMARY KEY REFERENCES companies(corp_code),
                careers TEXT, overview TEXT, browser TEXT,
                CHECK(careers IS NULL OR json_valid(careers)),
                CHECK(overview IS NULL OR json_valid(overview)),
                CHECK(browser IS NULL OR json_valid(browser)))""")
    staged = HAIKU / 'discovery'
    staged.mkdir(parents=True,exist_ok=True)
    urls = ('homepage_url', 'company_overview_url', 'careers_url', 'talent_url')
    with db:
        for item in sorted((WORK/'discovery').glob('*.json')):
            initial = read(item)
            career = read(HAIKU/'careers'/item.name)
            overview = read(HAIKU/'overview'/item.name)
            rendered = read(HAIKU/'rendered_summary'/item.name)
            if rendered and rendered['overview_summary'] and not (overview and overview['overview_summary']):
                capture = read(HAIKU/'rendered'/'playwright'/item.name)
                page = capture['pages'][0] if capture else {}
                original_host = urlsplit(page.get('url') or '').hostname or ''
                final_host = urlsplit(page.get('final_url') or '').hostname or ''
                if (original_host.removeprefix('www.') == final_host.removeprefix('www.')
                    and page.get('final_url') == rendered['company_overview_url']):
                    overview = rendered | {'method':'haiku_on_playwright_excerpts',
                                           'rendered_source': page, 'first_attempt':overview}
            if not (career or overview):
                continue
            revised = dict(initial)
            if career and career['careers_url'] and career['careers_source_url']:
                revised['careers_url'] = career['careers_url']
                revised['prefer_careers'] = True
            if overview and overview['company_overview_url'] and overview['overview_summary']:
                revised['company_overview_url'] = overview['company_overview_url']
            browser_file = HAIKU/'playwright'/item.name
            browser = read(browser_file)
            staged_file = staged/item.name
            changed = any(revised.get(k) != initial.get(k) for k in urls)
            if changed:
                stage(staged_file, revised | {'discovery_method':'claude_web_search_followup'})
            elif staged_file.is_file():
                superseded = HAIKU/'superseded_discovery'
                superseded.mkdir(exist_ok=True)
                staged_file.replace(superseded/item.name)
            if browser and (not changed or any(browser.get(k) != revised.get(k) for k in urls)
                            or browser.get('prefer_careers') != revised.get('prefer_careers')):
                backup = HAIKU/'superseded'
                backup.mkdir(exist_ok=True)
                browser_file.replace(backup/item.name)
                browser = None
            db.execute("""INSERT INTO company_source_followup(corp_code,careers,overview,browser)
                            VALUES(?,?,?,?) ON CONFLICT(corp_code) DO UPDATE SET
                            careers=excluded.careers,overview=excluded.overview,browser=excluded.browser
                            WHERE careers IS NOT excluded.careers OR overview IS NOT excluded.overview
                            OR browser IS NOT excluded.browser""",
                       (initial['corp_code'],packed(career),packed(overview),packed(browser)))
    if finish:
        (HAIKU/'discovery.finished').touch()

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--finish',action='store_true')
    parser.add_argument('--db',type=Path,default=ROOT/'dart.sqlite3')
    options=parser.parse_args()
    with closing(sqlite3.connect(options.db,timeout=30)) as db:
        save(db,options.finish)
        print(db.execute('SELECT count(*),count(careers),count(overview),count(browser) FROM company_source_followup').fetchone())
