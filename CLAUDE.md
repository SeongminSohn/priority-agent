# Priority Agent

Trello 카드의 작업 우선순위를 자동 분류하는 AI agent. FastAPI + LangGraph + pgvector + Supabase.
상세 계획: `~/.claude/plans/streamed-gliding-ullman.md`

## Tech Stack

- Backend: Python 3.12, FastAPI, LangGraph, LangChain, Pydantic
- Storage: Supabase (Postgres + pgvector + Auth), Alembic
- LLM: Gemini 2.5 Flash via LangChain `ChatModel` factory (교체 가능해야 함)
- Frontend: Vanilla HTML + JS + Tailwind CDN (FastAPI static 서빙)
- Deployment: Fly.io (Docker)
- CI/CD: GitHub Actions (lint + test + deploy)
- Observability: Langfuse + structured logging
- Integration: Trello REST API + webhook (HMAC-SHA1 검증)

## 핵심 설계 원칙 (반드시 지킬 것)

1. **Retrieval Interface-first** — `app/retrieval/base.py`의 `RetrievalService` Protocol이 유일한 검색 인터페이스. `EmbeddingRetriever` → `HybridRetriever` 교체는 config 한 줄로 가능해야 한다. 검색 로직을 여러 파일에 흩뿌리지 말 것.
2. **Additive 확장 원칙** — Active learning(override 기반)은 별도 `OverrideRetriever` + 프롬프트 hook으로 추가한다. 기존 노드/프롬프트 수정을 최소화할 것.
3. **LLM Model Abstraction** — 모델 지정은 `app/llm/factory.py` 한 곳에서만. 나머지 코드는 `ChatModel` 인터페이스만 알아야 한다.
4. **Agent 안전장치** — LangGraph `max_steps` 가드, tool call 실패 시 재시도(에러 타입 확인 후 retryable이면 n회, 아니면 로그로 노출), Pydantic validation 실패 시 프롬프트 재구성 후 1회 재시도.
5. **Structured everything** — API I/O, tool I/O, agent state, LLM 출력 모두 Pydantic 모델로 정의.

## 프로젝트 구조

```
backend/app/
├── main.py           # FastAPI entry, static mount
├── config.py         # Settings (pydantic-settings)
├── api/               # 라우터 (webhooks, boards, triage, overrides, dashboard)
├── agent/             # LangGraph 정의, state, nodes/, tools/
├── llm/               # factory.py(★ 모델 swap point), prompts/
├── retrieval/          # base.py(★ Protocol), embedding.py, override.py
├── integrations/       # trello.py
├── persistence/         # models.py (SQLAlchemy), db.py
└── auth/               # supabase.py (JWT 검증)
```

★ 표시 파일(`retrieval/base.py`, `llm/factory.py`)은 향후 확장성을 결정하므로 인터페이스 설계에 특히 신경 쓸 것.

## 코딩 규칙

- 모든 함수/클래스에 타입 힌트. Pydantic v2 스타일 사용.
- lint/format: `ruff` (CI에서 강제).
- 테스트: `pytest`. 새 기능에는 최소 unit test 동반.
- 커밋 전 로컬에서 `ruff check` + `pytest` 통과 확인.
- 주석은 WHY가 비자명할 때만. WHAT을 설명하는 주석 금지.
- 과도한 추상화 금지. Phase 2 항목(hybrid search, semantic caching, confidence scoring)을 미리 구현하지 말 것 — 인터페이스만 열어둔다.

## 현재 Phase

v0.1 (Week 1): Skeleton + 수동 triage. 프론트 없음, agent 없음(단일 LLM call), auth 없음, webhook 없음.
목표: `POST /triage/{card_id}` — 카드 fetch → structured output LLM call → DB 저장 → 결과 반환.

## 작업 방식

- 이 프로젝트는 multi-agent 워크플로 학습이 부수 목표. 구현은 가능한 한 subagent(backend-implementer, test-writer, security-reviewer)에 위임하고, 사용자는 검증/승인 역할.
- 아키텍처 변경, 새 의존성 추가, 파일 구조 변경은 사용자 확인 필요.

## v0.1 실행 루프

메인 세션은 코디네이터다. 구현은 subagent에 맡기고, 코디네이터는 작업 분배, 검증, git, 사용자 소통을 맡는다.

**작업 순서** (같은 단계 안의 항목은 병렬 subagent로 동시 실행):
1. Phase 1 (subagent 1개): `pyproject.toml`(uv), `.env.example`, `Dockerfile`, `docker-compose.yml`(Postgres + pgvector), `app/config.py`, `app/main.py`(health check), pytest/ruff 설정
2. Phase 2 (병렬): `retrieval/base.py` · `llm/factory.py` · `integrations/trello.py` · `persistence/` + Alembic 초기 마이그레이션 · `.github/workflows/ci.yml` · `README.md` 초안
3. Phase 3: `api/triage.py` (`POST /triage/{card_id}`)로 Phase 2 결과물 조립
4. Phase 4 (병렬): test-writer(triage 엔드포인트), security-reviewer(trello client, config secrets)
5. 배포: `deploy.yml` + `fly.toml`

**작업 단위 루프**:
1. backend-implementer에 스펙 전달 (수정할 파일 목록을 명시해서 병렬 작업끼리 파일이 겹치지 않게 한다)
2. 코디네이터가 `uv run ruff check && uv run pytest` 실행
3. 실패하면 에러 출력을 같은 subagent에 넘겨 수정. 최대 3회
4. 3회 안에 통과 못 하면 멈추고 사용자에게 원인과 선택지를 보고
5. 통과하면 feature 브랜치에 커밋. 단계가 끝나면 push → PR 생성 → CI 통과 확인 → 병합까지 코디네이터가 자율 진행하고, 사용자에게는 결과를 보고한다

새 패키지가 필요하면 subagent가 보고하고, 코디네이터가 `uv add`로 추가한다. 계획서에 있는 스택(FastAPI, LangChain, LangGraph, SQLAlchemy, Alembic, pgvector, pydantic-settings, httpx, pytest, ruff)은 추가 승인 없이 넣고, 계획서에 없는 패키지만 사용자에게 묻는다.

## 모델 사용 규칙

| 역할 | 모델 | 이유 |
|---|---|---|
| 메인 세션: 계획, 설계 결정 | Opus | 아키텍처와 ★ 인터페이스 설계는 판단 품질이 중요 |
| 메인 세션: 실행 루프 조율 | Sonnet | 분배, 검증 실행, git 작업은 정해진 절차 |
| backend-implementer, test-writer | Sonnet (frontmatter 지정) | 스펙이 주어진 구현은 Sonnet으로 충분하고 병렬 실행 비용이 낮음 |
| security-reviewer | Opus (frontmatter 지정) | 호출 횟수는 적고 놓치면 비용이 큼 |
| 파일 위치 찾기, 단순 조회 | Haiku (Explore 등) | 읽기 전용 검색 |

- ★ 파일(`retrieval/base.py`, `llm/factory.py`)은 구현을 Sonnet에 맡기되, 코디네이터가 인터페이스 시그니처를 스펙에 직접 적어서 넘긴다.
- 작업 단위 루프에서 3회 수정 실패한 작업은 Opus로 한 번 더 시도한 뒤에 사용자에게 보고한다 (Agent 호출 시 `model: "opus"`로 override).
- 사용자가 `/model`로 메인 세션 모델을 바꾸면 그 선택을 따른다.

## 사용자에게 요청할 항목 (필요해지는 시점에만)

미리 멈추지 않는다. 아래 항목이 필요한 작업만 보류하고 나머지는 계속 진행한 뒤, 해당 시점에 무엇을, 어디서 발급하는지, 어느 `.env` 변수에 넣는지 안내해서 요청한다. 단위 테스트는 외부 API를 mock하므로 키 없이 진행한다.

| 항목 | 요청 시점 | 위치 |
|---|---|---|
| Gemini API 키 | LLM 실제 호출 / triage e2e 테스트 | `GEMINI_API_KEY` |
| Trello API 키·토큰, 테스트 보드 | Trello client 실제 호출 테스트 | `TRELLO_API_KEY`, `TRELLO_TOKEN` |
| 임베딩 차원 확정 (기본안 1536, `gemini-embedding-001`) | Alembic 마이그레이션 작성 직전 | 스키마 |
| 앱 이름 (미정, 당분간 Priority Agent) | README 작성 시 | README, API 타이틀 |
| Fly.io 계정·결제 수단, `flyctl` 설치 | 배포 단계 | GitHub secret `FLY_API_TOKEN` |
| Supabase 프로젝트 | v0.3 (v0.1은 로컬 Postgres) | `DATABASE_URL` |

## Git 워크플로 (반드시 지킬 것)

- **브랜치 전략**: 기능/Phase별로 브랜치를 만들고 PR로 main에 병합한다. main에는 직접 push하지 않는다 (최초 초기 커밋 제외).
- **자율 진행**: 커밋, push, PR 생성, 병합 모두 사용자 승인 없이 코디네이터가 진행한다. 단, 병합 전 아래 게이트를 모두 통과해야 한다.
  1. 로컬 `uv run ruff check` + `uv run pytest` 통과
  2. push 후 GitHub Actions CI 통과 (`gh pr checks`로 확인, CI가 아직 없으면 로컬 검증만)
  3. 필요한 경우 security-reviewer 리뷰에서 high 이슈 없음 (보안 민감 코드가 포함된 PR)
- **게이트 실패 시**: 병합하지 않는다. 작업 단위 루프(최대 3회 수정)를 따르고, 그래도 안 되면 멈추고 보고한다.
- **여전히 사용자 확인이 필요한 것**: 아키텍처 변경, 새 의존성(계획서 외), 파일 구조 변경, force push, main 히스토리 재작성, 브랜치 보호 설정 변경, repo 가시성/삭제 등 되돌리기 어려운 작업.
- **병합 후**: 사용자에게 PR 링크, 변경 요약, 검증 결과를 보고한다. 문제가 있으면 사용자가 revert를 요청할 수 있다.
- **커밋 메시지**: Conventional Commits, 영어 (`feat:`, `fix:`, `chore:`, `test:`, `docs:`, `refactor:` 등).
- subagent(backend-implementer 등)는 git 커밋/푸시/PR 작업을 직접 수행하지 않는다. 이 워크플로는 코디네이터(메인 세션)만 담당한다.
