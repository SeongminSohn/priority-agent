import sys

from app.config import get_settings
from app.persistence.models import (
    AgentRun,
    AgentStep,
    Base,
    Board,
    Override,
    Priority,
    Ticket,
    TriageResult,
)


def test_priority_values() -> None:
    assert [p.value for p in Priority] == ["P0", "P1", "P2", "P3"]
    assert Priority("P0") is Priority.P0


def test_tables_and_columns() -> None:
    assert set(Base.metadata.tables) == {
        "boards",
        "tickets",
        "agent_runs",
        "agent_steps",
        "triage_results",
        "overrides",
    }
    assert {"user_id", "trello_board_id", "trello_token_encrypted", "webhook_id"} <= set(
        Board.__table__.c.keys()
    )
    assert {
        "board_id",
        "trello_card_id",
        "title",
        "description",
        "labels",
        "due_date",
        "members",
        "raw",
        "embedding",
    } <= set(Ticket.__table__.c.keys())
    assert {"run_id", "ticket_id", "category", "estimated_hours", "risk_flags"} <= set(
        TriageResult.__table__.c.keys()
    )
    assert {"original_priority", "final_priority", "ticket_text_embedding"} <= set(
        Override.__table__.c.keys()
    )


def test_embedding_dim_follows_settings() -> None:
    dim = get_settings().embedding_dim
    for col in (Ticket.__table__.c.embedding, Override.__table__.c.ticket_text_embedding):
        assert col.nullable
        assert col.type.dim == dim


def test_nullable_and_unique_constraints() -> None:
    assert Board.__table__.c.user_id.nullable
    assert Board.__table__.c.trello_board_id.unique
    assert Ticket.__table__.c.board_id.nullable
    assert Ticket.__table__.c.trello_card_id.unique


def test_foreign_keys() -> None:
    def targets(col: object) -> set[str]:
        return {fk.target_fullname for fk in col.foreign_keys}  # type: ignore[attr-defined]

    assert targets(AgentRun.__table__.c.ticket_id) == {"tickets.id"}
    assert targets(AgentStep.__table__.c.run_id) == {"agent_runs.id"}
    assert targets(TriageResult.__table__.c.run_id) == {"agent_runs.id"}
    assert targets(TriageResult.__table__.c.ticket_id) == {"tickets.id"}
    assert targets(Override.__table__.c.ticket_id) == {"tickets.id"}
    assert not TriageResult.__table__.c.ticket_id.unique


def test_check_constraints_and_vector_indexes() -> None:
    def check_names(model: type[Base]) -> set[str]:
        return {c.name for c in model.__table__.constraints if c.name and c.name.startswith("ck_")}

    assert check_names(AgentRun) == {"ck_agent_runs_status"}
    assert check_names(TriageResult) == {"ck_triage_results_priority"}
    assert check_names(Override) == {
        "ck_overrides_original_priority",
        "ck_overrides_final_priority",
    }
    for model in (Ticket, Override):
        ivf = [i for i in model.__table__.indexes if i.dialect_options["postgresql"]["using"]]
        assert [i.dialect_options["postgresql"]["using"] for i in ivf] == ["ivfflat"]


def test_agent_step_run_index_unique() -> None:
    uniques = {
        tuple(c.name for c in con.columns)
        for con in AgentStep.__table__.constraints
        if con.name == "uq_agent_steps_run_step"
    }
    assert uniques == {("run_id", "step_index")}


def test_db_module_import_does_not_connect() -> None:
    sys.modules.pop("app.persistence.db", None)
    import app.persistence.db as db

    assert db.get_engine.cache_info().currsize == 0
