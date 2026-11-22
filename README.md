# Company Research MCP

OpenDART의 한 회계연도 정기공시에서 지원동기와 면접 준비에 쓸 근거를 모읍니다. 서버는 공시 근거를 선별해 반환하고, MCP 호스트의 모델이 면접 브리핑을 작성합니다. 별도의 LLM API 키는 필요 없습니다.

## 공개 MCP 연결

MCP 클라이언트에서 원격 서버를 추가할 때 전송 방식은 **Streamable HTTP**, 서버 주소는 아래 값을 사용하세요. 공개 공시 조회에 API 키는 필요 없습니다.

```text
https://company-research-mcp.yscoder3893.workers.dev/mcp
```

연결한 뒤 채팅에서 이렇게 요청할 수 있습니다.

> 삼성전자 연구개발 직무 면접을 준비 중이야. 2025년 사업보고서와 2026년 반기보고서로 회사 방향성과 예상 질문을 정리하고, 근거마다 공시 링크를 붙여줘.

`get_company_overview`는 기업 소개와 공식 홈페이지 링크를, `get_talent_profile`은 채용 페이지와 인재·업무문화 근거를 조회합니다. 두 도구에는 회사 고유번호를 입력합니다. 응답은 검토한 근거(`reviewed`), 자동 수집 후보(`candidate`), 미확인(`unverified`) 상태를 표시합니다.

서버의 `search_companies`로 회사 고유번호를 찾고, `get_job_seeker_profile`에 그 번호를 전달하면 두 기간의 공시가 반환됩니다. 프롬프트를 지원하는 클라이언트에서는 `job_interview_brief`에 회사명과 지원 직무를 입력할 수 있습니다.

### 공개 Worker에서 삼성전자 조회 테스트

프로젝트 루트에서 아래 명령을 실행하면 공개 MCP에 직접 연결해 회사 검색, 두 기간의 공시 조회, 면접 브리핑 프롬프트를 확인합니다.

```bash
uv run --locked python - <<'PYTHON'
import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

URL = "https://company-research-mcp.yscoder3893.workers.dev/mcp"


async def main():
    async with asyncio.timeout(30):
        async with streamable_http_client(URL) as (reader, writer, _):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                matches = await session.call_tool("search_companies", {"query": "삼성전자"})
                assert not matches.isError, matches.content
                company = matches.structuredContent["result"][0]
                assert company["corp_code"] == "00126380"
                print(f"회사 검색: {company['corp_name']} ({company['corp_code']})")

                result = await session.call_tool("get_job_seeker_profile", {
                    "corp_code": company["corp_code"], "role": "연구개발",
                })
                assert not result.isError, result.content
                reports = [report for profile in result.structuredContent["profiles"]
                           for report in profile["periods"]]
                assert [(r["period_end_month"], r["status"]) for r in reports] == [
                    ("2025-12", "filed"), ("2026-06", "filed"),
                ]
                for report in reports:
                    print(f"{report['title']} | {report['source']}")

                prompt = await session.get_prompt("job_interview_brief", {
                    "company": company["corp_name"], "role": "연구개발",
                })
                assert prompt.messages
                print("프롬프트: job_interview_brief")


asyncio.run(main())
PYTHON
```

## 로컬에서 시작하기

Python 3.11 이상과 `uv`가 필요합니다. **수집 배치 → SQLite → MCP** 순서로 실행합니다. `.env.example`을 `.env`로 복사하고, `OPENDART_API_KEY=` 뒤에 발급받은 인증키를 입력하세요.

### 공시 수집 배치

```bash
cp -n .env.example .env
# .env에 인증키를 입력한 뒤 실행하세요.
uv sync
uv run --env-file .env dart-job-seeker-sync --corp-code 00126380 --year 2024
```

`--corp-code`와 `--year`를 반복하면 지정한 회사와 연도의 모든 조합을 수집합니다. `--db`로 배치의 SQLite 경로를 지정할 수 있습니다. 경로가 지정되지 않으면 현재 디렉터리의 `dart.sqlite3`을 사용합니다.

배치를 다시 실행하면 회사·연도별 자료를 갱신합니다. 일부 분기가 아직 공시되지 않았다면 조회된 분기만 저장합니다. OpenDART 요청이나 원문 파싱이 실패한 회사·연도의 이전 저장본은 유지되며, 배치는 나머지 대상을 처리한 뒤 종료 코드 1을 반환합니다. 응답의 `fetched_at`은 저장본 수집을 시작한 UTC 시각입니다.

### MCP 실행

MCP는 `DART_DB_PATH`의 SQLite를 읽기 전용으로 엽니다. OpenDART 인증키 없이 실행할 수 있고, 배치 갱신 결과는 다음 요청부터 조회됩니다.

HTTP 클라이언트를 연결하려면 배치와 동일한 `DART_DB_PATH`를 설정한 환경에서 실행하세요.

```bash
uv run dart-job-seeker --transport streamable-http
```

기본 HTTP 주소는 `http://127.0.0.1:8000/mcp`입니다. [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/v1/)

MCP 호스트가 표준 입출력으로 서버를 시작한다면 아래 설정을 사용하세요. 경로 두 곳은 실제 절대 경로로 바꾸세요.

```json
{
  "mcpServers": {
    "dart-job-seeker": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/dart-4-job-seeker", "run", "dart-job-seeker"],
      "env": {
        "DART_DB_PATH": "/absolute/path/to/dart-4-job-seeker/dart.sqlite3"
      }
    }
  }
}
```

## 로컬 MCP 사용법

- `search_companies(query)`로 회사명이나 종목코드를 검색해 저장된 법인의 OpenDART 8자리 고유번호와 조회 가능한 연도를 확인합니다.
- `get_job_seeker_profile(corp_code, year, include_optional=false, role="")`로 한 해의 자료를 조회합니다. 예를 들어 삼성전자 2024년 연구개발 직무를 준비한다면 `corp_code`에 `00126380`, `year`에 `2024`, `role`에 `연구개발`을 전달합니다.
- 호스트가 MCP 프롬프트를 지원하면 `job_interview_brief`에 회사명, 회계연도, 지원 직무를 입력하세요. 공시 링크가 포함된 회사 소개, 실적과 전략, 직원 현황, 최근 이슈, 지원동기와 예상 면접 질문을 작성하도록 안내합니다. 도구만 지원하는 호스트에서는 채팅으로 같은 브리핑을 요청할 수 있습니다.

## 추출 범위

| 구분 | 가져오는 근거 |
| --- | --- |
| 필수 | 사업보고서 I·II·IV·XI장 발췌, III장 주요 재무계정, VIII장 직원 현황과 연간 임원 경력 사례. II장의 제품 매출, 연구개발, 수주 표는 분기 비교용 근거도 제공합니다. |
| 선택 | `include_optional=true`일 때 II장 원재료와 V장 감사의견, IX장 계열회사 발췌를 추가합니다. |
| 제외 | VI·VII·X·XII장 원문은 생략합니다. 인접 보고서의 구조화 데이터에서 최대주주가 달라진 경우에만 변경 사실을 함께 제공합니다. |

현재 12월 결산 법인의 2015년 이후 공시를 조회합니다. Q1·H1·Q3·FY는 각각 3월·6월·9월·12월 기준이며, 연간 재무 수치와 반기 누적 금액은 각각 해당 누적 기간을 기준으로 합니다. 사업보고서는 대개 다음 해에 접수되므로 보고서 제목의 결산 월과 사업연도를 사용합니다. 기재정정이 있으면 최신 접수번호를 우선하며, 선택한 본문과 구조화 데이터의 접수번호가 다르면 해당 데이터가 불일치 상태로 표시됩니다.

제품 매출, 연구비, 수주는 양식이 회사마다 달라 원문 표를 발췌합니다. 발췌에는 분량 제한이 있습니다. 분기 보고서나 지표가 빠진 경우 해당 시점의 값은 비워 두고, 직원 수 증감은 인원 규모 변화로 해석합니다. 주요 계정이나 직원 API에서 사용할 값을 받지 못하면 원문의 III·VIII장 발췌를 가능한 범위에서 제공합니다.

## 검증

```bash
python3 -m unittest discover -s tests -v
```

모의 OpenDART 응답과 임시 SQLite 파일로 정정공시 선택, 누계 구분, 분기 누락, 선택 자료, 저장본 교체, 실패 시 이전 자료 유지, 읽기 전용 접근을 검증합니다.

OpenDART API 사양은 금융감독원 개발가이드를 참고했습니다. [공시정보](https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS001) · [정기보고서 주요정보](https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS002) · [정기보고서 재무정보](https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS003)

## 코스피 배치와 Cloudflare MCP

프로젝트 루트에서 KRX의 현재 코스피 상장법인 목록을 기준으로 2025년 사업보고서와 2026년 반기보고서를 수집합니다. 연도는 보고기간 종료일의 연도입니다. 결산월이 다른 회사는 KRX에 표시된 결산월을 기준으로 해당 보고서를 찾습니다.

```bash
uv run --env-file .env dart-job-seeker-sync --kospi
```

완료한 회사·기간은 다음 실행에서 건너뜁니다. 부분 실패를 재시도하려면 `--retry-partial`을, 신규 공시와 정정공시까지 다시 확인하려면 `--refresh`를 사용하세요. 공시가 없는 기간은 조회 결과에 표시됩니다. 기본 호출 예산은 실행당 12,000회이며 `--request-limit`으로 조정할 수 있습니다. OpenDART가 한도 초과 코드를 반환하면 수집을 중지하고 이미 적재한 데이터를 보존합니다.

### D1 동기화 및 Worker 실행

1. `worker/`에서 `npm ci`를 실행합니다. Cloudflare 계정에서 D1 데이터베이스를 생성한 뒤 `worker/wrangler.jsonc`의 `database_id`에 실제 ID를 입력합니다.
2. `worker/`에서 `npx wrangler d1 migrations apply dart-job-seeker --remote`를 실행해 스키마를 적용합니다.
3. 프로젝트 루트의 `.env`에 `CLOUDFLARE_ACCOUNT_ID`, `DART_D1_DATABASE_ID`, D1 쓰기 권한이 있는 `CLOUDFLARE_API_TOKEN`을 설정하고 동기화합니다.

   ```bash
   uv run --env-file .env dart-job-seeker-d1 --dry-run
   uv run --env-file .env dart-job-seeker-d1
   uv run --env-file .env dart-job-seeker-d1 --research-only
   ```

   기업 소개·채용 자료는 두 번째 D1 마이그레이션 적용 후 `--research-only`로 동기화합니다. 수집 체크포인트 반영 절차는 [공식 출처 수집 문서](.company-collection/README.md)를 참고하세요.

   업로더는 코스피 배치에서 수집한 대상 기간만 읽습니다. JSON을 D1 API의 SQL 파라미터로 보내며, 같은 저장본을 다시 보내도 기존 행의 내용은 그대로입니다.

4. `worker/`에서 `npm run check`로 타입을 확인하고 `npm run deploy`로 배포합니다. MCP 클라이언트는 배포 주소의 `/mcp`에 Streamable HTTP로 연결합니다. 기본 Worker는 공개 공시를 제공하는 읽기 전용 엔드포인트입니다.
5. `get_job_seeker_profile`에서 `year`를 생략하면 두 기간을 함께 조회합니다. `search_companies`는 D1에 적재된 회사와 연도를 반환합니다.

로컬 Worker 서버는 `worker/`에서 `npx wrangler d1 migrations apply dart-job-seeker --local`을 실행한 뒤 `npm run dev`로 시작할 수 있습니다.

### GitHub Actions 자동 배포

`dev`에 푸시하면 Worker 타입을 검사합니다. `main`에 푸시하면 검사를 통과한 Worker를 원격 D1 바인딩과 함께 배포합니다. D1 스키마 적용과 공시 업로드는 기존 절차로 실행합니다.

Cloudflare 대시보드에서 **Edit Cloudflare Workers** 템플릿으로 배포용 토큰을 만들고, 계정 범위를 이 Worker를 배포하는 계정으로 제한하세요. 계정 ID와 배포 토큰은 아래 명령으로 등록할 수 있습니다. 명령 프롬프트에 값을 붙여 넣으세요.

```bash
gh secret set CLOUDFLARE_ACCOUNT_ID --repo ysl-bots/company-research-mcp
gh secret set CLOUDFLARE_API_TOKEN --repo ysl-bots/company-research-mcp
gh workflow run deploy-worker.yml --ref main --repo ysl-bots/company-research-mcp
```

두 secret이 준비되기 전에는 타입 검사까지 실행됩니다. 수집용 OpenDART 인증키와 D1 업로드용 토큰은 로컬 환경에 둡니다. [Cloudflare GitHub Actions 가이드](https://developers.cloudflare.com/workers/ci-cd/external-cicd/github-actions/)

## 회사·인재 자료 가져오기

`company_materials.json`은 삼성전자, SK하이닉스, 현대자동차의 공식 자료에서 회사 개요·채용 절차·업무문화 근거를 선별한 버전 관리용 자료입니다. 세 법인과 DART 고유번호는 공식 DART 기업개황 팝업에서 HTTP 200 및 한글 법인명으로 확인했습니다. 각 자료에 확인 날짜, 페이지 제목, HTML 확인 방법, 이미지 처리와 범위를 기록합니다. 명시적 전사 인재 기준을 검증하지 못한 경우 별도 미검증 기록을 두며, 채용 절차와 업무문화 근거를 인재 기준으로 취급하지 않습니다. 사업부 자료는 사업부 한정으로 표시하며 이미지에만 있는 내용을 시각 확인하지 않은 경우 이를 근거에 넣지 않습니다.

인증키 없이 기존 데이터베이스와 별개인 깨끗한 SQLite에도 가져올 수 있습니다. 가져오기는 입력 전체를 먼저 검증하고 단일 트랜잭션으로 저장하며, 같은 내용을 다시 실행해도 중복 행을 만들지 않습니다. 기존 `companies`와 `profiles` 테이블은 변경하지 않습니다.

```bash
uv run dart-job-seeker-import-materials company_materials.json --db /tmp/company-materials.sqlite3
uv run python - <<'PYTHON'
import sqlite3
con = sqlite3.connect('/tmp/company-materials.sqlite3')
for code, name in con.execute('SELECT corp_code, corp_name FROM company_materials ORDER BY corp_name'):
    print(code, name)
PYTHON
```

입력 형식이 잘못되면 저장 전에 실패합니다. 회사별 JSON 전체는 `company_materials.payload`에 보존됩니다.
## 기업 리서치 자료 파일럿

[검토용 JSON](company_research_pilot.json)은 2026년 11월 21일 기준 삼성전자·SK하이닉스·현대자동차의 보도자료, 기업 블로그, 컨퍼런스, 애널리스트 리포트와 IR 자료를 담습니다. 기업별·유형별 최대 10건이며 IR 홈과 자료실 링크는 별도 집계합니다. `gaps`에는 상한에 못 미친 이유를 기록합니다.

DART 기업 정보가 들어 있는 로컬 SQLite에 아래 명령으로 적재합니다.

```bash
python -m dart_job_seeker.research_pilot company_research_pilot.json --db dart.sqlite3
```

적재기는 DART 고유번호와 종목코드를 확인한 뒤 하나의 트랜잭션으로 파일럿 자료와 기업 연결을 교체합니다. `company_research_pilot_items`에는 원문 URL당 한 행을 저장하고, `company_research_pilot_companies`에는 기업별 연결과 귀속 근거를 저장합니다.

## 기업 리서치 자료 조회

`get_company_research_materials`는 DART 고유번호로 보도자료, 기업 블로그, 컨퍼런스, 애널리스트 리포트와 IR 자료를 조회합니다. 수집 기간, 유형별 건수, 원문 출처와 자료 미확보 사유를 함께 반환합니다.

[전체 수집 자료](company_research_batch.json)와 [추가 재조사 결과](company_research_adaptive.json)를 Git에서 검토할 수 있습니다. 운영 D1에 동기화하려면 `0003_company_research_materials.sql` 마이그레이션을 적용한 뒤 아래 명령을 실행합니다.

```bash
python -m dart_job_seeker.d1 --db dart.sqlite3 --materials-snapshot company_research_batch.json
```
