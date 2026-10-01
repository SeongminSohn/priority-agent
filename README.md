# Priority Agent

Trello 카드의 작업 우선순위를 자동으로 분류하는 AI agent.

> 앱 이름은 미정이며 당분간 "Priority Agent"로 부른다.

## 현재 상태

v0.1 진행 중. 목표는 수동 triage 엔드포인트 `POST /triage/{card_id}`
(카드 fetch, structured output LLM call, DB 저장, 결과 반환)이다.
현재 동작하는 기능은 `/health`뿐이다. 나머지는 모두 계획 단계다.

## 기술 스택

- Backend: Python 3.12, FastAPI, LangGraph, LangChain, Pydantic
- Storage: Supabase (Postgres + pgvector + Auth), Alembic
- LLM: Gemini 2.5 Flash (LangChain `ChatModel` factory, 교체 가능)
- Frontend: Vanilla HTML + JS + Tailwind CDN (FastAPI static 서빙)
- Deployment: Fly.io (Docker)
- CI/CD: GitHub Actions (lint + test + deploy)
- Observability: Langfuse + structured logging
- Integration: Trello REST API + webhook (HMAC-SHA1 검증)

이 중 현재 의존성으로 들어가 있는 것은 FastAPI, LangChain, LangGraph, SQLAlchemy, Alembic, pgvector 등
backend 패키지뿐이며, 나머지(Supabase, Langfuse, Fly.io, 프론트엔드 등)는 계획이다.

## 설계 원칙

1. **Retrieval interface-first**: `app/retrieval/base.py`의 `RetrievalService` Protocol이 유일한
   검색 인터페이스다. `EmbeddingRetriever`에서 `HybridRetriever`로의 교체는 config 한 줄로
   가능해야 하며, 검색 로직을 여러 파일에 흩뿌리지 않는다.
2. **LLM model abstraction**: 모델 지정은 `app/llm/factory.py` 한 곳에서만 한다. 나머지 코드는
   LangChain `ChatModel` 인터페이스만 안다.
3. **Agent 안전장치**: LangGraph `max_steps` 가드, tool call 실패 시 에러 타입에 따른 재시도
   (retryable이면 n회, 아니면 로그로 노출), Pydantic validation 실패 시 프롬프트를 재구성해 1회 재시도.
4. **Structured everything**: API I/O, tool I/O, agent state, LLM 출력을 모두 Pydantic 모델로 정의한다.
5. **Additive 확장**: Active learning(override 기반)은 별도 `OverrideRetriever`와 프롬프트 hook으로
   추가하고, 기존 노드/프롬프트 수정은 최소화한다.

## 프로젝트 구조

```
backend/app/
├── main.py            # FastAPI entry (health check)            [존재]
├── config.py          # Settings (pydantic-settings)            [존재]
├── api/               # 라우터 (triage 등)                       [예정]
├── agent/             # LangGraph 정의, state, nodes, tools      [예정]
├── llm/               # factory.py(모델 swap point), prompts/    [예정]
├── retrieval/         # base.py(Protocol), embedding.py          [예정]
├── integrations/      # trello.py                               [예정]
├── persistence/       # models.py (SQLAlchemy), db.py            [예정]
└── auth/              # supabase.py (JWT 검증)                   [예정]
```

## 로컬 실행

사전 요구: Python 3.12, [uv](https://docs.astral.sh/uv/), Docker.

```bash
uv sync
cp .env.example .env
docker compose up -d db
cd backend && uv run uvicorn app.main:app --reload
```

서버가 뜨면 `http://localhost:8000/health`로 확인한다.

테스트와 lint는 repo 루트에서 실행한다.

```bash
uv run pytest
uv run ruff check
```

> `.env`에는 API 키 등 secrets가 들어간다. 절대 커밋하지 말 것.

## 로드맵

- **v0.1 (Week 1)**: skeleton + 수동 triage. 프론트, agent(단일 LLM call만 사용), auth, webhook은 포함하지 않는다.
  1. 프로젝트 skeleton (config, health, Docker, 툴링)
  2. retrieval 인터페이스, LLM factory, Trello client, persistence + Alembic, CI
  3. `POST /triage/{card_id}` 조립
  4. 테스트와 보안 리뷰
  5. 배포 (Fly.io)

hybrid search, semantic caching, confidence scoring 등은 이후 단계(Phase 2)로, 인터페이스만 열어둔다.

## 라이선스

MIT. 자세한 내용은 [LICENSE](LICENSE)를 참조.
