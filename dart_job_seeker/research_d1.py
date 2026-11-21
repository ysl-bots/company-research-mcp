"""Build a compact public projection of locally collected research evidence."""
from contextlib import closing
import json
from pathlib import Path
from urllib.parse import urlsplit

from .d1 import COMPANY_UPSERT
from .storage import connect

RESEARCH_UPSERT = """
INSERT INTO company_research (corp_code, payload) VALUES (?, ?)
ON CONFLICT(corp_code) DO UPDATE SET payload = excluded.payload
WHERE company_research.payload != excluded.payload
"""


def public_url(value):
    if not value or any(character.isspace() for character in value):
        return None
    try:
        url = urlsplit(value)
        if url.scheme in {"http", "https"} and url.hostname and not url.username and not url.password:
            return value
    except ValueError:
        pass
    return None


def evidence(url, heading, claims, kind):
    return {"url": url, "heading": heading, "claims": claims, "kind": kind}


def browser_evidence(pages, kind, matching_url=None):
    for page in pages:
        url = public_url(page.get("final_url"))
        if (page["kind"] == kind and page["status"] == "evidence_candidate" and url
            and (not matching_url or url == matching_url or page.get("url") == matching_url)
            and page.get("snippets")):
            return evidence(url, page.get("title"), page["snippets"], kind)
    return None


def project(discovery, first_browser, careers, overview, browser, reviewed):
    careers_url = public_url(reviewed.get("careers_url") or discovery.get("careers_url"))
    homepage_url = public_url(reviewed.get("homepage_url") or discovery.get("homepage_url"))
    careers_source_url = public_url(careers.get("careers_source_url"))
    if careers_source_url and public_url(careers.get("careers_url")):
        careers_url = public_url(careers["careers_url"])
    if careers_url == overview.get("excluded_careers_url"):
        careers_url = careers_source_url = None

    def reviewed_evidence(kinds):
        return [evidence(source["url"], source["heading"], source["claims"], source["kind"])
                for source in reviewed.get("sources", [])
                if source["kind"] in kinds and source["status"] == "verified" and public_url(source["url"])]

    pages = browser.get("pages", []) + first_browser.get("pages", [])
    company_items = reviewed_evidence({"company_overview"})
    company_status = "reviewed" if company_items else "unverified"
    if not company_items:
        url = public_url(overview.get("company_overview_url"))
        summary = overview.get("overview_summary")
        item = (evidence(url, overview.get("overview_heading"), [summary], "company_overview")
                if url and summary else browser_evidence(pages, "company_overview"))
        if item:
            company_items = [item]
            company_status = "candidate"

    talent_items = reviewed_evidence({"recruiting", "talent_culture", "talent_criteria"})
    talent_status = "reviewed" if talent_items else "candidate" if careers_url else "unverified"
    if not talent_items:
        item = browser_evidence(pages, "talent_profile", careers_url)
        if item:
            talent_items = [item]
            talent_status = "candidate"
    return {
        "homepage_url": homepage_url, "careers_url": careers_url, "careers_source_url": careers_source_url,
        "overview_status": company_status, "overview": company_items,
        "talent_status": talent_status, "talent": talent_items,
    }


def research_updates(path: Path):
    with closing(connect(path, readonly=True)) as db:
        rows = db.execute("""
            SELECT c.*, s.discovery, s.playwright, f.careers, f.overview, f.browser, m.payload AS reviewed
            FROM companies c JOIN company_source_collection s USING (corp_code)
            LEFT JOIN company_source_followup f USING (corp_code)
            LEFT JOIN company_materials m USING (corp_code)
            ORDER BY c.corp_code
        """)
        for row in rows:
            fields = [json.loads(row[key]) if row[key] else {} for key in
                      ("discovery", "playwright", "careers", "overview", "browser", "reviewed")]
            payload = json.dumps(project(*fields), ensure_ascii=False, separators=(",", ":"))
            yield [
                {"sql": COMPANY_UPSERT, "params": [row[key] for key in
                 ("corp_code", "corp_name", "corp_eng_name", "stock_code")]},
                {"sql": RESEARCH_UPSERT, "params": [row["corp_code"], payload]},
            ]
