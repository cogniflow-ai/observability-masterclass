/* ── Cogniflow Observer — Client JavaScript ──────────────────────────────── */

'use strict';

// ── Dark / light mode ─────────────────────────────────────────────────────

const THEME_KEY = 'cogniflow-theme';

function initTheme() {
  const saved = localStorage.getItem(THEME_KEY);
  const pref  = saved || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  setTheme(pref, false);
}

function setTheme(theme, save = true) {
  document.documentElement.setAttribute('data-theme', theme);
  const btn = document.getElementById('theme-btn');
  if (btn) btn.textContent = theme === 'dark' ? '☀' : '☾';
  if (save) localStorage.setItem(THEME_KEY, theme);
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme') || 'light';
  setTheme(current === 'dark' ? 'light' : 'dark');
}

// ── Alert sound ───────────────────────────────────────────────────────────

let _soundPlayed = false;

function playApprovalSound() {
  if (_soundPlayed) return;
  _soundPlayed = true;
  try {
    const ctx  = new (window.AudioContext || window.webkitAudioContext)();
    const osc  = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(440, ctx.currentTime + 0.3);
    gain.gain.setValueAtTime(0.3, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.4);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.4);
  } catch (e) { /* AudioContext unavailable */ }
}

function resetSound() { _soundPlayed = false; }

// ── Approval modal ────────────────────────────────────────────────────────

let _modalExpanded = false;

function showApprovalModal(agentId, preview) {
  const modal = document.getElementById('approval-modal');
  if (!modal) return;
  const titleEl   = modal.querySelector('.approval-modal-title');
  const previewEl = modal.querySelector('.approval-modal-preview');
  if (titleEl)   titleEl.textContent = agentId + ' is waiting for your approval';
  if (previewEl) {
    previewEl.textContent = preview || '';
    previewEl.style.display = _modalExpanded && preview ? 'block' : 'none';
  }
  modal.classList.add('visible');
  playApprovalSound();
}

function hideApprovalModal() {
  const modal = document.getElementById('approval-modal');
  if (modal) modal.classList.remove('visible');
}

function toggleModalPreview() {
  _modalExpanded = !_modalExpanded;
  const previewEl = document.querySelector('.approval-modal-preview');
  if (previewEl) previewEl.style.display = _modalExpanded ? 'block' : 'none';
  const toggle = document.querySelector('.approval-modal-toggle');
  if (toggle) toggle.textContent = _modalExpanded ? 'Show less ▲' : 'Show more ▼';
}

// ── Rejection note toggle ─────────────────────────────────────────────────

function toggleRejection(agentId) {
  const wrap = document.getElementById('reject-note-' + agentId);
  if (!wrap) return;
  const show = wrap.classList.toggle('show');
  const btn  = document.getElementById('reject-btn-' + agentId);
  if (btn) btn.textContent = show ? 'Cancel' : 'Reject';
  if (show) {
    const inp = wrap.querySelector('input[name="note"]');
    if (inp) inp.focus();
  }
}

function validateRejectNote(form) {
  const inp = form.querySelector('input[name="note"]');
  if (!inp || !inp.value.trim()) {
    alert('A rejection note is required so the downstream agent has feedback to work from.');
    if (inp) inp.focus();
    return false;
  }
  return true;
}

// ── Validation drill-down side panel ──────────────────────────────────────

function loadViolations(name, agentId, phase) {
  htmx.ajax(
    'GET',
    `/pipelines/${name}/agents/${agentId}/violations?phase=${encodeURIComponent(phase)}`,
    { target: '#detail-container', swap: 'innerHTML' },
  );
}

// ── Run audit (secrets) ───────────────────────────────────────────────────

function loadRunAudit(name, direction) {
  let url = `/pipelines/${name}/audit`;
  if (direction) url += `?direction=${encodeURIComponent(direction)}`;
  htmx.ajax('GET', url, { target: '#detail-container', swap: 'innerHTML' });
}

// ── Tab switching in detail panel ─────────────────────────────────────────

function switchTab(tabId) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  const btn = document.querySelector(`[data-tab="${tabId}"]`);
  const content = document.getElementById('tab-' + tabId);
  if (btn)     btn.classList.add('active');
  if (content) content.classList.add('active');
}

// ── Copy to clipboard ─────────────────────────────────────────────────────

function copyOutput(agentId) {
  const raw = document.getElementById('output-raw-' + agentId);
  if (!raw) return;
  navigator.clipboard.writeText(raw.value).then(() => {
    const btn = document.querySelector('.copy-btn');
    if (btn) {
      const orig = btn.textContent;
      btn.textContent = 'Copied!';
      setTimeout(() => { btn.textContent = orig; }, 1500);
    }
  });
}

// ── Event stream auto-scroll ──────────────────────────────────────────────

function scrollEventsToBottom() {
  const el = document.getElementById('events-scroll');
  if (el) el.scrollTop = el.scrollHeight;
}

// ── Pipeline controls (start / stop) ─────────────────────────────────────

// Button visual states:
// idle (green) → starting (gray) → running/started (gray)
//            pausing (gray, disabled) → paused (green "Resume") → running
//            → complete (gray, disabled; only Reset brings it back)
function setStartButtonState(state) {
  const btn = document.getElementById('start-btn');
  if (!btn) return;
  // "starting" is client-only — once the server reports running/complete, we leave it.
  if (btn.dataset.state === 'starting' && state === 'idle') return;
  btn.dataset.state = state;
  btn.classList.remove('btn-green', 'btn-gray');
  switch (state) {
    case 'starting':
      btn.classList.add('btn-gray');
      btn.disabled = true;
      btn.textContent = 'Starting…';
      break;
    case 'running':
    case 'pausing':
      btn.classList.add('btn-gray');
      btn.disabled = true;
      btn.textContent = 'Started';
      break;
    case 'paused':
      btn.classList.add('btn-green');
      btn.disabled = false;
      btn.textContent = '▶ Resume';
      btn.title = 'Resume the paused pipeline at the next layer';
      break;
    case 'resuming':
      btn.classList.add('btn-gray');
      btn.disabled = true;
      btn.textContent = 'Resuming…';
      break;
    case 'complete':
      btn.classList.add('btn-gray');
      btn.disabled = true;
      btn.textContent = '✓ Completed';
      btn.title = 'Pipeline finished — press Reset to run it again';
      break;
    case 'idle':
    default:
      btn.classList.add('btn-green');
      btn.disabled = false;
      btn.textContent = '▶ Start Pipeline';
      btn.title = 'Writes .command.json to start the pipeline via the launcher';
      break;
  }
}

function startPipeline(name) {
  // The Start button doubles as Resume when the pipeline is paused.
  const btn = document.getElementById('start-btn');
  if (btn && btn.dataset.state === 'paused') {
    resumePipeline(name);
    return;
  }
  // OBSERVER_CHANGES § 1.2 — block start when pipeline_validation_error is the
  // last attempted run's outcome.
  if (window._validationBlocked) {
    alert('Pipeline cannot start — fix the structural validation errors first '
        + '(re-save in the Configurator).');
    return;
  }
  setStartButtonState('starting');
  fetch(`/pipelines/${name}/start`, { method: 'POST' })
    .then(() => { resetSound(); })
    .catch(() => { setStartButtonState('idle'); });
}

function pausePipeline(name) {
  setPauseButtonState('pausing');
  setStartButtonState('pausing');
  setDagStateBanner('pausing');
  fetch(`/pipelines/${name}/pause`, { method: 'POST' })
    .catch(() => {
      // On network failure, revert to running so the user can retry.
      setPauseButtonState('running');
      setStartButtonState('running');
      setDagStateBanner(null);
    });
}

function resumePipeline(name) {
  setStartButtonState('resuming');
  setPauseButtonState('resuming');
  setPausedBanner('');
  setDagStateBanner(null);
  fetch(`/pipelines/${name}/resume`, { method: 'POST' })
    .catch(() => {
      // Roll back UI on failure.
      setStartButtonState('paused');
      setPauseButtonState('paused');
      setPausedBanner('paused');
      setDagStateBanner('paused');
    });
}

// Stop is always clickable — writing a stop command is a no-op if nothing is running.
// Only transient "stopping" briefly disables it.
function setStopButtonState(state) {
  const btn = document.getElementById('stop-btn');
  if (!btn) return;
  btn.dataset.state = state;
  btn.classList.remove('btn-red', 'btn-gray');
  const disabledStates = new Set([
    'stopping', 'stopped', 'complete', 'idle', 'paused',
  ]);
  if (disabledStates.has(state)) {
    btn.classList.add('btn-gray');
    btn.disabled = true;
    btn.textContent =
      state === 'stopping' ? 'Stopping…' :
      state === 'stopped'  ? '■ Stopped' :
                             '■ Stop';
  } else {
    // running, pausing, resuming — agents may still be in flight.
    btn.classList.add('btn-red');
    btn.disabled = false;
    btn.textContent = '■ Stop';
  }
}

// ── Sticky "stop phase" (client-only) ────────────────────────────────────
// After the user presses Stop we hold the UI in a "stopping" state until the
// server reports that nothing is running; then we flip to "stopped" until the
// user presses Reset. Server polls cannot override this sticky state — the
// orchestrator has no dedicated "stopped" signal, so the observer owns it.
let _stopPhase = null;  // null | 'stopping' | 'stopped'

function setDagStateBanner(phase) {
  const b = document.getElementById('dag-state-banner');
  if (!b) return;
  b.classList.remove('visible', 'stopping', 'stopped', 'pausing', 'paused');
  const label = {
    stopping: '⏹ Stopping pipeline…',
    stopped:  '⏹ Pipeline stopped',
    pausing:  '⏸ Pausing pipeline…',
    paused:   '⏸ Pipeline paused',
  }[phase];
  if (!label) return;
  b.textContent = label;
  b.classList.add('visible', phase);
}

// Backwards-compat alias for the earlier name.
function setDagStopBanner(phase) { setDagStateBanner(phase); }

function applyStopPhase() {
  if (!_stopPhase) return;
  setDagStopBanner(_stopPhase);
  const startBtn = document.getElementById('start-btn');
  const pauseBtn = document.getElementById('pause-btn');
  const stopBtn  = document.getElementById('stop-btn');
  const resetBtn = document.getElementById('reset-btn');

  if (startBtn) {
    startBtn.dataset.state = _stopPhase;
    startBtn.classList.remove('btn-green');
    startBtn.classList.add('btn-gray');
    startBtn.disabled = true;
    startBtn.textContent = '▶ Start Pipeline';
    startBtn.title = _stopPhase === 'stopping'
      ? 'Pipeline is stopping — please wait'
      : 'Pipeline stopped — press Reset to run it again';
  }
  if (pauseBtn) {
    pauseBtn.dataset.state = _stopPhase;
    pauseBtn.classList.add('btn-gray');
    pauseBtn.disabled = true;
    pauseBtn.textContent = '⏸ Pause';
    pauseBtn.title = 'Pause is unavailable while the pipeline is stopped';
  }
  setStopButtonState(_stopPhase);
  if (resetBtn) {
    // During "stopping" the orchestrator is still tearing things down —
    // Reset is unsafe. Once "stopped", Reset is the only way forward.
    resetBtn.disabled = (_stopPhase === 'stopping');
  }
  setPausedBanner('');
}

function clearStopPhase() {
  _stopPhase = null;
  const resetBtn = document.getElementById('reset-btn');
  if (resetBtn) resetBtn.disabled = false;
  setDagStopBanner(null);
}

// Pause button — visible only as an affordance while the pipeline is running.
// "pausing" and "paused" keep it disabled (pause already issued / already paused).
function setPauseButtonState(state) {
  const btn = document.getElementById('pause-btn');
  if (!btn) return;
  btn.dataset.state = state;
  btn.classList.remove('btn-gray');
  if (state === 'running') {
    btn.disabled = false;
    btn.textContent = '⏸ Pause';
    btn.title = 'Ask the pipeline to pause at the end of the current layer';
    return;
  }
  // Every other state → disabled + gray.
  btn.disabled = true;
  btn.classList.add('btn-gray');
  if (state === 'pausing') {
    btn.textContent = 'Pausing…';
    btn.title = 'The pipeline will pause when the current layer finishes. '
              + 'It may still fail earlier if any agent errors out.';
  } else if (state === 'paused') {
    btn.textContent = '⏸ Paused';
    btn.title = 'Pipeline is paused — press Resume (▶) to continue';
  } else {
    btn.textContent = '⏸ Pause';
    btn.title = 'Pause is only available while the pipeline is running';
  }
}

function setPausedBanner(state) {
  const banner = document.getElementById('paused-banner');
  if (!banner) return;
  if (state === 'paused') {
    banner.classList.add('visible');
  } else {
    banner.classList.remove('visible');
  }
}

function stopPipeline(name) {
  _stopPhase = 'stopping';
  applyStopPhase();
  fetch(`/pipelines/${name}/stop`, { method: 'POST' })
    .finally(() => {
      // Grace window while the orchestrator tears down, then promote to
      // "stopped" and keep the UI sticky until the user presses Reset.
      setTimeout(() => {
        if (_stopPhase === 'stopping') {
          _stopPhase = 'stopped';
          applyStopPhase();
        }
      }, 1500);
    });
}

function resetPipeline(name) {
  const msg = 'Reset this pipeline?\n\nDeletes agent status, outputs, context, approvals and events.\nPrompts and inputs are kept.';
  if (!confirm(msg)) return;
  clearStopPhase();
  const btn = document.getElementById('reset-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Resetting…'; }
  fetch(`/pipelines/${name}/reset`, { method: 'POST' })
    .then(async r => {
      let data = {};
      try { data = await r.json(); } catch (_) {}
      return { ok: r.ok && data.ok, errors: data.errors || [], status: r.status };
    })
    .then(d => {
      if (!d.ok) {
        const detail = d.errors && d.errors.length
          ? '\n\nCould not remove:\n- ' + d.errors.join('\n- ')
          : '';
        const hint = (d.errors || []).some(e => /PermissionError|WinError|being used/i.test(e))
          ? '\n\nThese files are held open by another process. Stop the launcher (python launcher.py) and try Reset again.'
          : '\n\nEnsure the pipeline and launcher are stopped, then retry.';
        alert(`Reset failed (HTTP ${d.status}).${detail}${hint}`);
        if (btn) { btn.disabled = false; btn.textContent = '↻ Reset'; }
        return;
      }
      window.location.reload();
    })
    .catch(err => {
      if (btn) { btn.disabled = false; btn.textContent = '↻ Reset'; }
      alert('Reset failed (network): ' + (err && err.message ? err.message : err));
    });
}

// ── HTMX events ───────────────────────────────────────────────────────────

// Track DAG agent statuses across polls so we can flash names on change
const _prevAgentStatus = {};

function syncAgentStatusSnapshot() {
  document.querySelectorAll('#dag-container [data-agent-id]').forEach(g => {
    _prevAgentStatus[g.dataset.agentId] = g.dataset.status;
  });
}

function flashChangedAgentNames() {
  document.querySelectorAll('#dag-container [data-agent-id]').forEach(g => {
    const id = g.dataset.agentId;
    const status = g.dataset.status;
    const prev = _prevAgentStatus[id];
    if (prev !== undefined && prev !== status) {
      const nameEl = g.querySelector('.dag-agent-name');
      if (nameEl) {
        nameEl.classList.remove('flash');
        void nameEl.getBoundingClientRect();  // force reflow to restart animation
        nameEl.classList.add('flash');
      }
    }
    _prevAgentStatus[id] = status;
  });
}

document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  scrollEventsToBottom();
  const init = window._initialState || 'idle';
  setStartButtonState(init);
  setStopButtonState(init);
  setPauseButtonState(init);
  setPausedBanner(init);
  syncAgentStatusSnapshot();  // baseline — first poll should not flash everything
});

// Pipeline state from HX-Trigger on each cards poll
document.body.addEventListener('pipeline-state', (e) => {
  // Sticky stop phase beats whatever the server reports.
  if (_stopPhase) { applyStopPhase(); return; }
  const state = (e.detail && e.detail.state) || 'idle';
  setStartButtonState(state);
  setPauseButtonState(state);
  setPausedBanner(state);
  setStopButtonState(state);
  // DAG corner badge mirrors pause state while not in a stop phase.
  setDagStateBanner(state === 'pausing' || state === 'paused' ? state : null);
});

// After each cards update
document.body.addEventListener('htmx:afterSwap', (e) => {
  const tid = e.detail.target && e.detail.target.id;
  if (tid === 'events-container') {
    scrollEventsToBottom();
  } else if (tid === 'cards-container') {
    // The DAG is OOB-swapped alongside the cards — compare statuses and flash any that changed
    flashChangedAgentNames();
  }
});

// Approval needed event (from HX-Trigger header)
document.body.addEventListener('approval-needed', () => {
  // Find the first awaiting agent and show modal
  const card = document.querySelector('.agent-card.needs-approval');
  if (!card) return;
  const agentId = card.dataset.agentId;
  const preview = card.dataset.outputPreview || '';
  showApprovalModal(agentId, preview);
});

// Approval resolved
document.body.addEventListener('approval-resolved', () => {
  hideApprovalModal();
  resetSound();
});

// Pipeline complete
document.body.addEventListener('pipeline-complete', () => {
  const banner  = document.getElementById('complete-banner');
  const poller  = document.getElementById('cards-poller');
  const epoller = document.getElementById('events-poller');
  if (banner)  banner.classList.add('visible');
  setStartButtonState('complete');
  setStopButtonState('complete');
  setPauseButtonState('complete');
  setPausedBanner('');
  // Stop polling by removing the trigger
  if (poller)  poller.removeAttribute('hx-trigger');
  if (epoller) epoller.removeAttribute('hx-trigger');
  htmx.process(poller);
  htmx.process(epoller);
});

// Manual refresh
function manualRefresh(name) {
  const poller = document.getElementById('cards-poller');
  if (poller) {
    poller.setAttribute('hx-trigger', `every ${window._pollMs || 2000}ms`);
    htmx.process(poller);
    htmx.trigger(poller, 'refresh');
  }
  const epoller = document.getElementById('events-poller');
  if (epoller) {
    epoller.setAttribute('hx-trigger', `every ${window._pollMs || 2000}ms`);
    htmx.process(epoller);
    htmx.trigger(epoller, 'refresh');
  }
  const banner = document.getElementById('complete-banner');
  if (banner) banner.classList.remove('visible');
  resetSound();
}

// Card click → load detail
function loadDetail(name, agentId, cardEl) {
  document.querySelectorAll('.agent-card').forEach(c => c.classList.remove('selected'));
  if (cardEl) cardEl.classList.add('selected');
  htmx.ajax('GET', `/pipelines/${name}/agents/${agentId}`, {
    target:  '#detail-container',
    swap:    'innerHTML',
  });
}

// ── History / versioning page ────────────────────────────────────────────

function loadHistoryFile(name, logical, btnEl) {
  document.querySelectorAll('.hv-file-row').forEach(b => b.classList.remove('selected'));
  if (btnEl) btnEl.classList.add('selected');
  const diffEl = document.getElementById('history-diff-container');
  if (diffEl) diffEl.innerHTML = '';
  htmx.ajax('GET', `/pipelines/${name}/history/file?logical=${encodeURIComponent(logical)}`, {
    target: '#history-file-container',
    swap:   'innerHTML',
  });
}

function showDiff(name, logical, verA, verB) {
  if (!verB) return;
  const url = `/pipelines/${name}/history/diff`
    + `?logical=${encodeURIComponent(logical)}`
    + `&a=${encodeURIComponent(verA)}`
    + `&b=${encodeURIComponent(verB)}`;
  htmx.ajax('GET', url, {
    target: '#history-diff-container',
    swap:   'innerHTML',
  });
}

// ── Syntax highlighting (prompts + pipeline.json) ────────────────────────
//
// We render a read-only, highlighted <pre> behind a transparent <textarea>.
// The caret stays in the textarea; the colours come from the mirror layer.
// Modes are chosen from the logical file path.

function _escapeHtml(s) {
  return s.replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;');
}

// XML-style tagline highlighter for prompt .md files. Matches only the
// no-space form <name> and </name> — anything containing spaces or
// attributes is treated as plain text (same rule used by the validator).
function _highlightXmlTags(escaped) {
  return escaped.replace(
    /&lt;(\/?)([A-Za-z_][\w-]*)&gt;/g,
    (_m, slash, name) => {
      const cls = slash ? 'tok-tag-close' : 'tok-tag-open';
      return `<span class="${cls}">&lt;${slash}${name}&gt;</span>`;
    }
  );
}

// JSON tokenizer — produces highlighted HTML from raw (unescaped) input.
function _highlightJson(raw) {
  const tokenRe =
    /"(?:\\.|[^"\\])*"(?:\s*:)?|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\btrue\b|\bfalse\b|\bnull\b|[{}[\],:]/g;
  let out = '';
  let last = 0;
  let m;
  while ((m = tokenRe.exec(raw)) !== null) {
    if (m.index > last) out += _escapeHtml(raw.slice(last, m.index));
    const t = m[0];
    let cls = 'tok-punct';
    if (t.startsWith('"')) {
      if (t.endsWith(':') || /"\s*:$/.test(t)) cls = 'tok-key';
      else cls = 'tok-string';
    } else if (/^-?\d/.test(t)) {
      cls = 'tok-number';
    } else if (t === 'true' || t === 'false' || t === 'null') {
      cls = 'tok-literal';
    }
    out += `<span class="${cls}">${_escapeHtml(t)}</span>`;
    last = m.index + t.length;
  }
  if (last < raw.length) out += _escapeHtml(raw.slice(last));
  return out;
}

function _modeFor(logical) {
  if (!logical) return 'plain';
  if (logical.endsWith('.jsonl')) return 'jsonl';
  if (logical.endsWith('.json'))  return 'json';
  if (logical.endsWith('.md') || logical.endsWith('.txt')) return 'xml';
  return 'plain';
}

// JSONL: each non-blank line is its own JSON object. Tokenize per line so
// one malformed line doesn't poison the rest, and preserve exact line breaks.
function _highlightJsonl(raw) {
  return raw.split('\n').map(line => {
    if (!line) return '';
    // Skip highlighting if the line isn't something that looks like JSON.
    if (!/^\s*[\[{"]/.test(line)) return _escapeHtml(line);
    return _highlightJson(line);
  }).join('\n');
}

function _highlight(text, mode) {
  if (mode === 'json')  return _highlightJson(text);
  if (mode === 'jsonl') return _highlightJsonl(text);
  if (mode === 'xml')   return _highlightXmlTags(_escapeHtml(text));
  return _escapeHtml(text);
}

function _updateValidityBadge(form, text) {
  const badge = form.querySelector('[data-validity]');
  if (!badge) return;
  if (!text.trim()) {
    badge.textContent = '';
    badge.removeAttribute('data-state');
    return;
  }
  try {
    JSON.parse(text);
    badge.textContent = '✓ Valid JSON';
    badge.setAttribute('data-state', 'ok');
  } catch (e) {
    badge.textContent = '⚠ ' + String(e.message).replace(/^JSON\.parse:\s*/, '');
    badge.setAttribute('data-state', 'err');
  }
}

function _bindEditor(wrap) {
  if (wrap._hlBound) return;
  wrap._hlBound = true;

  const textarea = wrap.querySelector('.hv-editor');
  const mirror   = wrap.querySelector('.hv-editor-mirror code');
  if (!textarea || !mirror) return;

  const logical = wrap.getAttribute('data-logical') || '';
  const mode    = _modeFor(logical);
  const form    = wrap.closest('.hv-file-form');

  const render = () => {
    mirror.innerHTML = _highlight(textarea.value, mode);
    if (mode === 'json' && form) _updateValidityBadge(form, textarea.value);
  };
  const syncScroll = () => {
    mirror.style.transform = `translateY(${-textarea.scrollTop}px)`;
  };

  textarea.addEventListener('input',  render);
  textarea.addEventListener('scroll', syncScroll);
  render();

  if (mode === 'json' && form) {
    const btn = form.querySelector('[data-format-json]');
    if (btn && !btn._bound) {
      btn._bound = true;
      btn.addEventListener('click', () => {
        try {
          const parsed = JSON.parse(textarea.value);
          textarea.value = JSON.stringify(parsed, null, 2) + '\n';
          render();
        } catch (e) {
          _updateValidityBadge(form, textarea.value);
        }
      });
    }
  }
}

function _highlightViewers(root) {
  const scope = root || document;
  scope.querySelectorAll('pre[data-logical]').forEach(pre => {
    if (pre._hlDone) return;
    pre._hlDone = true;
    const code = pre.querySelector('code');
    if (!code) return;
    const mode = _modeFor(pre.getAttribute('data-logical') || '');
    const raw  = code.textContent;
    code.innerHTML = _highlight(raw, mode);
  });
}

function initSyntaxHighlighting(root) {
  const scope = root || document;
  scope.querySelectorAll('.hv-editor-wrap').forEach(_bindEditor);
  _highlightViewers(scope);
}

// Re-bind after every HTMX swap / load, and once on initial page load.
document.addEventListener('htmx:afterSwap', (e) => initSyntaxHighlighting(e.target));
document.addEventListener('htmx:load',      (e) => initSyntaxHighlighting(e.target));
document.addEventListener('DOMContentLoaded', () => initSyntaxHighlighting());
