## Leading Tools: Positioning, Differentiators, Pricing

- **GitHub Copilot** (Microsoft/GitHub): the default incumbent, bundled deeply into VS Code, Visual Studio, JetBrains, Neovim, Xcode and github.com. Shifted from a single-model product to a model picker (Anthropic Claude, OpenAI GPT, Google Gemini) during 2024–2025. Tiers: Free (limited completions/chat), Pro (~$10/user/month), Pro+ and Business/Enterprise tiers at higher price points; exact early-2026 pricing may have moved — verify on GitHub's pricing page.
- **Cursor** (Anysphere): VS Code fork positioned as an "AI-native IDE". Differentiators: Composer/Agent mode for multi-file edits, tight codebase indexing, fast apply model. Pricing tiers Hobby (free), Pro (~$20/month) and Business (~$40/user/month); Anysphere raised large funding rounds in 2024–2025 pushing its valuation into the multi-billion range (exact figure uncertain).
- **Windsurf** (formerly Codeium): rebranded around its Windsurf Editor and "Cascade" agent. Free tier historically more generous than Cursor; paid plans roughly $15/user/month individual and higher for teams. Acquisition/investment activity around Windsurf in 2024–2025 was widely reported — confirm current ownership before citing.
- **Tabnine**: privacy- and self-hosting-focused; supports on-prem, air-gapped and VPC deployments with a choice of models including smaller Tabnine-trained ones. Pricing centred on per-seat Dev/Enterprise plans (roughly $9–$39/user/month, tier names have shifted).
- **Amazon Q Developer** (successor to CodeWhisperer): integrated across AWS Console, IDEs, CLI and now positioned around agents for code transformation (e.g. Java version upgrades) and AWS-aware assistance. Free tier plus Pro (~$19/user/month).
- **JetBrains AI Assistant / Junie**: native to JetBrains IDEs; "Junie" is JetBrains' coding agent launched 2024–2025 running alongside the inline assistant. Bundled in the AI Pro/AI Ultimate add-ons (~$10–$20/user/month range, verify).
- **Replit (Ghostwriter → Agent)**: Ghostwriter has effectively been absorbed into "Replit Agent", a browser-based build-an-app assistant. Tied to Replit's Core/Teams subscriptions.

## Capability Benchmarks

Public benchmarks (SWE-bench Verified, Aider leaderboards, LiveCodeBench) track the underlying frontier models more than the IDE wrappers. Tool-level quality is largely a function of context retrieval, edit application and UI, not the base LLM. Treat specific percentage scores as volatile.

- **Inline completion**: Copilot and Windsurf lead on latency and single-line accuracy; Tabnine competitive on local/small-model completion; Cursor competitive but tuned more for multi-line/chunk edits.
- **Multi-file refactoring**: Cursor (Composer/Agent) and Windsurf (Cascade) generally outperform Copilot's equivalent edit mode on cross-file consistency in informal comparisons; Copilot's "edits" and agent modes closed much of the gap through 2025.
- **Test generation**: Copilot, Cursor and Q Developer all generate plausible unit tests; weakness across the board remains meaningful coverage of edge cases and integration tests — models tend to mirror the happy path of the function under test.
- **Documentation**: broadly solved for docstrings and READMEs; quality degrades on architectural documentation requiring cross-repo synthesis.
- **Bug detection**: weakest category. Copilot Autofix (via CodeQL), Amazon Q and Cursor's review features can catch shallow bugs and known vulnerability patterns; none reliably find logic or concurrency bugs. False-positive rates on AI-only scanners remain a common complaint.

## Integration Patterns

- **IDE plugins**: every major tool ships VS Code and JetBrains plugins; Copilot additionally covers Visual Studio, Xcode (GA in 2024), Neovim and Eclipse. Cursor and Windsurf are themselves VS Code forks rather than plugins.
- **CLI**: a distinct category emerged in 2024–2025 — Claude Code (Anthropic), Gemini CLI (Google), OpenAI Codex CLI, Aider (open source), Amazon Q CLI, and GitHub Copilot CLI. These run in the terminal, read/write files directly and invoke shell commands.
- **API access**: Tabnine, Codeium/Windsurf and Copilot expose limited APIs; most "API access" for coding is really direct use of the underlying model APIs (Anthropic, OpenAI, Google) plus frameworks.
- **CI/CD**: GitHub Copilot code review and Copilot Workspace hook into PRs; CodeRabbit, Greptile, Qodo (formerly Codium AI) and Graphite Reviewer occupy the AI PR-review niche. Amazon Q integrates with CodeCatalyst pipelines.
- **Repo indexing**: Cursor, Windsurf, Sourcegraph Cody and Copilot all maintain embeddings/indexes of the workspace; quality of retrieval is a primary differentiator.

## Autocomplete → Chat → Agentic Shift

The 2022–2023 product shape was ghost-text autocomplete. 2024 brought chat panels and inline "edit with AI" selections. Through 2025 the centre of gravity moved to agent modes: Cursor Composer/Agent, Copilot Agent Mode and Copilot Workspace, Windsurf Cascade, JetBrains Junie, Replit Agent, Amazon Q "Dev Agent", plus CLI agents (Claude Code, Codex CLI). Drivers: longer model context windows, tool-use/function-calling maturity, and stronger coding models (Claude Sonnet/Opus 4.x series, GPT-4.1/5-class, Gemini 2.x). As of early 2026 autocomplete is commoditised; product competition is on agent reliability, review loops and codebase grounding. (Specific adoption/usage ratios between modes are not reliably public — do not cite numbers.)

## New Entrants and Consolidation (Past ~12 Months)

- **Anthropic Claude Code** (released 2025) became a significant terminal-native entrant and pushed rivals to ship CLI equivalents.
- **OpenAI Codex** was revived as a cloud/CLI coding agent distinct from ChatGPT; OpenAI's acquisition activity around Windsurf was widely reported in 2025 — ownership status as of early 2026 should be reverified.
- **Google** consolidated Duet AI and Gemini Code Assist under the Gemini brand and shipped Gemini CLI and Jules (async coding agent).
- **Cognition (Devin)** continued as an autonomous-agent product; Cognition's acquisition of Windsurf assets was reported in 2025 — confirm current state.
- **Sourcegraph Cody**, **Cline** (open source VS Code agent), **Roo Code**, **Aider**, **Continue.dev** and **Zed's AI features** fill out the long tail.
- Consolidation pressure is clear: Codeium → Windsurf → acquisition, CodeWhisperer folded into Q, Ghostwriter folded into Replit Agent, Duet folded into Gemini. Independent mid-tier tools are increasingly rare.