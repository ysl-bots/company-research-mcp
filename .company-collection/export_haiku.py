"""Export updated candidates with the original, Claude and browser provenance."""
import argparse
import json
from pathlib import Path
import sqlite3

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--db',type=Path,default=Path(__file__).resolve().parents[1]/'dart.sqlite3')
args = parser.parse_args()
with sqlite3.connect(args.db.resolve().as_uri()+'?mode=ro',uri=True) as db:
    rows = db.execute("""SELECT c.corp_code,c.corp_name,c.stock_code,s.discovery,s.playwright,
                        f.careers,f.overview,f.browser
                        FROM companies c JOIN company_source_collection s USING(corp_code)
                        LEFT JOIN company_source_followup f USING(corp_code) ORDER BY c.stock_code""")
    for code,name,stock,discovery,browser,careers,overview,follow_browser in rows:
        prior = json.loads(discovery)
        old_pages = json.loads(browser)['pages']
        c = json.loads(careers) if careers else None
        o = json.loads(overview) if overview else None
        new_pages = json.loads(follow_browser)['pages'] if follow_browser else None
        old_overview = next(p for p in old_pages if p['kind']=='company_overview')
        intro_cast = old_overview['status']=='evidence_candidate' and bool(old_overview.get('snippets'))
        effective_career = (c['careers_url'] if c and c['careers_source_url'] else None) or prior['careers_url']
        if o and effective_career == o.get('excluded_careers_url'):
            effective_career = None
        effective_intro = (o['company_overview_url'] if o and o['overview_summary'] else None) or (old_overview.get('url') if intro_cast else None)
        doc={'corp_code':code,'corp_name':name,'stock_code':stock,
             'careers_url':effective_career,'overview_url_with_text':effective_intro,
             'previous_discovery':prior,'previous_playwright':old_pages,
             'claude_careers':c,'claude_overview':o,'followup_playwright':new_pages}
        print(json.dumps(doc,ensure_ascii=False,separators=(',',':')))
