"""Export a reviewable snapshot of the local discovery and Playwright results."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--db',type=Path,default=Path(__file__).resolve().parents[1]/'dart.sqlite3')
args = parser.parse_args()
with sqlite3.connect(args.db.resolve().as_uri()+'?mode=ro',uri=True) as db:
    rows = db.execute("""SELECT corp_code, corp_name, stock_code, discovery, playwright
                        FROM companies LEFT JOIN company_source_collection USING(corp_code)
                        ORDER BY stock_code""")
    for code, name, stock, discovery, playwright in rows:
        metadata = json.loads(discovery) if discovery else {'corp_code':code,'corp_name':name,'stock_code':stock}
        result = json.loads(playwright) if playwright else None
        metadata['state'] = 'browser_attempted' if result else 'url_searched' if discovery else 'pending'
        if result:
            metadata['checked_at'] = result['checked_at']
            metadata['pages'] = result['pages']
        print(json.dumps(metadata,ensure_ascii=False,separators=(',',':')),file=sys.stdout)
