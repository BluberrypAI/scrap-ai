# Re:Source AI

중소 제조기업의 폐자원 처리 의사결정을 지원하는 **AI·지식 기반 서비스 레포**입니다.
공정·시설 후보 탐색, 근거 문서 검색, 법규·시설 검증 지원, 비용·탄소 계산을 담당합니다.

- 전체 실행 흐름: [docs/pipeline-baseline.md](docs/pipeline-baseline.md)
- 코드와 데이터 구조: [docs/architecture.md](docs/architecture.md)
- SCRAP 처리경로 API: [docs/scrap-next-step.md](docs/scrap-next-step.md)
- 의사결정 연구 검토: [docs/scrap-decision-research.md](docs/scrap-decision-research.md)
- 팀 공통 규칙: [resource-ai-team/.github CONTRIBUTING](https://github.com/resource-ai-team/.github/blob/main/CONTRIBUTING.md)

## 조직 레포 구성

| 레포 | 역할 |
| --- | --- |
| `Re_ai` (이 레포) | AI Agent, RAG, Knowledge Graph, 비용·탄소 계산 |
| `Re_server` | API Gateway, 인증·회원, 기업 정보, 업무 데이터 |
| `Re_client` | 사용자 화면 |
| `Re_infra` | 전체 통합 실행, 컨테이너 구성, 클라우드 인프라 |
| `.github` | 공통 규칙과 Issue·PR 템플릿 |

## 빠른 시작

### A. macOS·Linux

필수 환경은 Python 3.11 이상과 Docker Desktop입니다.

```bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,embeddings]'
```

`.env`에서 아래 값을 설정합니다.

```dotenv
NEO4J_PASSWORD=로컬에서_사용할_비밀번호
LAW_API_OC=국가법령정보_공동활용_API_OC
```

PostgreSQL·pgvector와 Neo4j를 실행합니다.

```bash
docker compose -f docker-compose.yml -f docker-compose.neo4j.yml up -d
```

DB 스키마와 지식그래프를 준비합니다.

```bash
resource-rag init-db
resource-kg import
resource-kg audit
```

공식 법령·판례 데이터를 수집하고 인덱싱합니다.

```bash
legal-rag collect
legal-rag index
resource-rag build-public-evidence
resource-rag index data/official/public_evidence_summaries.jsonl
```

API를 실행합니다.

```bash
uvicorn resource_ai.main:app --reload
```

실행 후 확인:

- Swagger UI: http://127.0.0.1:8000/docs
- 상태 확인: http://127.0.0.1:8000/health
- Neo4j Browser: http://127.0.0.1:7474

```bash
curl http://127.0.0.1:8000/health
```

정상 응답:

```json
{"status":"ok"}
```

### B. Windows PowerShell

```powershell
Copy-Item .env.example .env
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,embeddings]"
docker compose -f docker-compose.yml -f docker-compose.neo4j.yml up -d
.\.venv\Scripts\resource-rag.exe init-db
.\.venv\Scripts\resource-kg.exe import
.\.venv\Scripts\resource-kg.exe audit
.\.venv\Scripts\legal-rag.exe collect
.\.venv\Scripts\legal-rag.exe index
.\.venv\Scripts\uvicorn.exe resource_ai.main:app --reload
```

### C. 전체 서비스 통합 실행

이 레포의 Docker Compose는 AI 로컬 개발용 PostgreSQL·Neo4j만 실행합니다.
`Re_client → Re_server → Re_ai` 전체 서비스 실행과 배포는 `Re_infra`의 Compose·운영 절차를 따릅니다.


## 기술스택

| 영역 | 선택 |
| --- | --- |
| 언어·API | Python 3.11+ / FastAPI |
| RAG 저장소 | PostgreSQL 16 / pgvector |
| 지식그래프 | Neo4j 5 |
| 검색 | Vector Search + Keyword Search + RRF + Reranking |
| 임베딩 | `intfloat/multilingual-e5-small` |
| DB 클라이언트 | psycopg / Neo4j Python Driver |
| 품질 | pytest / ruff |
| 로컬 인프라 | Docker Compose |

`EMBEDDING_PROVIDER=hash`는 테스트용이며 실제 의미 검색에는 사용하지 않습니다.

## 현재 구조

```text
Re_ai/
├── src/resource_ai/
│   ├── agents/            후보 조사 실행과 RAG·KG 호출
│   ├── api/               FastAPI 라우터와 입출력 스키마
│   ├── calculators/       비용·탄소·시나리오 비교
│   ├── core/              설정과 의존성 구성
│   ├── evidence/          공통 근거 문서·검색 결과 모델
│   ├── integrations/      국가법령정보 공동활용 API 연동
│   ├── knowledge_graph/   Neo4j 적재·공정 경로·시설 후보 조회
│   ├── rag/               수집·검증·청킹·임베딩·검색
│   └── rules/             검수된 판정 규칙 골격
├── migrations/            PostgreSQL·pgvector 마이그레이션
├── data/                  설정·샘플·평가 데이터
├── docs/                  아키텍처·실행·검증 문서
├── tests/                 단위·통합 테스트
├── docker-compose.yml     PostgreSQL·pgvector
└── docker-compose.neo4j.yml
```

## 주요 API

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| `GET` | `/health` | API 프로세스 상태 확인 |
| `GET` | `/rag/search` | RAG 근거 검색 |
| `GET` | `/kg/ontology` | 지식그래프 온톨로지 조회 |
| `GET` | `/kg/planning-coverage` | 공정·시설 연결 범위 진단 |
| `POST` | `/kg/candidates` | 소재 기반 공정 후보 조회 |
| `POST` | `/kg/candidate-pool` | 전체 후보 조사 묶음 조회 |
| `POST` | `/kg/process-plans` | 공정·시설 경로 후보 생성 |
| `POST` | `/kg/facility-candidates` | 시설 후보 조회 |
| `POST` | `/kg/facility-assessment` | 시설 공개 조건 대조 |
| `POST` | `/kg/assessed-plans` | 경로 생성과 시설 조건 대조 |
| `POST` | `/analysis/research` | KG 후보와 법률 근거 연결 |
| `POST` | `/analysis/compare-scenarios` | 비용·탄소·Pareto 비교 |

API별 입력·출력과 현재 한계는 [docs/api.md](docs/api.md)와 실행 중인 Swagger UI에서 확인합니다.

## 테스트

```bash
ruff check .
ruff format --check .
pytest -q
```

실제 PostgreSQL 통합 테스트:

```bash
docker compose up -d postgres
RUN_POSTGRES_INTEGRATION=true pytest -q tests/integration/test_multi_corpus_postgres.py
```

실제 Neo4j 통합 테스트는 `RUN_NEO4J_INTEGRATION=true`를 지정해 실행합니다.

Legal·Technical RAG 검색 품질은 적재가 완료된 PostgreSQL에서 다음과 같이 확인합니다.

```bash
resource-rag evaluate --cases data/config/retrieval_eval.json
```

## 문서

| 문서 | 내용 |
| --- | --- |
| [docs/pipeline-baseline.md](docs/pipeline-baseline.md) | 전체 서비스 파이프라인과 역할 경계 |
| [docs/architecture.md](docs/architecture.md) | 코드·DB·서비스 구조 |
| [docs/api.md](docs/api.md) | API 목록과 오류 처리 |
| [docs/rag-multi-corpus.md](docs/rag-multi-corpus.md) | RAG 문서 계약과 코퍼스별 정책 |
| [docs/knowledge-graph.md](docs/knowledge-graph.md) | KG 적재·조회·검증 |
| [docs/candidate-analysis.md](docs/candidate-analysis.md) | 후보 조사와 비용·탄소 비교 |
| [data/README.md](data/README.md) | 데이터 위치·출처·권리 관리 |
| [docs/README.md](docs/README.md) | 전체 문서 안내 |

## 기여 방법

1. Jira 티켓과 GitHub Issue를 생성합니다.
2. `develop`에서 `feat/깃허브이슈번호-작업명` 브랜치를 만듭니다.
3. 커밋은 `feat:`, `fix:`, `refactor:`, `docs:` 머리말을 사용합니다.
4. 모든 PR은 `develop`을 대상으로 생성하고 CI와 리뷰를 통과합니다.
5. PR이 병합되면 Jira 티켓을 `Done`으로 변경합니다.

`develop → main` 배포 PR은 인프라 담당자만 병합합니다.

## SCRAP 반입 근거 검토

SCRAP의 공정·업체 후보는 scrap-server의 PostgreSQL에서 조회합니다. AI 서비스는 서버가 보낸 후보별 공개 근거를 검토하고, 요청이 있을 때 PP 공정의 공개 웹 조사 단서를 반환합니다.

| scrap-server API | scrap-ai 내부 API | 역할 |
| --- | --- | --- |
| `POST /api/treatment/intake-review` | `POST /treatment/intake-reviews` | 선택한 한 업체의 반입 근거 검토 |
| `POST /api/treatment/intake-reviews` | `POST /treatment/intake-reviews` | 현재 단계의 모든 업체 후보 검토 |
| `POST /api/treatment/facility-web-search` | `POST /treatment/facility-web-search` | PP 공정에 대한 추가 공개 웹 조사 |

서버와 AI에 같은 `SCRAP_INTAKE_SERVICE_TOKEN`을 설정하고 AI에 Azure Responses 배포를 설정합니다. 검토 결과와 조사 단서는 후보 판단에 사용하며 실제 인계 확인은 서버에서 따로 진행합니다.
