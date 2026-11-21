import json, os, sqlite3, subprocess, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

work = Path(__file__).resolve().parent
root = work.parent
from contextlib import closing

with closing(sqlite3.connect((root / 'dart.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
    companies = db.execute('select corp_code, corp_name, stock_code from companies order by stock_code').fetchall()

item = {'type':'object','properties':{k:{'type':['string','null']} for k in
    ('corp_code','homepage_url','company_overview_url','careers_url','talent_url','verification_note')},
    'required':['corp_code','homepage_url','company_overview_url','careers_url','talent_url','verification_note'],'additionalProperties':False}
schema = {'type':'object','properties':{'companies':{'type':'array','items':item}},'required':['companies'],'additionalProperties':False}
(work / 'schema.json').write_text(json.dumps(schema))
results = work / 'discovery'
results.mkdir(exist_ok=True)
logs = work / 'logs'
logs.mkdir(exist_ok=True)

def discover(group):
    pending = [row for row in group if not (results / (row[0] + '.json')).exists()]
    if not pending:
        return
    first = pending[0][0]
    output = logs / (first + '.json')
    prompt = """각 KOSPI 법인의 공식 URL을 Codex Web Search로만 조사하세요. 기업마다 검색 도구로 검색하고 가능하면 근거를 확인하세요.
corp_code를 입력 값 그대로 복사하고 법인명과 종목코드로 동명 회사와 그룹 계열사를 구분하세요.
homepage_url은 해당 법인의 공식 사이트, company_overview_url은 공식 소개 페이지, careers_url은 해당 법인의 공식 채용 페이지, talent_url은 공식 인재상이나 일하는 방식 상세 페이지입니다.
그룹 채용 사이트는 해당 법인 전용 페이지일 때만 사용하세요. 확인 못한 URL은 null, verification_note에는 한국어로 어떤 법인인지 확인한 단서나 미확인 이유를 남기세요. 뉴스 기사, 잡포털, IR, 보도자료 제외. 어떤 파일도 변경하지 말고 지정 JSON 구조만 반환하세요. 회사 목록(고유번호, 이름, 종목코드):
""" + json.dumps(pending, ensure_ascii=False)
    command = ['codex','exec','--ephemeral','--json','-m','gpt-6-luna','-c','model_reasoning_effort="low"','-c','web_search="live"','-s','read-only','--output-schema',str(work/'schema.json'),'-o',str(output),'-']
    for _ in range(2):
        with (logs / (first+'.jsonl')).open('ab') as log:
            ran = subprocess.run(command, input=prompt.encode(), stdout=log, stderr=log, cwd=root)
        if ran.returncode == 0:
            try:
                found = json.loads(output.read_text())['companies']
                indexed = {x['corp_code']:x for x in found}
                if len(found) != len(pending) or set(indexed) != {r[0] for r in pending}:
                    continue
                for code, name, stock in pending:
                    record = indexed[code]
                    for field in ('homepage_url','company_overview_url','careers_url','talent_url'):
                        url = record[field]
                        if url and (urlsplit(url).scheme not in ('http','https') or not urlsplit(url).hostname):
                            record[field] = None
                    record.update(corp_name=name, stock_code=stock, discovery_method='codex_web_search')
                    with NamedTemporaryFile('w',dir=results,encoding='utf-8',delete=False) as f:
                        json.dump(record,f,ensure_ascii=False,indent=2)
                    os.replace(f.name,results/(code+'.json'))
                print(f'확인 {len(pending)}개 · {first}',flush=True)
                return
            except (ValueError, KeyError, OSError):
                pass
        time.sleep(5)
    print(f'재시도 필요 · {first}',flush=True)

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--last-half', action='store_true')
    parser.add_argument('--middle', action='store_true')
    args = parser.parse_args()
    size = 8
    groups = [companies[i:i+size] for i in range(0,len(companies),size)]
    if args.last_half:
        groups = list(reversed(groups[len(groups)//2:]))
    if args.middle:
        groups = groups[len(groups)//2:3*len(groups)//4] if not args.last_half else groups
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(discover,groups))
    if all((results / (code + '.json')).exists() for code, _, _ in companies):
        (work / 'discovery.finished').touch()
    elif not (args.last_half or args.middle):
        raise SystemExit(1)
