"""
Cogniflow Orchestrator v3.0 — Cyclic event loop engine (REQ-EXEC).

run_cyclic_pipeline() is the entry point for all cyclic pipelines.
It manages the event loop, agent state, convergence checking,
deadlock detection, cycle counting, and all termination policies.
"""
from __future__ import annotations

import json
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from .exceptions import (
    DeadlockError, CycleLimitExceeded, PipelineTimeoutError,
)
from .mailbox import Mailbox, Message
from .memory import init_agent_memory, get_relevant_artifacts
from .cyclic_agent import run_cyclic_agent
from .memory import write_artifact  # used by engine for system messages

if TYPE_CHECKING:
    from .config import OrchestratorConfig
    from .events import EventLog


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Agent state record ────────────────────────────────────────────────────────

class AgentState:
    def __init__(self, agent_id: str, agent_dir: Path, cfg: dict) -> None:
        self.id           = agent_id
        self.dir          = agent_dir
        self.cfg          = cfg
        self.status       = "pending"       # pending → running → waiting/done/failed
        self.waiting_for: list[str] = []
        self.waiting_since: Optional[str] = None
        self.invocation_n = 0
        self.thread_id    = ""

    def set_status(self, status: str,
                   waiting_for: list[str] | None = None,
                   thread_id: str = "") -> None:
        self.status = status
        self.thread_id = thread_id
        if status == "waiting":
            self.waiting_for  = waiting_for or []
            self.waiting_since = _now()
        else:
            self.waiting_for  = []
            self.waiting_since = None
        self._write_status()

    def _write_status(self) -> None:
        sp = self.dir / "06_status.json"
        data: dict[str, Any] = {}
        if sp.exists():
            try:
                data = json.loads(sp.read_text(encoding="utf-8"))
            except Exception:
                pass
        data.update({
            "agent_id":      self.id,
            "status":        self.status,
            "waiting_for":   self.waiting_for,
            "waiting_since": self.waiting_since,
            "invocation_n":  self.invocation_n,
            "current_thread_id": self.thread_id,
        })
        sp.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ── Main pipeline entry point ─────────────────────────────────────────────────

def run_cyclic_pipeline(
    pipeline_dir: Path,
    spec: dict[str, Any],
    config: "OrchestratorConfig",
    log: "EventLog",
    run_id: str,
) -> None:
    """
    Execute a cyclic pipeline.  Drives the event loop until convergence,
    timeout, or an unrecoverable error.
    """
    term        = spec.get("termination", {})
    strategy    = term.get("strategy", "all_done")
    max_cycles  = int(term.get("max_cycles", 10))
    timeout_s   = int(term.get("timeout_s", 3600))
    on_cyc_lim  = term.get("on_cycle_limit", "escalate_pm")
    dl_interval = int(term.get("deadlock_check_interval_s", 30))
    on_deadlock = term.get("on_deadlock", "escalate_pm")
    domain_tags = spec.get("tags", {}).get("domain", [])

    state_dir  = pipeline_dir / ".state"
    agents_dir = state_dir / "agents"
    shared_dir = state_dir / "shared"
    shared_dir.mkdir(parents=True, exist_ok=True)
    (shared_dir / "ARTIFACT_INDEX.json").write_text(
        json.dumps({"artifacts": []}, indent=2), encoding="utf-8"
    )

    # ── Build agent map ───────────────────────────────────────────────────────
    edges      = spec.get("edges", [])
    agent_cfgs: dict[str, dict] = {}
    for a in spec["agents"]:
        cfg_path = pipeline_dir / a.get("dir", a["id"]) / "00_config.json"
        cfg: dict[str, Any] = {}
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        agent_cfgs[a["id"]] = cfg

    agents: dict[str, AgentState] = {
        a["id"]: AgentState(
            a["id"],
            pipeline_dir / a.get("dir", a["id"]),
            agent_cfgs[a["id"]],
        )
        for a in spec["agents"]
    }

    # ── Initialise memory dirs ────────────────────────────────────────────────
    for aid in agents:
        mem_dir = agents_dir / aid
        init_agent_memory(mem_dir, aid, run_id)

    # ── Build edge maps ───────────────────────────────────────────────────────
    # contacts[agent] = list of agents it can send to
    contacts: dict[str, list[str]] = {aid: [] for aid in agents}
    # reachable_from[agent] = list of agents that can send TO it (for artifact inject)
    reachable_from: dict[str, list[str]] = {aid: [] for aid in agents}

    for edge in edges:
        f, t, etype = edge["from"], edge["to"], edge["type"]
        if f in contacts and t not in contacts[f]:
            contacts[f].append(t)
        if not edge.get("directed", True):  # bidirectional
            if t in contacts and f not in contacts[t]:
                contacts[t].append(f)
        if t in reachable_from and f not in reachable_from[t]:
            reachable_from[t].append(f)
        if not edge.get("directed", True):
            if f in reachable_from and t not in reachable_from[f]:
                reachable_from[f].append(t)

    # All agents can escalate to PM
    for aid in agents:
        if "pm" in agents and "pm" not in contacts.get(aid, []) and aid != "pm":
            contacts[aid].append("pm")

    # ── Initialise mailbox ────────────────────────────────────────────────────
    mailbox = Mailbox(state_dir / "mailbox")
    mailbox.init_agents(list(agents.keys()))

    # ── Seed initial task-edge messages ──────────────────────────────────────
    open_threads: dict[frozenset, str] = {}  # frozenset(a,b) → thread_id
    cycle_counters: dict[frozenset, int] = {}  # frozenset(a,b) → cycle count
    total_messages = 0

    for edge in edges:
        if edge["type"] == "task" and edge.get("directed", True):
            key       = frozenset([edge["from"], edge["to"]])
            thread_id = mailbox.make_thread_id(edge["from"], edge["to"])
            open_threads[key] = thread_id
            cycle_counters[key] = 0
            # Read 02_prompt.md for the seed message content
            src_dir   = pipeline_dir / agent_cfgs.get(edge["from"], {}).get("dir", edge["from"])
            src_dir   = pipeline_dir / next(
                (a.get("dir", a["id"]) for a in spec["agents"] if a["id"] == edge["from"]),
                edge["from"]
            )
            prompt_path = src_dir / "02_prompt.md"
            content = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() \
                      else f"Perform your role for the project. See CLAUDE.md for context."
            msg = mailbox.enqueue(
                send_to=edge["to"], sender=edge["from"],
                content=content, thread_id=thread_id,
                message_id=f"{edge['from']}-seed",
            )
            log.message_sent(edge["from"], edge["to"], msg.message_id,
                             thread_id, msg.seq)
            log.conversation_thread_start(thread_id,
                                          [edge["from"], edge["to"]],
                                          edge["type"])
            total_messages += 1

    # Also seed feedback/peer threads (for tracking)
    for edge in edges:
        if edge["type"] in ("feedback", "peer"):
            key = frozenset([edge["from"], edge["to"]])
            if key not in open_threads:
                thread_id = mailbox.make_thread_id(edge["from"], edge["to"])
                open_threads[key] = thread_id
                cycle_counters[key] = 0
                log.conversation_thread_start(thread_id,
                                              [edge["from"], edge["to"]],
                                              edge["type"])

    # ── Suspended agents (for deadlock escalation) ────────────────────────────
    suspended: set[str] = set()
    pm_id = "pm" if "pm" in agents else None

    # ── Watchdog state ────────────────────────────────────────────────────────
    last_deadlock_check = time.time()
    start_time          = time.time()

    # ── Event loop ────────────────────────────────────────────────────────────
    while True:
        now = time.time()

        # Wall-clock timeout
        if now - start_time >= timeout_s:
            pending = [aid for aid, a in agents.items() if a.status != "done"]
            log.pipeline_timeout(run_id, now - start_time, pending)
            raise PipelineTimeoutError(run_id, timeout_s)

        # Convergence check
        if _convergence_reached(agents, strategy, mailbox, suspended, pm_id):
            agents_done = [aid for aid, a in agents.items() if a.status == "done"]
            log.pipeline_convergence(run_id, agents_done,
                                     total_messages, max(cycle_counters.values(), default=0))
            return

        # Deadlock watchdog
        if now - last_deadlock_check >= dl_interval:
            _run_deadlock_check(agents, on_deadlock, suspended, mailbox,
                                open_threads, pm_id, log)
            last_deadlock_check = now

        # Get next message
        message = mailbox.next_pending(suspended)
        if message is None:
            time.sleep(config.loop_poll_s)
            continue

        agent   = agents[message.send_to]
        agent_state = agents[message.send_to]
        pair_key    = frozenset([message.sender, message.send_to])

        # Cycle guard check
        cycle_count = cycle_counters.get(pair_key, 0) + 1
        if cycle_count > max_cycles and not message.sender.startswith("_"):
            _handle_cycle_limit(
                on_cyc_lim, message.sender, message.send_to, cycle_count,
                max_cycles, suspended, mailbox, open_threads, pm_id, log,
            )
            cycle_counters[pair_key] = cycle_count
            log.cycle_guard_triggered(message.sender, message.send_to,
                                      cycle_count, on_cyc_lim)
            if on_cyc_lim == "halt":
                raise CycleLimitExceeded(message.sender, message.send_to, cycle_count)
            if on_cyc_lim == "escalate_pm":
                continue  # message stays in inbox, pair is suspended
        else:
            cycle_counters[pair_key] = cycle_count

        # Log message received
        log.message_received(
            message.send_to, message.message_id,
            message.thread_id, mailbox.queue_depth(message.send_to)
        )

        # Prepare invocation metadata
        agent_state.invocation_n += 1
        agent_cfgs[message.send_to]["_invocation_n"] = agent_state.invocation_n
        mem_dir = agents_dir / message.send_to

        agent_state.set_status("running", thread_id=message.thread_id)

        try:
            routing = run_cyclic_agent(
                agent_id=message.send_to,
                agent_dir=agent_state.dir,
                mem_dir=mem_dir,
                shared_dir=shared_dir,
                message=message,
                contacts=contacts.get(message.send_to, []),
                reachable_from=reachable_from.get(message.send_to, []),
                cycle_count=cycle_count,
                max_cycles=max_cycles,
                domain_tags=domain_tags,
                agent_cfg=agent_cfgs[message.send_to],
                config=config,
                log=log,
                pipeline_dir=pipeline_dir,
                run_id=run_id,
            )
        except Exception as exc:
            agent_state.set_status("failed", thread_id=message.thread_id)
            log.agent_fail(message.send_to, -1)
            if config.verbose:
                print(f"  ✗ {message.send_to}: {exc}")
            continue  # keep the loop running — other agents may still converge

        # Commit message to processed/ (REQ-MAILBOX-004 — after END marker written)
        mailbox.commit(message)
        total_messages += 1

        # ── v4 — approval-route redirect ─────────────────────────────────────
        # When the gate's approval_routes.on_reject / on_approve fired,
        # cyclic_agent attached redirect metadata to *routing*. Build the
        # feedback/task message here and enqueue it against the mailbox.
        redirect = routing.pop("_approval_redirected", None)
        if redirect:
            redirect_target  = routing.pop("_redirect_target")
            redirect_include = routing.pop("_redirect_include", ["output"])
            redirect_mode    = routing.pop("_redirect_mode", "feedback")
            redirect_note    = routing.pop("_redirect_note", "")
            redirect_output  = routing.pop("_redirect_output", "")
            gate_agent_id    = routing.pop("_gate_agent_id", message.send_to)

            status_label = redirect  # "rejected" or "approved"
            parts: list[str] = [
                f"## Feedback from {gate_agent_id}",
                "",
                f"Status:     {status_label}",
                f"Decided by: {config.approver}",
                f"Decided at: {_now()}",
            ]
            if "note" in redirect_include:
                note_text = redirect_note.strip() or "(no note provided)"
                parts.extend(["", "**Note:**", note_text])
            if "output" in redirect_include:
                parts.extend([
                    "",
                    f"**Prior output ({status_label}):**",
                    redirect_output or "(empty output)",
                ])
            if "full_context" in redirect_include:
                ctx_path = agent_state.dir / "04_context.md"
                if ctx_path.exists():
                    try:
                        full_ctx = ctx_path.read_text(encoding="utf-8")
                    except OSError:
                        full_ctx = ""
                    if full_ctx.strip():
                        parts.extend([
                            "",
                            "**Full context sent to gate:**",
                            full_ctx,
                        ])
            feedback_content = "\n".join(parts)

            tpair  = frozenset([gate_agent_id, redirect_target])
            thread = open_threads.get(tpair)
            if thread is None:
                thread = mailbox.make_thread_id(gate_agent_id, redirect_target)
                open_threads[tpair] = thread

            fb_sender_status = "waiting" if status_label == "rejected" else "working"
            fb_kind = ("rejection_feedback" if status_label == "rejected"
                       else "approval_task")
            fb_msg = mailbox.enqueue(
                send_to=redirect_target,
                sender=gate_agent_id,
                content=feedback_content,
                thread_id=thread,
                in_reply_to=message.message_id,
                status_of_sender=fb_sender_status,
                kind=fb_kind,
            )
            log.message_sent(gate_agent_id, redirect_target,
                             fb_msg.message_id, thread, fb_msg.seq,
                             kind=fb_kind)

            if status_label == "rejected":
                agent_state.set_status("awaiting_feedback", thread_id=thread)
                log.agent_awaiting_feedback(gate_agent_id, from_gate=gate_agent_id)
                if config.verbose:
                    print(f"  ⏸  {gate_agent_id} — awaiting feedback "
                          f"from {redirect_target}")
            else:
                agent_state.set_status("done", thread_id=message.thread_id)
                if config.verbose:
                    print(f"  ✓  {gate_agent_id} — approved → {redirect_target}")

            # Reactivate the target if it was waiting on this gate.
            target_state = agents.get(redirect_target)
            if target_state and target_state.status == "waiting" \
               and gate_agent_id in target_state.waiting_for:
                target_state.set_status("running", thread_id=thread)

            continue  # skip the normal routing block below

        # Update agent status
        new_status   = routing.get("status", "working")
        new_send_to  = routing.get("send_to", [])
        context_req  = routing.get("context_request")

        if new_status == "waiting":
            agent_state.set_status("waiting", waiting_for=new_send_to,
                                   thread_id=message.thread_id)
            log.agent_waiting(message.send_to, new_send_to, message.thread_id)
        elif new_status == "done":
            agent_state.set_status("done", thread_id=message.thread_id)
        else:
            agent_state.set_status("working", thread_id=message.thread_id)

        # Emit feedback_loop_tick for non-system senders
        if not message.sender.startswith("_"):
            log.feedback_loop_tick(
                [message.sender, message.send_to],
                cycle_count, message.thread_id,
                routing.get("_tokens", 0),
            )

        # Route outbound messages
        for target_id in new_send_to:
            if target_id not in agents:
                log.routing_violation(message.send_to, target_id,
                                      "target agent not in pipeline")
                continue
            if target_id not in contacts.get(message.send_to, []):
                log.routing_violation(message.send_to, target_id,
                                      "no edge from this agent to target")
                continue

            tpair  = frozenset([message.send_to, target_id])
            thread = open_threads.get(tpair, mailbox.make_thread_id(message.send_to, target_id))
            open_threads[tpair] = thread

            next_msg = mailbox.enqueue(
                send_to=target_id,
                sender=message.send_to,
                content=routing.get("_response_body", ""),
                thread_id=thread,
                in_reply_to=message.message_id,
                status_of_sender=new_status,
            )
            # Attach context_request for next invocation
            if context_req:
                next_msg._context_request = context_req

            log.message_sent(message.send_to, target_id, next_msg.message_id,
                             thread, next_msg.seq)

            # Reactivate waiting agent if it was waiting for this sender
            target_state = agents.get(target_id)
            if target_state and target_state.status == "waiting" \
               and message.send_to in target_state.waiting_for:
                target_state.set_status("running", thread_id=thread)

        if config.verbose:
            status_icon = "⏸" if new_status == "waiting" else ("✓" if new_status == "done" else "→")
            print(f"  {status_icon} {message.send_to} [{new_status}] cycle={cycle_count}")

        time.sleep(0)  # yield to other threads


# ── Convergence ───────────────────────────────────────────────────────────────

def _convergence_reached(
    agents: dict[str, AgentState],
    strategy: str,
    mailbox: Mailbox,
    suspended: set[str],
    pm_id: Optional[str],
) -> bool:
    if strategy == "all_done":
        all_done = all(a.status == "done" for a in agents.values())
        return all_done and mailbox.all_inboxes_empty(suspended)
    elif strategy == "coordinator_signal":
        return pm_id is not None and agents.get(pm_id, AgentState("", Path(), {})).status == "done"
    elif strategy == "timeout_only":
        return False
    return False


# ── Deadlock detection ────────────────────────────────────────────────────────

def _run_deadlock_check(
    agents: dict[str, AgentState],
    on_deadlock: str,
    suspended: set[str],
    mailbox: Mailbox,
    open_threads: dict,
    pm_id: Optional[str],
    log: "EventLog",
) -> None:
    waiting_agents = {aid: a.waiting_for for aid, a in agents.items()
                      if a.status == "waiting" and aid not in suspended}
    if len(waiting_agents) < 2:
        return

    # Detect circular dependencies
    deadlocked = _find_deadlock_cycle(waiting_agents)
    if not deadlocked:
        return

    waiting_graph = {aid: waiting_agents[aid] for aid in deadlocked}
    log.deadlock_detected(deadlocked, waiting_graph)

    if on_deadlock == "halt":
        raise DeadlockError(deadlocked)

    elif on_deadlock == "escalate_pm" and pm_id:
        summary = json.dumps(waiting_graph)
        for aid in deadlocked:
            suspended.add(aid)
        # Notify PM
        key = frozenset([pm_id, deadlocked[0]])
        thread = open_threads.get(key, f"pm-deadlock-{_now()}")
        mailbox.enqueue_system(
            pm_id,
            f"Deadlock detected among agents: {', '.join(deadlocked)}. "
            f"Waiting graph: {summary}. Please resolve.",
            thread,
        )

    elif on_deadlock == "force_unblock_oldest":
        # Find agent with earliest waiting_since
        oldest_agent = min(
            deadlocked,
            key=lambda aid: agents[aid].waiting_since or "9999",
        )
        agents[oldest_agent].set_status("running")
        # Find a thread to inject into
        for aid in deadlocked:
            if aid != oldest_agent:
                key = frozenset([oldest_agent, aid])
                thread = open_threads.get(key, f"deadlock-unblock")
                mailbox.enqueue_system(
                    oldest_agent,
                    "Deadlock detected. Proceed with your best current information "
                    "without waiting for a response.",
                    thread,
                )
                break


def _find_deadlock_cycle(waiting: dict[str, list[str]]) -> list[str]:
    """Return agents in a circular wait, or empty list if none."""
    for start in waiting:
        visited: list[str] = []
        current = start
        while current in waiting:
            if current in visited:
                cycle_start = visited.index(current)
                return visited[cycle_start:]
            visited.append(current)
            # Take first waiting_for that is also waiting
            next_agents = [a for a in waiting.get(current, []) if a in waiting]
            if not next_agents:
                break
            current = next_agents[0]
    return []


# ── Cycle limit ───────────────────────────────────────────────────────────────

def _handle_cycle_limit(
    on_cycle_limit: str,
    agent_a: str,
    agent_b: str,
    cycle_count: int,
    max_cycles: int,
    suspended: set[str],
    mailbox: Mailbox,
    open_threads: dict,
    pm_id: Optional[str],
    log: "EventLog",
) -> None:
    if on_cycle_limit == "escalate_pm" and pm_id:
        suspended.add(agent_a)
        suspended.add(agent_b)
        key    = frozenset([pm_id, agent_a])
        thread = open_threads.get(key, f"pm-cycle-limit")
        mailbox.enqueue_system(
            pm_id,
            f"Cycle limit of {max_cycles} reached between '{agent_a}' and "
            f"'{agent_b}' (current cycle: {cycle_count}). "
            "Please make a convergence decision.",
            thread,
        )
    elif on_cycle_limit == "force_done":
        pass  # engine marks agent done in status update
    # "halt" is handled by the caller (raises CycleLimitExceeded)
