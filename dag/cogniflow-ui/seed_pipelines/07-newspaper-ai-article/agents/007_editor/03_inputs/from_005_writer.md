# The Reviewer Is the New Bottleneck — And Nobody Has Fixed It Yet

Three-quarters of developers now use AI coding tools daily. A controlled study of senior open-source maintainers found those tools made them roughly 19% slower on complex tasks — while the same participants reported feeling faster. That gap, between what engineers believe AI is doing for them and what instrumentation actually shows, is the defining management problem of 2026. Capability has outrun the operating model, and the binding constraints are no longer model quality.

## The Productivity Numbers Don't Say What the Slides Say

Vendor decks quoting 25–55% speed-ups do not survive contact with controlled measurement. The credible numbers from instrumented enterprise rollouts — ANZ, Goldman, Commerzbank — cluster around 10–15%. The GitHub-Microsoft-Accenture randomised trial measured roughly 26% more pull requests per developer on Copilot, which is real but narrower than it sounds: PR count is a throughput metric, not a delivery metric, and DORA's 2024 data paired modest throughput gains with measurable declines in delivery stability.

The METR finding deserves to live at the top of every business case as a calibration check. Self-reported speed-up, measured slowdown. Any internal developer survey that doesn't reconcile against hard cycle-time data should be treated as sentiment, not evidence. The honest position is that AI assistance is credibly positive on instrumented toil — boilerplate, scaffolding, dependency bumps, well-scoped migrations — and unproven to negative on the work senior engineers are actually paid for.

## Writing Got Faster. Reviewing Did Not.

The bottleneck has migrated from keyboard to reviewer. PRs are quicker to author and slower to digest, and the second-order response — deploying AI review bots to relieve AI-induced reviewer fatigue — is either going to close the loop or compound the silent-wrong-answer problem. Test-generation agents already routinely encode the bug they were meant to catch. Bug detection is the weakest capability across the field: shallow patterns and known CVE shapes are within reach, logic and concurrency bugs are not, and false positives drive the same fatigue the bots were brought in to solve.

There is an inverted adoption pattern worth dwelling on. Senior engineers switch the tools off for complex work at higher rates than juniors. The people most equipped to catch silent failures trust the output least; the people least equipped to catch them trust it most. That has obvious consequences for pairing rituals, hiring, and the political defensibility of any mandated rollout.

## Per-Seat Pricing Was a Lie of Convenience

Engineering budgets are built around seats. Agentic workloads are metered in tokens and vary by an order of magnitude per task. Enterprise reports through 2025 describe 2–10× budget overruns on autonomous long-horizon jobs, with several large adopters forced into mid-year resets. Finance functions still treating inference as a SaaS line will keep mis-forecasting, and the orchestrator-worker patterns now dominant in production — a lead agent delegating to workers with smaller contexts — make the variance worse, not better.

The organisations pulling ahead have split their engineering AI budget into two lines: stable per-seat assistants owned by the platform team, and volatile token consumption owned by whichever team triggered the run. Hard per-run ceilings, showback from day one, anomaly detection on inference spend. Most enterprises are not yet there.

## Autonomy Is a Security Surface, Not a Feature

Marketing for Devin and its peers implies unattended engineering. Independent evaluation says otherwise: production deployment is overwhelmingly interactive, and unattended merges into main exist only in tightly gated domains like dependency bumps. That is the right answer for now, because every capability upgrade — shell access, repo write, browser, cloud credentials — widens the attack surface. Prompt injection via README files, issue bodies, fetched pages and transitive dependencies is OWASP's top LLM risk for a reason, and slopsquatting has moved from academic demo to observed exploitation.

There is no settled architectural answer to any of this. The pragmatic response is to strip untrusted content from agent context by default, enforce package allow-lists, require a second-agent or human verification step before any write to a shared environment, and red-team your own deployments before someone else does it for you.

## What to Do This Quarter

Pick one IDE assistant, one CLI agent, one review bot and one chat surface per developer persona, and publish an explicit list of where AI is not to be used — complex refactors by senior engineers, early-career learning rotations, regulated workloads outside the approved private-tenant path. Instrument with DORA and SPACE rather than vendor dashboards. Make a human approver of record non-negotiable on any AI-authored production commit, captured in an audit trail aligned to ISO 42001. Assume 2–10× your initial token forecast until you have six months of data. The teams that win the next twelve months will not be the ones with the most tools. They will be the ones who priced the review burden honestly and built the governance to absorb it.