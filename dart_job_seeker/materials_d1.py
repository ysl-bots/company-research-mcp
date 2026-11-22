"""Project the reviewed research snapshot into the public D1 material table."""
from contextlib import closing
from datetime import date
import json
from pathlib import Path
import re

from .d1 import COMPANY_UPSERT
from .research_batch import KINDS, validate_record, valid_url
from .storage import connect


UPSERT = """
INSERT INTO company_research_materials (corp_code, checked_date, payload)
VALUES (?, ?, ?)
ON CONFLICT(corp_code) DO UPDATE SET
    checked_date = excluded.checked_date, payload = excluded.payload
WHERE excluded.checked_date > company_research_materials.checked_date
   OR (excluded.checked_date = company_research_materials.checked_date
       AND company_research_materials.payload != excluded.payload)
"""


def material_updates(db_path: Path, snapshot_path: Path):
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    checked = date.fromisoformat(snapshot["checked_date"])
    start = date.fromisoformat(snapshot["start_date"])
    if snapshot["schema_version"] != 1 or start > checked:
        raise ValueError("잘못된 리서치 스냅샷 버전 또는 기간")
    with closing(connect(db_path, readonly=True)) as db:
        identities = {
            row["corp_code"]: dict(row)
            for row in db.execute("SELECT corp_code, corp_name, corp_eng_name, stock_code FROM companies")
        }

    records = snapshot["companies"]
    codes = {record["corp_code"] for record in records}
    if len(codes) != len(records) or codes != identities.keys() or len(codes) != snapshot["completed_companies"]:
        raise ValueError("스냅샷과 SQLite의 법인 집합이 일치하지 않습니다.")

    updates = []
    for record in records:
        code = record["corp_code"]
        identity = identities[code]
        if not re.fullmatch(r"[0-9]{8}", code) or record["stock_code"] != identity["stock_code"]:
            raise ValueError(f"법인 식별자 불일치: {code}")
        count = len(record["items"])
        validated = validate_record(record, [identity], checked, start)
        if len(validated["items"]) != count:
            raise ValueError(f"스냅샷 자료 검증 실패: {code}")
        for item in validated["items"]:
            item["assets"] = [
                asset for asset in item.get("assets", [])
                if valid_url(asset.get("url"))
            ]
        payload = {
            "corp_code": code,
            "start_date": start.isoformat(),
            "checked_date": checked.isoformat(),
            "ir_home": validated["ir_home"],
            "ir_library": validated["ir_library"],
            "item_counts": {kind: validated["item_counts"][kind] for kind in KINDS},
            "missing_reasons": validated["missing_reasons"],
            "items": validated["items"],
        }
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(serialized.encode()) > 1_900_000:
            raise ValueError(f"D1 행 크기 한도에 근접합니다: {code}")
        updates.append([
            {"sql": COMPANY_UPSERT, "params": [identity[key] for key in
                ("corp_code", "corp_name", "corp_eng_name", "stock_code")]},
            {"sql": UPSERT, "params": [code, checked.isoformat(), serialized]},
        ])
    return updates
