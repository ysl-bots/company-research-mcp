# DART 취준생 공시 MCP

OpenDART의 한 회계연도 정기공시에서 지원동기와 면접 준비에 쓸 근거를 모읍니다. 서버는 공시 근거를 선별해 반환하고, MCP 호스트의 모델이 면접 브리핑을 작성합니다. 별도의 LLM API 키는 필요 없습니다.

## 시작하기

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

## 사용법

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
   ```

   업로더는 코스피 배치에서 수집한 대상 기간만 읽습니다. JSON을 D1 API의 SQL 파라미터로 보내며, 같은 저장본을 다시 보내도 기존 행의 내용은 그대로입니다.

4. `worker/`에서 `npm run check`로 타입을 확인하고 `npm run deploy`로 배포합니다. MCP 클라이언트는 배포 주소의 `/mcp`에 Streamable HTTP로 연결합니다. 기본 Worker는 공개 공시를 제공하는 읽기 전용 엔드포인트입니다.
5. `get_job_seeker_profile`에서 `year`를 생략하면 두 기간을 함께 조회합니다. `search_companies`는 D1에 적재된 회사와 연도를 반환합니다.

로컬 Worker 서버는 `worker/`에서 `npx wrangler d1 migrations apply dart-job-seeker --local`을 실행한 뒤 `npm run dev`로 시작할 수 있습니다.
