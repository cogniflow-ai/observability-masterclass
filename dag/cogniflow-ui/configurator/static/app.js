/* Cogniflow Configurator — client-side helpers */

// ── Theme ─────────────────────────────────────────────────────
function toggleTheme() {
  const html = document.documentElement;
  const next = html.dataset.theme === 'dark' ? 'light' : 'dark';
  html.dataset.theme = next;
  try { localStorage.setItem('cgTheme', next); } catch(e) {}
}

(function applyTheme() {
  try {
    const saved = localStorage.getItem('cgTheme');
    if (saved) document.documentElement.dataset.theme = saved;
  } catch(e) {}
})();

// ── Panel collapse (sidebar / right panel) ────────────────────
// Initial state is applied inline in base.html to prevent a paint flash.
// This only handles runtime toggling + persistence.
function togglePanel(which) {
  const cls = which === 'sidebar' ? 'sidebar-collapsed' : 'right-collapsed';
  const key = 'cogniflow-' + cls;
  const root = document.documentElement;
  const willCollapse = !root.classList.contains(cls);
  root.classList.toggle(cls, willCollapse);
  try { localStorage.setItem(key, willCollapse ? '1' : '0'); } catch(e) {}
}

function expandPanel(which) {
  const cls = which === 'sidebar' ? 'sidebar-collapsed' : 'right-collapsed';
  const key = 'cogniflow-' + cls;
  const root = document.documentElement;
  if (!root.classList.contains(cls)) return;  // already expanded
  root.classList.remove(cls);
  try { localStorage.setItem(key, '0'); } catch(e) {}
}

// Auto-expand the right panel whenever an HTMX request targets content
// inside it (e.g. clicking an "Open file →" link in the validation banner,
// or any agent/file action that swaps #right-panel-content). Without this,
// a collapsed panel would silently receive the swap and the user would see
// nothing happen.
document.addEventListener('htmx:beforeRequest', function (evt) {
  const target = evt.detail && evt.detail.target;
  if (!target || !target.closest) return;
  if (target.closest('.right-panel')) expandPanel('right');
});

// ── Modal helpers ─────────────────────────────────────────────
function showModal(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = 'flex';
}
function hideModal(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = 'none';
}

// Close modal on overlay click
document.addEventListener('click', function(e) {
  if (e.target.classList.contains('modal-overlay')) {
    e.target.style.display = 'none';
  }
});

// ── Edit agent modal population ───────────────────────────────
function openEditAgent(id, name, type, category, description, depends, timeout, approval) {
  document.getElementById('edit-field-id').value          = id;
  document.getElementById('edit-agent-id').value          = id;
  document.getElementById('edit-field-name').value        = name;
  document.getElementById('edit-field-category').value    = category;
  document.getElementById('edit-field-description').value = description;
  document.getElementById('edit-field-depends').value     = depends;
  document.getElementById('edit-field-timeout').value     = timeout;

  const typeEl = document.getElementById('edit-field-type');
  if (typeEl) {
    for (let opt of typeEl.options) {
      opt.selected = opt.value === type;
    }
  }
  const appEl = document.getElementById('edit-field-approval');
  if (appEl) {
    for (let opt of appEl.options) {
      opt.selected = opt.value === approval;
    }
  }
  showModal('modal-edit-agent');
}

function submitEditAgent(e) {
  e.preventDefault();
  const id   = document.getElementById('edit-agent-id').value;
  const form = document.getElementById('edit-agent-form');
  const data = new FormData(form);
  const url  = `/pipeline/${getPipelineName()}/graph/agent/${id}`;

  fetch(url, {
    method: 'PUT',
    body: data,
    headers: { 'HX-Request': 'true' }
  })
  .then(r => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.text();
  })
  .then(html => {
    const panel = document.getElementById('topology-panel');
    if (panel) {
      panel.outerHTML = html;
      if (window.htmx) {
        window.htmx.process(document.getElementById('topology-panel') || document.body);
      }
    }
    hideModal('modal-edit-agent');
  })
  .catch(err => {
    console.error('Edit agent failed:', err);
    alert('Failed to update agent. Check server logs.');
  });
}

function getPipelineName() {
  const m = window.location.pathname.match(/\/pipeline\/([^/]+)/);
  return m ? m[1] : '';
}

// ── Agent filter ──────────────────────────────────────────────
function filterAgents(query) {
  const cards = document.querySelectorAll('#agents-grid .agent-card');
  const q = query.toLowerCase();
  cards.forEach(card => {
    const text = (card.dataset.search || '').toLowerCase();
    card.style.display = (q === '' || text.includes(q)) ? '' : 'none';
  });
}

// ── Token count update ────────────────────────────────────────
function updateTokenCount(textarea) {
  const chars = textarea.value.length;
  const tokens = Math.max(1, Math.round(chars / 4));
  const statEl = textarea.closest('.prompt-editor')
                         ?.querySelector('.prompt-stats span');
  if (statEl) {
    statEl.textContent = `~${tokens} tokens`;
  }
}

// ── Prompt tab switching ──────────────────────────────────────
function showPromptPane(pane) {
  ['edit','preview','history'].forEach(p => {
    const el = document.getElementById('prompt-pane-' + p);
    if (el) el.style.display = p === pane ? '' : 'none';
  });
  document.querySelectorAll('.prompt-tabs .subtab').forEach(btn => {
    btn.classList.toggle('active', btn.textContent.toLowerCase().includes(pane));
  });
}

// ── Syntax highlighting (prompts + JSON) ──────────────────────
//
// Port of the Observer's mirror-overlay highlighter. A transparent
// <textarea> sits above a colored <pre><code> mirror; the user types
// into the textarea and the mirror is repainted on every input.

function _escapeHtml(s) {
  return s.replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;');
}

function _highlightXmlTags(escaped) {
  return escaped.replace(
    /&lt;(\/?)([A-Za-z_][\w-]*)&gt;/g,
    (_m, slash, name) => {
      const cls = slash ? 'tok-tag-close' : 'tok-tag-open';
      return `<span class="${cls}">&lt;${slash}${name}&gt;</span>`;
    }
  );
}

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

function _highlightJsonl(raw) {
  return raw.split('\n').map(line => {
    if (!line) return '';
    if (!/^\s*[\[{"]/.test(line)) return _escapeHtml(line);
    return _highlightJson(line);
  }).join('\n');
}

function _modeFor(logical) {
  if (!logical) return 'plain';
  if (logical.endsWith('.jsonl')) return 'jsonl';
  if (logical.endsWith('.json'))  return 'json';
  if (logical.endsWith('.md') || logical.endsWith('.txt')) return 'xml';
  return 'plain';
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

// Port of validation.validate_prompt_taglines(text, required).
// Returns null on success, or a string error message. Mirrors the server
// rules exactly: (1) every required name must appear as both <n> and </n>;
// (2) every tagline must be paired; (3) no nesting.
function _validateTaglines(text, required) {
  const re = /<(\/?)([A-Za-z_][\w-]*)>/g;
  const matches = [];
  let m;
  while ((m = re.exec(text)) !== null) {
    matches.push({ slash: m[1], name: m[2], index: m.index });
  }

  const openNames  = new Set(matches.filter(x => !x.slash).map(x => x.name));
  const closeNames = new Set(matches.filter(x =>  x.slash).map(x => x.name));
  const missing = [];
  for (const name of required) {
    if (!openNames.has(name))  missing.push('<' + name + '>');
    if (!closeNames.has(name)) missing.push('</' + name + '>');
  }
  if (missing.length) {
    return 'Missing required tagline(s): ' + missing.join(', ');
  }

  const stack = [];
  const lineOf = (idx) => (text.slice(0, idx).match(/\n/g) || []).length + 1;
  for (const x of matches) {
    const line = lineOf(x.index);
    if (!x.slash) {
      if (stack.length) {
        const top = stack[stack.length - 1];
        return `Nested tagline on line ${line}: <${x.name}> opened while ` +
               `<${top.name}> (line ${top.line}) is still open. ` +
               `Taglines cannot be nested.`;
      }
      stack.push({ name: x.name, line });
    } else {
      if (!stack.length) {
        return `Closing tagline </${x.name}> on line ${line} has no ` +
               `matching opening <${x.name}>.`;
      }
      const top = stack[stack.length - 1];
      if (top.name !== x.name) {
        return `Mismatched tagline on line ${line}: found </${x.name}> ` +
               `but <${top.name}> (line ${top.line}) is still open.`;
      }
      stack.pop();
    }
  }
  if (stack.length) {
    const top = stack[stack.length - 1];
    return `Unclosed tagline: <${top.name}> opened on line ${top.line} is never closed.`;
  }
  return null;
}

function _updateTaglineBadge(form, wrap, text) {
  const badge = form.querySelector('[data-validity]');
  if (!badge) return;
  if (!text.trim()) {
    badge.textContent = '';
    badge.removeAttribute('data-state');
    return;
  }
  const raw = wrap.getAttribute('data-required-taglines') || '';
  const required = raw.split(',').map(s => s.trim()).filter(Boolean);
  const err = _validateTaglines(text, required);
  if (err === null) {
    badge.textContent = required.length
      ? `✓ Taglines valid (${required.join(', ')})`
      : '✓ Taglines valid';
    badge.setAttribute('data-state', 'ok');
  } else {
    badge.textContent = '⚠ ' + err;
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
  const form    = wrap.closest('form');

  const isPromptFile = /(?:^|\/)(?:01_system|02_prompt)\.md$/.test(logical);
  const taglineCheck = mode === 'xml' && form &&
                       (wrap.hasAttribute('data-required-taglines') || isPromptFile);

  const render = () => {
    mirror.innerHTML = _highlight(textarea.value, mode);
    if (mode === 'json' && form) _updateValidityBadge(form, textarea.value);
    if (taglineCheck)             _updateTaglineBadge(form, wrap, textarea.value);
  };
  const syncScroll = () => {
    mirror.style.transform = `translateY(${-textarea.scrollTop}px)`;
  };

  textarea.addEventListener('input',  () => { render(); updateTokenCount(textarea); });
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

document.addEventListener('htmx:afterSwap', (e) => initSyntaxHighlighting(e.target));
document.addEventListener('htmx:load',      (e) => initSyntaxHighlighting(e.target));
document.addEventListener('DOMContentLoaded', () => initSyntaxHighlighting());

// ── Tag-chip input ────────────────────────────────────────────
// <div class="tag-input" data-placeholder="...">
//   <input type="hidden" name="..." value="role,guardrails"/>
// </div>
// Renders the CSV value as chips, adds an inline text entry at the end.
// Enter or comma commits a chip; Backspace on empty entry removes the last
// chip; clicking the × on a chip removes it. The hidden input is kept in
// sync so normal form submission sends the CSV string.
function initTagInputs(scope) {
  scope = scope || document;
  scope.querySelectorAll('.tag-input').forEach(_bindTagInput);
}

function _bindTagInput(wrap) {
  if (wrap._tagBound) return;
  wrap._tagBound = true;
  const hidden = wrap.querySelector('input[type="hidden"]');
  if (!hidden) return;

  const placeholder = wrap.dataset.placeholder || 'add tag';
  const saveUrl = wrap.dataset.saveUrl || '';
  const saveKind = wrap.dataset.kind || '';
  // Parse initial CSV → trimmed, deduplicated, lowercase-preserving list.
  let tags = (hidden.value || '').split(',')
                .map(s => s.trim()).filter(Boolean);
  tags = Array.from(new Set(tags));

  function render() {
    // Remove existing chips + entry (keep hidden input).
    [...wrap.querySelectorAll('.tag-chip, .tag-entry')].forEach(el => el.remove());

    tags.forEach((t, i) => {
      const chip = document.createElement('span');
      chip.className = 'tag-chip';
      chip.innerHTML = '';
      const label = document.createElement('span');
      label.className = 'tag-chip-label';
      label.textContent = t;
      const x = document.createElement('button');
      x.type = 'button';
      x.className = 'tag-chip-remove';
      x.textContent = '×';
      x.setAttribute('aria-label', 'Remove ' + t);
      x.addEventListener('click', () => {
        tags.splice(i, 1);
        sync(); render();
        entry().focus();
      });
      chip.appendChild(label);
      chip.appendChild(x);
      wrap.appendChild(chip);
    });

    const inp = document.createElement('input');
    inp.type = 'text';
    inp.className = 'tag-entry';
    inp.placeholder = tags.length ? '' : placeholder;
    inp.autocomplete = 'off';
    inp.spellcheck = false;
    inp.addEventListener('keydown', onKey);
    inp.addEventListener('blur', commitCurrent);
    wrap.appendChild(inp);
  }

  function entry() { return wrap.querySelector('.tag-entry'); }

  function sync() {
    hidden.value = tags.join(',');
    if (!saveUrl || !saveKind) return;
    // Fire-and-forget auto-save — no Apply click needed.
    wrap.classList.remove('tag-input-saved', 'tag-input-error');
    wrap.classList.add('tag-input-saving');
    const body = 'kind=' + encodeURIComponent(saveKind)
               + '&tags=' + encodeURIComponent(hidden.value);
    fetch(saveUrl, {
      method: 'POST',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: body,
    }).then(r => {
      wrap.classList.remove('tag-input-saving');
      if (r.ok) {
        wrap.classList.add('tag-input-saved');
        setTimeout(() => wrap.classList.remove('tag-input-saved'), 900);
      } else {
        wrap.classList.add('tag-input-error');
      }
    }).catch(() => {
      wrap.classList.remove('tag-input-saving');
      wrap.classList.add('tag-input-error');
    });
  }

  function commit(raw) {
    const v = (raw || '').trim().replace(/^[<]+|[>]+$/g, '');  // strip stray angle brackets
    if (!v) return false;
    if (!/^[A-Za-z_][\w-]*$/.test(v)) return false;            // tagline shape
    if (tags.includes(v)) return false;
    tags.push(v);
    sync();
    return true;
  }

  function commitCurrent() {
    const inp = entry();
    if (!inp) return;
    if (commit(inp.value)) { inp.value = ''; render(); entry().focus(); }
  }

  function onKey(e) {
    const inp = e.currentTarget;
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      commitCurrent();
    } else if (e.key === 'Backspace' && inp.value === '' && tags.length) {
      e.preventDefault();
      tags.pop();
      sync();
      render();
      entry().focus();
    }
  }

  render();
}

document.addEventListener('DOMContentLoaded', () => initTagInputs());
document.addEventListener('htmx:afterSwap', (e) => initTagInputs(e.target));
document.addEventListener('htmx:load',      (e) => initTagInputs(e.target));

// ── Mode toggle (dag / cyclic) ────────────────────────────────
// Auto-saves on click, mirrors the Horizontal/Vertical orient-toggle pattern.
function initModeToggles(scope) {
  scope = scope || document;
  scope.querySelectorAll('.mode-toggle').forEach(wrap => {
    if (wrap._modeBound) return;
    wrap._modeBound = true;
    wrap.querySelectorAll('.mode-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        if (btn.classList.contains('active')) return;
        wrap.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const mode = btn.dataset.mode;
        // Mirror the mode badge in the page header.
        const badge = document.querySelector('.badge-mode');
        if (badge) { badge.textContent = mode; badge.className = 'badge-mode ' + mode; }
        fetch(wrap.dataset.saveUrl, {
          method: 'POST',
          headers: {'Content-Type': 'application/x-www-form-urlencoded'},
          body: 'mode=' + encodeURIComponent(mode),
        }).then(() => {
          // If the Diagram subtab is currently shown, re-render the SVG —
          // layout differs between dag and cyclic modes.
          const viewEl = document.getElementById('graph-view-wrap');
          const refresh = wrap.dataset.refreshUrl;
          if (viewEl && refresh && window.htmx) {
            htmx.ajax('GET', refresh,
                      { target: '#graph-view-wrap', swap: 'outerHTML' });
          }
        });
      });
    });
  });
}
document.addEventListener('DOMContentLoaded', () => initModeToggles());
document.addEventListener('htmx:afterSwap', (e) => initModeToggles(e.target));
document.addEventListener('htmx:load',      (e) => initModeToggles(e.target));

// ── Validate-taglines checkbox auto-save ──────────────────────
function saveValidateTaglines(pipelineName, checked) {
  fetch('/pipeline/' + encodeURIComponent(pipelineName)
        + '/settings/validate-taglines', {
    method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: 'enabled=' + (checked ? 'true' : 'false'),
  });
}

// ── Tab key cycles Graph subtabs (Diagram → Topology → Configuration → …)
// Intentionally scoped: ignored while focus is in an editable control so the
// standard browser focus-advance in forms still works.
document.addEventListener('keydown', function (e) {
  if (e.key !== 'Tab') return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;  // let OS/browser shortcuts through
  const a = document.activeElement;
  if (a && (
    a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' ||
    a.tagName === 'SELECT' || a.tagName === 'BUTTON' ||
    a.isContentEditable)) return;
  const bar = document.getElementById('graph-subtabs');
  if (!bar) return;
  const subtabs = Array.from(bar.querySelectorAll('.subtab'));
  if (!subtabs.length) return;
  let idx = subtabs.findIndex(s => s.classList.contains('active'));
  if (idx < 0) idx = 0;
  const step = e.shiftKey ? -1 : 1;                // Shift+Tab cycles backward
  const next = subtabs[(idx + step + subtabs.length) % subtabs.length];
  e.preventDefault();
  window.location.href = next.getAttribute('href');
});

// ── HTMX events ───────────────────────────────────────────────
document.addEventListener('htmx:afterRequest', function(evt) {
  // If pipeline.json was saved via JSON editor, refresh the topology subtab reference
  const url = evt.detail?.requestConfig?.path || '';
  if (url.includes('/graph/json') || url.includes('/graph/agent') || url.includes('/graph/edge')) {
    // Update validation badge if present in response headers or repaint
    const badge = document.getElementById('val-badge');
    if (badge) {
      // badge text may be updated via hx-swap-oob in topology partial
    }
  }
});

// ── Specialize button: live timer + top banner ───────────────
// Prompt specialization shells out to `claude` and can take a minute or more.
// We don't change the network call — just surface progress so the user can
// see the process is alive:
//   - the clicked button text flips to "⏱ m:ss" and ticks every second
//   - a pulsing banner under the top header shows which agent is running
// Both stop on htmx:afterRequest (success OR failure) for the same form.
(function () {
  function fmt(totalSec) {
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    return `${m}:${s.toString().padStart(2, '0')}`;
  }

  // Track running specializations. Keyed by the form element so concurrent
  // runs (rare but possible across different agents) each get their own timer.
  const running = new Map();

  function startFormTimer(form) {
    if (running.has(form)) return;   // already running
    const btn = form.querySelector('.specialize-btn');
    const agentId = form.dataset.agentId || '';
    if (!btn) return;

    const originalText = btn.textContent;
    const originalWidth = btn.offsetWidth;
    btn.dataset.originalText = originalText;
    btn.style.minWidth = originalWidth + 'px';  // prevent width jitter as counter ticks
    btn.disabled = true;
    btn.classList.add('specialize-running');

    const startMs = Date.now();
    const tick = () => {
      const elapsed = Math.floor((Date.now() - startMs) / 1000);
      btn.textContent = `⏱ calling claude… ${fmt(elapsed)}`;
      updateBannerTimer(elapsed);
    };
    tick();
    const handle = setInterval(tick, 1000);

    running.set(form, { handle, startMs, agentId });
    showBanner(agentId);
  }

  function stopFormTimer(form) {
    const entry = running.get(form);
    if (!entry) return;
    clearInterval(entry.handle);
    running.delete(form);

    const btn = form.querySelector('.specialize-btn');
    if (btn) {
      btn.disabled = false;
      btn.classList.remove('specialize-running');
      btn.style.minWidth = '';
      btn.textContent = btn.dataset.originalText || '⚙ Generate';
    }

    // Hide the banner only when no other specialize is in flight.
    if (running.size === 0) {
      hideBanner();
    } else {
      // Switch banner to show one of the still-running runs.
      const [, any] = running.entries().next().value;
      showBanner(any.agentId);
      updateBannerTimer(Math.floor((Date.now() - any.startMs) / 1000));
    }
  }

  function showBanner(agentId) {
    const banner = document.getElementById('specialize-top-banner');
    const who = document.getElementById('specialize-banner-agent');
    if (!banner) return;
    if (who) who.textContent = agentId || '(agent)';
    banner.hidden = false;
  }

  function hideBanner() {
    const banner = document.getElementById('specialize-top-banner');
    if (!banner) return;
    banner.hidden = true;
    const t = document.getElementById('specialize-banner-timer');
    if (t) t.textContent = '0:00';
  }

  function updateBannerTimer(elapsed) {
    const t = document.getElementById('specialize-banner-timer');
    if (t) t.textContent = fmt(elapsed);
  }

  // HTMX fires htmx:beforeRequest / htmx:afterRequest bubbling up from the
  // originating element. Delegate on the document.
  document.addEventListener('htmx:beforeRequest', (evt) => {
    const el = evt.target;
    if (!el || !el.closest) return;
    const form = el.closest('.specialize-form');
    if (form) startFormTimer(form);
  });

  document.addEventListener('htmx:afterRequest', (evt) => {
    const el = evt.target;
    if (!el || !el.closest) return;
    const form = el.closest('.specialize-form');
    if (form) stopFormTimer(form);
  });

  // Safety net: if HTMX aborts or the page is navigated, clean up.
  window.addEventListener('beforeunload', () => {
    running.forEach((entry) => clearInterval(entry.handle));
    running.clear();
  });
})();
