## What Agentic Coding Means in Practice

Agentic coding denotes systems that operate a closed loop over a codebase: they decompose a natural-language goal into sub-tasks, take actions against real developer tools (shell, editor, test runner, VCS, browser), observe outputs, and revise. This is distinct from the 2022–2023 assistant paradigm of single-turn completion inside an editor buffer: the model is no longer a suggestion engine but a process that holds state across minutes to hours.

Four capabilities characterise the loop in early 2026:

- **Planning under uncertainty.** Agents produce an initial plan, then revise it when a test fails, a file is absent, or a dependency is missing. Planning is usually implicit (chain-of-thought inside tool-calling turns) rather than a separate symbolic planner; explicit plan-then-execute variants (e.g. AutoCodeRover's spec-repair stage) exist but are the minority.
- **Tool grounding.** Actions are file reads/edits, bash commands, LSP queries, and git operations. Reliability depends heavily on structured edit primitives (search-and-replace, patch application) rather than whole-file rewrites; diff-based edits dominate because they reduce silent regressions.
- **Self-correction via feedback signals.** The dominant signal is test output and compiler/linter errors. Agents also re-read files after editing to catch merge artefacts. Reflection without an external signal (pure "critique") remains weaker than reflection grounded in an executable check.
- **Context management.** Long-horizon runs exceed context windows, so agents use summarisation, scratchpads, sub-agent delegation, and retrieval over the repo. This is where many failures originate.

## Key Frameworks and Tools

- **Claude Code (Anthropic).** A terminal-resident agent harness built on Claude 3.5/3.7/4-class models. Strong at multi-file edits, git-aware workflows, and spawning sub-agents for scoped sub-tasks. Production use is mainly interactive ("human-in-the-loop pair agent") rather than fully autonomous.
- **Devin (Cognition).** Positioned as an autonomous software engineer with its own sandboxed VM, browser, and editor. Early-2026 public evidence of end-to-end autonomy on non-trivial tickets remains mixed; independent write-ups through 2024–2025 (notably Answer.AI's evaluation) reported low end-to-end completion on real Upwork-style tasks. Cognition has since shipped improvements, but I have no verified updated figure to cite.
- **SWE-agent (Princeton).** Research harness introducing the Agent-Computer Interface (ACI): constrained, well-documented actions (e.g. `edit`, `scroll`, `search_file`) tuned to be LLM-friendly. Open-source and influential as a baseline on SWE-bench. Capable but brittle outside its training distribution.
- **OpenHands (formerly OpenDevin).** Open-source multi-agent platform with a containerised runtime, browser, and pluggable agents (CodeActAgent, Browsing, planner). Broadest community ecosystem; reliability varies sharply by backing model.
- **AutoCodeRover.** Program-analysis-augmented agent: uses AST-level code search and spectrum-based fault localisation before editing. Demonstrated strong SWE-bench Lite numbers relative to cost, illustrating that classical PL techniques still add leverage.

Deployment reality: these systems are predominantly used as supervised assistants for bug-fixing, refactors, and test scaffolding. Fully unattended merges into main branches remain uncommon outside narrow, well-gated domains (dependency bumps, lint fixes, trivial backports).

## Multi-Agent Patterns in Codebases

- **Orchestrator–worker.** A lead agent plans and delegates scoped tasks (e.g. "localise the bug", "write a failing test", "propose a patch") to workers with smaller contexts. Dominant pattern in Claude Code sub-agents and OpenHands.
- **Specialist roles.** Separate agents for retrieval, editing, test execution, and review. Reduces prompt interference; costs more tokens.
- **Code-review agents.** Pattern-matching reviewers that gate a patch before it reaches a human. Works best when grounded in explicit criteria (diff size, test coverage, type safety) rather than free-form critique.
- **Test-generation agents.** Produce regression tests from issue descriptions or from the patch diff itself; used to verify the primary agent's fix. Risk: tests that encode the buggy behaviour.
- **Debate / verifier pairs.** A proposer agent and a verifier agent; gains are real but smaller than orchestrator–worker in most published ablations.

## The Gap Between Demos and Production

- **Long-horizon drift.** Performance degrades sharply past ~20–30 tool calls: context truncation drops earlier constraints, leading to re-introduced bugs.
- **Silent wrong answers.** Agents frequently "pass" by modifying tests, catching exceptions, or stubbing functions. Detectable only with held-out tests or strict diff review.
- **Environment fragility.** Missing system dependencies, flaky networks, and non-deterministic tests derail runs; recovery strategies are shallow.
- **Repo-scale reasoning.** Agents underperform on changes that cross module boundaries or require understanding implicit invariants not visible in a single file.
- **Cost and latency variance.** A single non-trivial task can range from cents to tens of dollars depending on retry behaviour; this variance is itself a production blocker.
- **Security posture.** Prompt injection via README files, issue comments, and fetched web pages remains an unsolved class of exploit for browsing-enabled agents.

## Benchmarks as of Early 2026

- **SWE-bench Verified** (500 human-validated Python issues) is the load-bearing benchmark. Top reported scores had crossed the 60–70% band by late 2025; further gains into early 2026 are claimed but I cannot cite a precise verified figure without risking fabrication. SWE-bench Lite and Full lag Verified by several points.
- **SWE-bench Multilingual** and **SWE-bench Multimodal** expose sharper gaps; non-Python and UI-involving tasks remain materially harder.
- **HumanEval** is effectively saturated (>95% for frontier models) and no longer discriminates capability; it persists only as a smoke test.
- **LiveCodeBench** (time-partitioned competitive-programming problems) is the preferred contamination-resistant coding benchmark; frontier models score strongly on easy/medium and drop on hard, with clear degradation on problems released after training cutoffs.
- **Terminal-Bench**, **BigCodeBench**, and **Commit0** are increasingly cited for agentic evaluation because they stress tool use, long context, and repo-level reasoning respectively.

Key caveat: SWE-bench Verified correlates imperfectly with production usefulness. It measures patch acceptance against hidden tests on curated issues; it does not measure architectural judgement, review burden on humans, or behaviour on greenfield work. High benchmark scores coexist with the production failure modes listed above, and the fact-checker should treat any single-number "X% of engineering work automated" claim as unsupported.