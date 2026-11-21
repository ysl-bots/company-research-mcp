# 코스피 공식 출처 수집

`run.py`는 현재 로컬 SQLite의 법인 목록을 읽고, Codex Web Search로 법인별 공식 홈페이지와 채용 출처를 찾는다. URL을 확인하지 못한 경우 JSON에 근거와 함께 빈 값으로 기록한다. 완료한 법인은 다시 검색하지 않는다.

`collect.cjs`는 검색 결과의 공식 페이지를 Playwright에서 열고, 페이지 제목과 관련 본문 후보, 이미지 위치를 별도 체크포인트에 저장한다. 브라우저 결과 상태는 자료 후보, 검토 필요, URL 미확인, 열기 실패로 구분한다. 자료 후보는 후속 검토가 필요한 수집 결과다.

`save.py --watch`는 각 단계의 체크포인트를 기존 SQLite의 `company_source_collection` 테이블에 반영한다. 기존 공시 및 검토된 기업 자료는 각각 원래 테이블에 있다.

- URL 조사 및 재개는 `python3 .company-collection/run.py`
- Playwright 수집은 `node .company-collection/collect.cjs`. Playwright가 별도 위치에 설치되어 있으면 `COMPANY_PLAYWRIGHT_MODULE`, 별도 Chromium 실행 파일을 사용하면 `COMPANY_CHROMIUM`을 지정한다. 접속 실패를 다시 처리하려면 같은 명령에 `--retry-failed`를 붙인다.
- SQLite 반영은 `python3 .company-collection/save.py`. 이 명령에 `--watch`를 추가하면 진행 중인 체크포인트도 반영한다.
- 진행 상태와 결과 확인은 `python3 .company-collection/export.py`. JSON Lines로 전체 법인을 출력한다. Git 검토용 결과는 `python3 .company-collection/export.py > company_sources.jsonl`로 갱신한다.

## Claude Code 보완 조사

`python3 .company-collection/run_haiku.py all`은 기존 채용 URL 누락을 먼저 조사한 뒤, 기업 소개 본문 후보가 없는 법인을 조사한다. 각 배치는 `claude -p --model claude-haiku-5-5 --effort low`를 사용하며, WebSearch 호출과 모델을 CLI 로그로 확인한다. 완료한 법인은 체크포인트로 건너뛴다.

`python3 .company-collection/save_haiku.py`는 보완 기록을 `company_source_followup`에 저장하고 새 URL을 Playwright 입력으로 준비한다. `COMPANY_COLLECTION_DIR="$PWD/.company-collection/haiku" node .company-collection/collect.cjs`로 변경된 URL을 렌더링한다. 검색 완료 후 `save_haiku.py --finish`로 브라우저에 종료 신호를 주고, 브라우저 종료 후 `save_haiku.py`를 한 번 더 실행해 최종 결과를 반영한다.

`python3 .company-collection/export_haiku.py > company_sources_haiku.jsonl`로 Git 검토용 보완 결과를 내보낸다. 기존 출처, Claude 결과와 새 Playwright 결과를 각각 확인할 수 있다.

WebFetch에서 본문을 확보하지 못한 회사의 공식 URL은 `stage_rendered.py`로 다시 준비한다. 이 경우 `COMPANY_COLLECTION_DIR="$PWD/.company-collection/haiku/rendered" node .company-collection/collect.cjs --overview-only`로 렌더링하고, `run_haiku.py rendered`로 근거가 있는 문장만 추린다. 자동 추출 요약에는 원문 캡처와 최초 조회 상태를 함께 저장한다.
