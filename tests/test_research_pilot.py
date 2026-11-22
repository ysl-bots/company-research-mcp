from contextlib import closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dart_job_seeker.research_pilot import COMPANIES, import_snapshot, validate
from dart_job_seeker.storage import connect, initialize


SNAPSHOT = Path(__file__).resolve().parents[1] / "company_research_pilot.json"


class ResearchPilotTest(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

    def test_window_includes_both_boundaries(self):
        item = self.data["items"][0]
        for day in (self.data["start_date"], self.data["checked_date"]):
            item["date"] = day
            validate(self.data)
        for day in ("2025-11-20", "2026-11-22"):
            item["date"] = day
            with self.assertRaisesRegex(ValueError, "Outside collection window"):
                validate(self.data)

    def test_cap_and_conference_grouping(self):
        item = self.data["items"][0]
        self.data["items"].append({**item, "url": item["url"] + "-extra"})
        with self.assertRaisesRegex(ValueError, "Limit exceeded"):
            validate(self.data)
        self.data["items"].pop()
        item = next(item for item in self.data["items"] if item["kind"] == "conference")
        self.data["items"].append({**item, "url": item["url"].rstrip("/") + "-video"})
        with self.assertRaisesRegex(ValueError, "Duplicate session"):
            validate(self.data)

    def test_reimport_preserves_unique_items_and_shared_report_links(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "pilot.sqlite3"
            initialize(path)
            with closing(connect(path)) as db, db:
                db.executemany(
                    "INSERT INTO companies VALUES (?, '', '', ?)", COMPANIES.items()
                )

            import_snapshot(SNAPSHOT, path)
            before = self.rows(path)
            import_snapshot(SNAPSHOT, path)
            self.assertEqual(before, self.rows(path))
            self.assertEqual(len(before[0]), len(self.data["items"]))

            report = next(item for item in self.data["items"] if len(item["companies"]) > 1)
            self.assertEqual({code for code, url, _ in before[1] if url == report["url"]},
                             set(report["companies"]))

    @staticmethod
    def rows(path):
        with closing(connect(path, readonly=True)) as db:
            return [
                [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY 1, 2")]
                for table in ("company_research_pilot_items", "company_research_pilot_companies")
            ]
