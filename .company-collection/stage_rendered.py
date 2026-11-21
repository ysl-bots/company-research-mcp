"""Prepare browser-only recovery for company introduction text."""
from pathlib import Path
import json
import re

root=Path(__file__).resolve().parent
recover=root/'haiku'/'rendered'
stage=recover/'discovery'
stage.mkdir(parents=True,exist_ok=True)
count=0
for path in (root/'haiku'/'overview').glob('*.json'):
    result=json.loads(path.read_text())
    if result['overview_summary']:
        continue
    record=json.loads((root/'discovery'/path.name).read_text())
    url=result.get('company_overview_url') or record.get('company_overview_url') or record.get('homepage_url')
    if not url:
        continue
    record['company_overview_url']=url
    (stage/path.name).write_text(json.dumps(record,ensure_ascii=False))
    count+=1
print(count)
