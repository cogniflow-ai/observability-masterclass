"""Tests for v4 features: vault, input_schema, approval_routes validation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.exceptions import (
    PipelineValidationError, SchemaViolationError,
)
from orchestrator.schema import (
    validate_input_schema, input_schema_from_agent_config,
)
from orchestrator.validate import validate_pipeline
from orchestrator.vault import (
    AuditCtx, Vault, has_any_marker, resolve_vault_path,
)


class FakeLog:
    """Minimal EventLog stand-in: records the v4 event methods."""

    def __init__(self) -> None:
        self.substituted: list[dict] = []
        self.missing:     list[dict] = []
        self.leaked:      list[dict] = []

    def secret_substituted(self, **kw):    self.substituted.append(kw)
    def secret_missing(self, **kw):        self.missing.append(kw)
    def secret_leaked(self, **kw):         self.leaked.append(kw)


# ══════════════════════════════════════════════════════════════════════════════
# Vault — CRUD + audit
# ══════════════════════════════════════════════════════════════════════════════

def _vault(tmp_path: Path) -> Vault:
    return Vault(tmp_path / "secrets.db")


def test_vault_put_get_roundtrip(tmp_path):
    v = _vault(tmp_path)
    v.put("DB_PASSWORD", "s3kret", description="staging DB",
          tags=["auth", "dev"], pipeline="demo")
    assert v.get("DB_PASSWORD") == "s3kret"
    meta = v.get_metadata("DB_PASSWORD")
    assert meta["name"] == "DB_PASSWORD"
    assert meta["description"] == "staging DB"
    assert meta["tags"] == ["auth", "dev"]
    assert meta["origin_pipeline"] == "demo"


def test_vault_put_update_preserves_origin(tmp_path):
    v = _vault(tmp_path)
    v.put("X", "v1", pipeline="first")
    v.put("X", "v2", pipeline="second")  # update — origin not overwritten
    assert v.get("X") == "v2"
    meta = v.get_metadata("X")
    assert meta["origin_pipeline"] == "first"


def test_vault_invalid_name_rejected(tmp_path):
    v = _vault(tmp_path)
    with pytest.raises(ValueError):
        v.put("1NUMERIC_FIRST", "value")
    with pytest.raises(ValueError):
        v.put("HAS-DASH", "value")
    with pytest.raises(ValueError):
        v.put("", "value")


def test_vault_empty_value_rejected(tmp_path):
    v = _vault(tmp_path)
    with pytest.raises(ValueError):
        v.put("OK", "")


def test_vault_delete(tmp_path):
    v = _vault(tmp_path)
    v.put("X", "v")
    assert v.delete("X") is True
    assert v.get("X") is None
    assert v.delete("X") is False  # already gone


def test_vault_list_never_returns_value(tmp_path):
    v = _vault(tmp_path)
    v.put("A", "aaa", description="alpha")
    v.put("B", "bbb", description="beta")
    rows = v.list()
    assert {r["name"] for r in rows} == {"A", "B"}
    for r in rows:
        assert "value" not in r
        assert "aaa" not in json.dumps(r)
        assert "bbb" not in json.dumps(r)


def test_vault_get_metadata_missing_returns_none(tmp_path):
    v = _vault(tmp_path)
    assert v.get_metadata("NOPE") is None


# ══════════════════════════════════════════════════════════════════════════════
# Vault — rehydrate / redact / scan_leaks
# ══════════════════════════════════════════════════════════════════════════════

def test_rehydrate_outbound_replaces_placeholders(tmp_path):
    v = _vault(tmp_path)
    v.put("DB_URL", "postgres://user:pw@host/db")
    log = FakeLog()
    text = "Connect with <<secret:DB_URL>> and go."
    out = v.rehydrate(
        text, ctx=AuditCtx(run_id="r1", pipeline_name="p", agent_id="a",
                           file="04_context"),
        direction="outbound", event_log=log,
    )
    assert out == "Connect with postgres://user:pw@host/db and go."
    # Event emitted
    assert any(e["direction"] == "outbound" and e["secret_name"] == "DB_URL"
               for e in log.substituted)


def test_rehydrate_inbound_emits_inbound_direction(tmp_path):
    v = _vault(tmp_path)
    v.put("TOKEN", "abc123")
    log = FakeLog()
    text = "Token: <<secret:TOKEN>>"
    out = v.rehydrate(
        text, ctx=AuditCtx(agent_id="a", file="05_output"),
        direction="inbound", event_log=log,
    )
    assert "abc123" in out
    assert "<<secret:TOKEN>>" not in out
    assert log.substituted and log.substituted[0]["direction"] == "inbound"


def test_rehydrate_missing_secret_left_intact(tmp_path):
    v = _vault(tmp_path)
    log = FakeLog()
    text = "Hi <<secret:NOPE>>, <<secret:OTHER>>"
    out = v.rehydrate(
        text, ctx=AuditCtx(agent_id="a", file="04_context"),
        direction="outbound", event_log=log,
    )
    assert out == text
    names = {e["secret_name"] for e in log.missing}
    assert names == {"NOPE", "OTHER"}


def test_rehydrate_mixed_known_and_unknown(tmp_path):
    v = _vault(tmp_path)
    v.put("KNOWN", "the-value")
    log = FakeLog()
    text = "known=<<secret:KNOWN>>, unknown=<<secret:UNKNOWN>>"
    out = v.rehydrate(
        text, ctx=AuditCtx(agent_id="a", file="04_context"),
        direction="outbound", event_log=log,
    )
    assert out == "known=the-value, unknown=<<secret:UNKNOWN>>"


def test_rehydrate_noop_when_no_marker(tmp_path):
    v = _vault(tmp_path)
    out = v.rehydrate("hello world",
                      ctx=AuditCtx(file="x"), direction="outbound")
    assert out == "hello world"


def test_rehydrate_invalid_direction(tmp_path):
    v = _vault(tmp_path)
    with pytest.raises(ValueError):
        v.rehydrate("text", ctx=AuditCtx(), direction="sideways")


def test_scan_leaks_finds_raw_value(tmp_path):
    v = _vault(tmp_path)
    v.put("API_KEY", "sk-live-ZZZ-9999")
    log = FakeLog()
    response = "I used the key sk-live-ZZZ-9999 to call the API"
    leaked = v.scan_leaks(
        response, ctx=AuditCtx(agent_id="a", file="response_raw"),
        event_log=log,
    )
    assert leaked == ["API_KEY"]
    assert log.leaked and log.leaked[0]["secret_name"] == "API_KEY"


def test_scan_leaks_no_value_no_event(tmp_path):
    v = _vault(tmp_path)
    v.put("K", "raw-value")
    log = FakeLog()
    leaked = v.scan_leaks(
        "No secrets here, just <<secret:K>> placeholder.",
        ctx=AuditCtx(agent_id="a", file="response_raw"),
        event_log=log,
    )
    assert leaked == []
    assert log.leaked == []


def test_redact_values_roundtrip(tmp_path):
    v = _vault(tmp_path)
    v.put("PW", "hunter2")
    redacted = v.redact_values(
        "password=hunter2, user=bob",
        ctx=AuditCtx(agent_id="a", file="01_system"),
    )
    assert redacted == "password=<<secret:PW>>, user=bob"


# ══════════════════════════════════════════════════════════════════════════════
# Vault — audit log
# ══════════════════════════════════════════════════════════════════════════════

def test_audit_records_names_never_values(tmp_path):
    v = _vault(tmp_path)
    v.put("DB_URL", "postgres://p@h/d")
    v.rehydrate(
        "x=<<secret:DB_URL>>",
        ctx=AuditCtx(run_id="r1", pipeline_name="p1",
                     agent_id="agent", file="04_context"),
        direction="outbound",
    )
    rows = v.audit(run_id="r1")
    assert rows, "expected at least one audit row"
    # Never the value itself in any column.
    for row in rows:
        assert "postgres://p@h/d" not in json.dumps(row)
    assert any(r["direction"] == "outbound" and r["secret_name"] == "DB_URL"
               for r in rows)


def test_audit_filters_by_pipeline(tmp_path):
    v = _vault(tmp_path)
    v.put("X", "vvv")
    for pipeline in ("a", "b"):
        v.rehydrate("<<secret:X>>",
                    ctx=AuditCtx(pipeline_name=pipeline, agent_id="g",
                                 file="04_context"),
                    direction="outbound")
    rows_a = v.audit(pipeline_name="a")
    rows_b = v.audit(pipeline_name="b")
    assert all(r["pipeline_name"] == "a" for r in rows_a)
    assert all(r["pipeline_name"] == "b" for r in rows_b)


def test_pipeline_link_records_usage(tmp_path):
    v = _vault(tmp_path)
    v.put("TOKEN", "v")
    v.rehydrate(
        "<<secret:TOKEN>>",
        ctx=AuditCtx(pipeline_name="demo", agent_id="a", file="04_context"),
        direction="outbound",
    )
    rows = v.usage("TOKEN")
    assert [r["pipeline_name"] for r in rows] == ["demo"]


# ══════════════════════════════════════════════════════════════════════════════
# Vault — helpers
# ══════════════════════════════════════════════════════════════════════════════

def test_has_any_marker():
    assert has_any_marker("hello <<secret:X>> world")
    assert not has_any_marker("hello world")
    assert not has_any_marker("")


def test_resolve_vault_path_prefers_pipelines_parent(tmp_path):
    pipelines = tmp_path / "pipelines"
    pipelines.mkdir()
    pipe = pipelines / "demo"
    pipe.mkdir()
    assert resolve_vault_path(pipe) == pipelines / "secrets.db"


def test_resolve_vault_path_falls_back_to_state(tmp_path):
    pipe = tmp_path / "standalone"
    pipe.mkdir()
    # parent is tmp_path, not named "pipelines" → fallback
    assert resolve_vault_path(pipe) == pipe / ".state" / "secrets.db"


def test_resolve_vault_path_respects_explicit(tmp_path):
    explicit = tmp_path / "custom" / "vault.db"
    assert resolve_vault_path(tmp_path, str(explicit)) == explicit


# ══════════════════════════════════════════════════════════════════════════════
# Input schema
# ══════════════════════════════════════════════════════════════════════════════

def test_input_schema_has_sections_pass():
    schema = {"mode": ["has_sections"],
              "sections": ["Problem", "Goals"]}
    upstream = {"pm": "# Problem\nstuff\n## Goals\nmore"}
    validate_input_schema("architect", schema, upstream)  # does not raise


def test_input_schema_has_sections_missing():
    schema = {"mode": ["has_sections"],
              "sections": ["Problem", "Goals", "Constraints"]}
    upstream = {"pm": "# Problem\n\n## Goals\nsome content"}
    with pytest.raises(SchemaViolationError) as excinfo:
        validate_input_schema("architect", schema, upstream)
    exc = excinfo.value
    assert exc.phase == "input"
    assert any("Constraints" in v for v in exc.violations)


def test_input_schema_require_upstream_narrows():
    schema = {"mode": ["contains"],
              "contains": ["approved"],
              "require_upstream": ["pm"]}
    upstream = {
        "pm":       "Status: approved",
        "random":   "not related",        # not required → ignored
    }
    # pm satisfies; random not checked
    validate_input_schema("downstream", schema, upstream)


def test_input_schema_static_required_empty_fails():
    schema = {"mode": [], "static_inputs_required": True}
    with pytest.raises(SchemaViolationError) as excinfo:
        validate_input_schema("a", schema, {}, static_inputs={})
    assert "static_inputs_required" in "\n".join(excinfo.value.violations)


def test_input_schema_static_required_with_content_passes():
    schema = {"mode": [], "static_inputs_required": True}
    validate_input_schema(
        "a", schema, {},
        static_inputs={"fixture.yaml": "key: value"},
    )


def test_input_schema_missing_upstream_reported():
    schema = {"mode": ["contains"], "contains": ["x"],
              "require_upstream": ["pm"]}
    with pytest.raises(SchemaViolationError) as excinfo:
        validate_input_schema("a", schema, {})  # no pm
    assert any("produced no output" in v for v in excinfo.value.violations)


def test_input_schema_reads_from_agent_config(tmp_path):
    cfg = {"input_schema": {"mode": ["contains"], "contains": ["ok"]}}
    (tmp_path / "00_config.json").write_text(json.dumps(cfg), encoding="utf-8")
    s = input_schema_from_agent_config(tmp_path)
    assert s is not None
    assert s["mode"] == ["contains"]


# ══════════════════════════════════════════════════════════════════════════════
# validate_pipeline — input_schema + approval_routes
# ══════════════════════════════════════════════════════════════════════════════

def _make_pipeline(tmp_path: Path, extra_agents_cfg: dict[str, dict] = None,
                   cyclic: bool = False) -> Path:
    """Write a minimal two-agent pipeline and return its dir."""
    p = tmp_path / "pl"
    p.mkdir()
    (p / "pipeline.json").write_text(json.dumps({
        "name": "test",
        "agents": [
            {"id": "pm",        "depends_on": []},
            {"id": "architect", "depends_on": ["pm"]},
        ],
        "edges": (
            [{"from": "pm", "to": "architect",  "type": "feedback",
              "directed": False}]
            if cyclic else []
        ),
        "termination": (
            {"strategy": "all_done", "max_cycles": 5} if cyclic else {}
        ),
        "tags": ({"domain": ["test"]} if cyclic else {}),
    }), encoding="utf-8")
    for aid in ("pm", "architect"):
        d = p / aid
        d.mkdir()
        (d / "01_system.md").write_text(f"You are {aid}.", encoding="utf-8")
        (d / "02_prompt.md").write_text(f"Do {aid} work.", encoding="utf-8")
        cfg = (extra_agents_cfg or {}).get(aid)
        if cfg:
            (d / "00_config.json").write_text(
                json.dumps(cfg), encoding="utf-8"
            )
    return p


def test_validate_accepts_valid_input_schema(tmp_path):
    pl = _make_pipeline(tmp_path, {
        "architect": {
            "input_schema": {
                "mode": ["has_sections"],
                "sections": ["Problem"],
                "require_upstream": ["pm"],
            },
        },
    })
    spec = validate_pipeline(pl)
    assert spec["name"] == "test"


def test_validate_rejects_unknown_input_schema_mode(tmp_path):
    pl = _make_pipeline(tmp_path, {
        "architect": {"input_schema": {"mode": ["made_up_mode"]}},
    })
    with pytest.raises(PipelineValidationError) as excinfo:
        validate_pipeline(pl)
    assert any("unknown input_schema mode" in e.lower()
               for e in excinfo.value.errors)


def test_validate_rejects_require_upstream_not_in_depends_on(tmp_path):
    pl = _make_pipeline(tmp_path, {
        "architect": {"input_schema": {
            "mode": ["contains"], "contains": ["x"],
            "require_upstream": ["nobody"],
        }},
    })
    with pytest.raises(PipelineValidationError) as excinfo:
        validate_pipeline(pl)
    errors = excinfo.value.errors
    assert any("references unknown agent" in e or "not in depends_on" in e
               for e in errors)


def test_validate_rejects_approval_route_to_self(tmp_path):
    pl = _make_pipeline(tmp_path, {
        "architect": {
            "requires_approval": True,
            "approval_routes": {
                "on_reject": {"target": "architect", "include": ["output"]},
            },
        },
    }, cyclic=True)
    with pytest.raises(PipelineValidationError) as excinfo:
        validate_pipeline(pl)
    assert any("V-APPROVE-001" in e for e in excinfo.value.errors)


def test_validate_rejects_approval_route_to_unknown_agent(tmp_path):
    pl = _make_pipeline(tmp_path, {
        "architect": {
            "requires_approval": True,
            "approval_routes": {
                "on_reject": {"target": "ghost", "include": ["output"]},
            },
        },
    }, cyclic=True)
    with pytest.raises(PipelineValidationError) as excinfo:
        validate_pipeline(pl)
    assert any("V-APPROVE-001" in e for e in excinfo.value.errors)


def test_validate_rejects_approval_routes_without_requires_approval(tmp_path):
    pl = _make_pipeline(tmp_path, {
        "architect": {
            "approval_routes": {
                "on_reject": {"target": "pm", "include": ["output"]},
            },
        },
    }, cyclic=True)
    with pytest.raises(PipelineValidationError) as excinfo:
        validate_pipeline(pl)
    assert any("requires_approval is not true" in e for e in excinfo.value.errors)


def test_validate_rejects_approval_routes_in_dag_mode(tmp_path):
    # DAG (no feedback/peer edges) → approval_routes is unsupported.
    pl = _make_pipeline(tmp_path, {
        "architect": {
            "requires_approval": True,
            "approval_routes": {
                "on_reject": {"target": "pm", "include": ["output"]},
            },
        },
    }, cyclic=False)
    with pytest.raises(PipelineValidationError) as excinfo:
        validate_pipeline(pl)
    assert any("only supported on cyclic" in e for e in excinfo.value.errors)


def test_validate_accepts_approval_routes_in_cyclic_mode(tmp_path):
    pl = _make_pipeline(tmp_path, {
        "architect": {
            "requires_approval": True,
            "approval_routes": {
                "on_reject":  {"target": "pm", "include": ["output","note"]},
                "on_approve": {"target": "pm", "include": ["output"],
                               "mode": "task"},
            },
        },
    }, cyclic=True)
    spec = validate_pipeline(pl)
    assert spec is not None


def test_validate_rejects_invalid_include_entry(tmp_path):
    pl = _make_pipeline(tmp_path, {
        "architect": {
            "requires_approval": True,
            "approval_routes": {
                "on_reject": {"target": "pm", "include": ["bogus_part"]},
            },
        },
    }, cyclic=True)
    with pytest.raises(PipelineValidationError) as excinfo:
        validate_pipeline(pl)
    assert any("unknown entry" in e for e in excinfo.value.errors)


def test_validate_rejects_invalid_mode(tmp_path):
    pl = _make_pipeline(tmp_path, {
        "architect": {
            "requires_approval": True,
            "approval_routes": {
                "on_reject": {"target": "pm", "include": ["output"],
                              "mode": "teleport"},
            },
        },
    }, cyclic=True)
    with pytest.raises(PipelineValidationError) as excinfo:
        validate_pipeline(pl)
    assert any("mode='teleport'" in e for e in excinfo.value.errors)


# ══════════════════════════════════════════════════════════════════════════════
# Gap-closers: Message.kind, extract_markers, scan_pipeline_for_markers
# ══════════════════════════════════════════════════════════════════════════════

def test_message_kind_default_is_normal():
    from orchestrator.mailbox import Message
    m = Message(message_id="m1", thread_id="t1", seq=1,
                sender="a", send_to="b", content="hi")
    assert m.kind == "normal"


def test_message_kind_roundtrip():
    from orchestrator.mailbox import Message
    m = Message(message_id="m1", thread_id="t1", seq=1,
                sender="a", send_to="b", content="hi",
                kind="rejection_feedback")
    d = m.to_dict()
    assert d["kind"] == "rejection_feedback"
    m2 = Message.from_dict(d)
    assert m2.kind == "rejection_feedback"


def test_message_kind_defaults_when_missing_in_dict():
    # Backwards compat: older inbox files on disk have no "kind" field.
    from orchestrator.mailbox import Message
    d = {"message_id": "m1", "thread_id": "t1", "seq": 1,
         "sender": "a", "send_to": "b", "content": "hi",
         "sent_at": "2026-04-22T00:00:00Z"}
    m = Message.from_dict(d)
    assert m.kind == "normal"


def test_extract_markers():
    from orchestrator.vault import extract_markers
    assert extract_markers("") == []
    assert extract_markers("no markers") == []
    assert extract_markers("<<secret:A>> and <<secret:B>>") == ["A", "B"]
    assert extract_markers("<<secret:X>> <<secret:X>>") == ["X", "X"]


def test_scan_pipeline_for_markers_reports_agent_and_file(tmp_path):
    from orchestrator.vault import scan_pipeline_for_markers

    pl = tmp_path / "pl"
    (pl / "a").mkdir(parents=True)
    (pl / "b").mkdir()
    (pl / "pipeline.json").write_text(json.dumps({
        "name": "x",
        "agents": [{"id": "a"}, {"id": "b"}],
    }), encoding="utf-8")
    (pl / "a" / "01_system.md").write_text(
        "Use <<secret:API_KEY>> and <<secret:DB>>", encoding="utf-8",
    )
    (pl / "a" / "02_prompt.md").write_text(
        "Again <<secret:API_KEY>>", encoding="utf-8",
    )
    (pl / "b" / "01_system.md").write_text(
        "No markers here.", encoding="utf-8",
    )
    refs = scan_pipeline_for_markers(pl)
    assert set(refs) == {"API_KEY", "DB"}
    # API_KEY shows up in both files on agent 'a'
    sites = refs["API_KEY"]
    assert len(sites) == 2
    assert {(s["agent"], s["file"]) for s in sites} == {
        ("a", "01_system.md"), ("a", "02_prompt.md"),
    }


def test_scan_pipeline_raises_on_missing_pipeline(tmp_path):
    from orchestrator.vault import scan_pipeline_for_markers
    with pytest.raises(FileNotFoundError):
        scan_pipeline_for_markers(tmp_path / "nonexistent")
