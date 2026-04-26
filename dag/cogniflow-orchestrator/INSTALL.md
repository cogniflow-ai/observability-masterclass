# Installation Guide

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10 or later | `python --version` to check |
| Claude CLI | any | Must be authenticated with your subscription |
| pip | any | Bundled with Python |

---

## Step 1 — Install the Claude CLI

The orchestrator calls `claude` (or `claude.exe` on Windows) as a subprocess. You need the Claude CLI installed and authenticated with your **monthly Pro or Team subscription** before running any pipeline.

### macOS / Linux

```bash
npm install -g @anthropic-ai/claude-code
claude --version        # confirm install
claude                  # log in — follow the browser prompt
```

If you do not have Node.js, install it from https://nodejs.org first.

### Windows

```powershell
npm install -g @anthropic-ai/claude-code
claude --version
claude
```

The installer places `claude.exe` in your npm global bin directory, typically:
```
C:\Users\<you>\AppData\Roaming\npm\claude.cmd
```

The orchestrator searches common install paths automatically. If it fails to find the binary, set the `CLAUDE_BIN` environment variable (see Step 4).

### Verify authentication

```bash
claude -p "say hello"
```

If this returns a response, your subscription is active and the CLI is ready.

---

## Step 2 — Install Python dependencies

From the directory containing this file:

```bash
pip install -r requirements.txt
```

This installs:
- `networkx` — DAG topological sort and cycle detection
- `filelock`  — thread-safe event log writes

To also install the test suite dependencies:

```bash
pip install pytest pytest-cov
```

---

## Step 3 — Verify the installation

```bash
python cli.py validate pipelines/ai_coding_2026
```

Expected output:
```
✓ pipeline.json is valid
  Name   : 7-agent
  Agents : 7
```

If this passes, you are ready to run.

---

## Step 4 — Configure (optional)

All configuration is via environment variables. Copy the example file:

```bash
cp .env.example .env
```

Then edit `.env`. The most important setting is `CLAUDE_BIN` if auto-detection fails:

```bash
# Windows example:
CLAUDE_BIN=C:\Users\yourname\AppData\Roaming\npm\claude.cmd

# macOS example:
CLAUDE_BIN=/usr/local/bin/claude
```

Other useful settings:

```bash
AGENT_TIMEOUT=300        # seconds per agent before killing (default: 300)
MODEL_CONTEXT_LIMIT=180000  # token window of your Claude model
VERBOSE=1                # set to 0 to reduce output
```

To load `.env` before running:

```bash
# macOS / Linux
export $(cat .env | grep -v '#' | xargs)

# Windows PowerShell
Get-Content .env | Where-Object { $_ -notmatch '^#' } | ForEach-Object {
    $name, $value = $_.Split('=', 2); [Environment]::SetEnvironmentVariable($name, $value)
}
```

---

## Step 5 — Run the sample pipeline

```bash
python cli.py run pipelines/7-agent
```

The pipeline will:
1. Validate the pipeline definition
2. Run three research agents in parallel (Layer 0)
3. Run the synthesiser once all three complete (Layer 1)
4. Run the writer and fact-checker in parallel (Layer 2)
5. Run the editor once both complete (Layer 3)

Outputs are written to `pipelines/7-agent/.state/agents/<agent_id>/05_output.md`.

To read the final article:

```bash
python cli.py inspect pipelines/ai_coding_2026 --agent 007_editor --file output
```

---

## Troubleshooting

### "claude: command not found"
The `claude` binary is not in PATH. Set `CLAUDE_BIN` to the full path (see Step 4).

### "Error: not authenticated" or similar auth error
Run `claude` interactively to complete login, then retry.

### Agent fails immediately (exit code 1, empty output)
- Check the prompt files exist: `python cli.py inspect pipelines/ai_coding_2026 --agent 001_researcher_tools --file prompt`
- Check Claude works standalone: `claude -p "hello"`
- Check your subscription is active at claude.ai

### Pipeline stalls / one agent hangs
The default timeout is 300 seconds. Increase it for complex research tasks:
```bash
python cli.py run pipelines/ai_coding_2026 --timeout 600
```

### Resume after interruption
Re-run the same command. Completed agents are skipped automatically:
```bash
python cli.py run pipelines/ai_coding_2026
```

### Reset and re-run from scratch
```bash
python cli.py reset pipelines/ai_coding_2026
python cli.py run   pipelines/ai_coding_2026
```

### Reset a single agent
```bash
python cli.py reset pipelines/ai_coding_2026 --agent 004_synthesizer
python cli.py run   pipelines/ai_coding_2026
```
