'use strict';
const $ = (s) => document.querySelector(s), sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const state = { user: null, analysis: null };

/* ---------- helpers (DOM is built with textContent, never innerHTML, so thesis/AI text cannot inject markup) ---------- */
const h = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (v != null && v !== false) e.setAttribute(k, v === true ? '' : v);
  }
  e.append(...kids.flat(9).filter((x) => x != null && x !== false));
  return e;
};
const replace = (target, ...kids) => target.replaceChildren(...kids.flat(9).filter((x) => x != null && x !== false));
const mount = (...n) => replace($('#view'), ...n);
const toast = (m, bad) => { const t = $('#toast'); t.textContent = m; t.className = 'show' + (bad ? ' bad' : ''); clearTimeout(toast.t); toast.t = setTimeout(() => (t.className = ''), 4000); };
const guard = (fn) => async (...a) => {
  const target = a[0]?.currentTarget;
  const control = target instanceof HTMLButtonElement ? target
    : target instanceof HTMLFormElement ? target.querySelector('button:not([type]), button[type="submit"], input[type="submit"]') : null;
  if (target instanceof HTMLElement) target.setAttribute('aria-busy', 'true');
  if (control) {
    control.disabled = true;
    control.classList.add('is-loading');
  }
  try { return await fn(...a); } catch (e) {
    if (e.status === 401) { state.user = null; return render(); }
    toast(e.message || 'Something went wrong. Please try again.', true);
  } finally {
    if (control) {
      control.disabled = false;
      control.classList.remove('is-loading');
    }
    if (target instanceof HTMLElement) target.removeAttribute('aria-busy');
  }
};
const btn = (text, fn, cls = '') => h('button', { class: 'btn ' + cls, type: 'button', onclick: guard(fn) }, text);
const empty = (t) => h('p', { class: 'muted' }, t);
const stat = (label, v, icon) => h('div', { class: 'stat' }, h('span', { class: 'stat-icon', 'aria-hidden': 'true' }, icon || '•'), h('div', {}, h('b', {}, String(v ?? 0)), h('span', {}, label)));
const badge = (t) => h('span', { class: 'badge ' + String(t || 'unknown').replace(/[^a-z0-9_-]/gi, '') }, String(t || 'unknown').replaceAll('_', ' '));
const date = (s) => (s ? new Date(s).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : 'none');
const table = (cols, rows) => h('div', { class: 'scroll' }, h('table', {}, h('thead', {}, h('tr', {}, cols.map((c) => h('th', { scope: 'col' }, c)))), h('tbody', {}, rows.map((r) => h('tr', {}, r.map((c) => h('td', {}, c)))))));
const field = (name, label, type = 'text') => h('label', {}, label, h('input', { name, type, required: true, autocomplete: type === 'password' ? 'current-password' : name }));
const researchQualityNotice = (thesis) => {
  if (thesis.status !== 'analyzed') return null;
  if (thesis.research_quality_evaluation === 'complete') {
    const count = thesis.research_quality_issue_count;
    if (!Number.isInteger(count)) return null;
    return h('p', { class: 'quality-flash complete', role: 'status' },
      count === 0
        ? 'Research-quality check complete: no research-quality issues were found.'
        : `Research-quality check found ${count} potential issue${count === 1 ? '' : 's'}. Ask your supervisor to review them; detailed feedback is released after review.`);
  }
  if (thesis.research_quality_evaluation === 'unavailable') {
    return h('p', { class: 'quality-flash unavailable', role: 'status' },
      'Research-quality evaluation was unavailable, so no result is reported. Formatting and citation checks are still available.');
  }
  if (thesis.research_quality_evaluation === 'failed') {
    return h('p', { class: 'quality-flash unavailable', role: 'status' },
      'Research-quality evaluation could not be completed. Check the analysis note in your report; no zero-issues result is implied.');
  }
  if (thesis.research_quality_evaluation === 'incomplete') {
    return h('p', { class: 'quality-flash unavailable', role: 'status' },
      'Research-quality evaluation was incomplete, so the issue count may not be complete. Check the analysis note in your report.');
  }
  return null;
};
async function download(url, name) {
  const objectUrl = URL.createObjectURL(await API.get(url));
  const a = h('a', { href: objectUrl, download: name });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

/* ---------- shared components ---------- */
const itemCard = (i, controls) => h('article', { class: 'item ' + i.severity },
  h('div', { class: 'meta' }, badge(i.severity), badge(i.status), i.student_status && badge('fix_accepted')),
  h('p', {}, i.content),
  i.suggestion && h('p', { class: 'sug' }, h('b', {}, 'Suggested fix: '), i.suggestion),
  i.supervisor_comment && h('p', { class: 'cmt' }, h('b', {}, 'Supervisor: '), i.supervisor_comment),
  controls && h('div', { class: 'ctl' }, controls));

const chainsView = (chains) => chains.length ? chains.map((c) => h('section', { class: 'panel' }, h('h3', {}, c.title),
  h('p', { class: 'muted' }, `${c.revision_cycles} revision cycle(s)` + (c.compliance_change_first_to_latest != null ? `, compliance findings changed by ${c.compliance_change_first_to_latest} from first to latest` : '')),
  table(['Version', 'Status', 'All issues', 'Compliance findings', 'Resolved', 'New'], c.versions.map((v) =>
    ['v' + v.version, badge(v.status), v.issues.total, v.compliance_findings, v.since_previous?.resolved ?? 'none', v.since_previous?.new ?? 'none'])))) : empty('Nothing to show yet.');

/* ---------- login / register ---------- */
function loginView() {
  let registering = false;
  const f = h('form', { onsubmit: guard(async (e) => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(f));
    if (registering) {
      await API.post('/api/auth/register', { name: d.name, email: d.email, password: d.password });
      toast('Account created. You can now log in.'); registering = false; return draw();
    }
    const r = await API.post('/api/auth/login', { email: d.email, password: d.password, role: d.role });
    API.token = r.csrf_token; state.user = r.user; location.hash = '#/'; render();
  }) });
  function draw() {
    replace(f,
      registering && field('name', 'Full name'), field('email', 'Email', 'email'), field('password', 'Password', 'password'),
      registering && h('p', { class: 'muted' }, 'At least 8 characters, with letters and numbers.'),
      !registering && h('label', {}, 'Log in as', h('select', { name: 'role' }, ['student', 'supervisor', 'admin'].map((r) => h('option', { value: r }, r)))),
      h('button', { class: 'btn', type: 'submit' }, registering ? 'Create student account' : 'Log in'),
      h('p', { class: 'muted' }, registering ? 'Already registered? ' : 'New student? ',
        h('a', { href: '#', onclick: (e) => { e.preventDefault(); registering = !registering; draw(); } }, registering ? 'Log in' : 'Create an account')));
  }
  draw();
  return h('section', { class: 'panel auth' }, h('div', { class: 'auth-mark', 'aria-hidden': 'true' }, 'TS'),
    h('p', { class: 'eyebrow' }, 'UNIZIK POSTGRADUATE SUPPORT'), h('h1', {}, 'Thesis Support System'),
    h('p', { class: 'muted' }, 'Compliance checks and quality feedback for UNIZIK postgraduate theses.'), f);
}

/* ---------- student ---------- */
function startAnalysis(id) {
  state.analysis = { id: Number(id), status: 'queued', polling: false };
  location.hash = '#/analysis/' + id;
}
function analysisView(id) {
  const thesisId = Number(id), panel = h('section', { class: 'panel analysis-panel', 'aria-live': 'polite' });
  const paint = (s) => {
    const active = !s.done && !s.stale && !s.pollError;
    panel.setAttribute('aria-busy', active ? 'true' : 'false');
    replace(panel,
      h('div', { class: 'analysis-mark' }, active ? h('span', { class: 'spinner', 'aria-hidden': 'true' }) : s.status === 'analyzed' ? '✓' : '!'),
      h('div', { class: 'analysis-copy' }, h('p', { class: 'eyebrow' }, 'THESIS ANALYSIS'), h('h2', {}, active ? 'Checking your draft' : s.pollError ? 'Status temporarily unavailable' : s.status === 'analyzed' ? 'Analysis complete' : s.stale ? 'Analysis needs attention' : 'Analysis could not be completed'),
        h('p', { class: 'muted' }, active ? 'We are checking your document structure, formatting, citations and research quality. This may take a few minutes.' : s.pollError ? 'The analysis may still be running, but its latest status could not be retrieved.' : (s.analysis_note || (s.status === 'analyzed' ? 'Your results are ready to review.' : 'You can retry the analysis after checking the file.'))),
        active && h('div', { class: 'analysis-status' }, badge(s.status), h('span', { class: 'muted' }, 'This page will update automatically.')),
        s.pollError && btn('Check status again', () => {
          state.analysis = { ...state.analysis, pollError: null, polling: false };
          paint(state.analysis);
          pollAnalysis(thesisId);
        }, 'ghost'),
        (s.status === 'analysis_failed' || s.stale) && btn('Retry analysis', async () => {
          await API.post(`/api/student/theses/${thesisId}/reanalyze`);
          state.analysis = { id: thesisId, status: 'queued', polling: false };
          paint(state.analysis);
          pollAnalysis(thesisId);
        }),
        s.status === 'analyzed' && btn('Open feedback report', () => { location.hash = '#/report/' + thesisId; }, 'ok'),
        h('a', { class: 'btn ghost', href: '#/' }, 'Back to my theses')));
  };
  const current = state.analysis?.id === thesisId ? state.analysis : { id: thesisId, status: 'queued' };
  state.analysis = current;
  paint(current);
  if (!current.done && !current.stale && !current.pollError) pollAnalysis(thesisId);
  mount(h('div', { class: 'page-heading' }, h('a', { class: 'back-link', href: '#/' }, '← My theses')), panel);
}
async function pollAnalysis(id) {
  if (state.analysis?.id !== id || state.analysis.polling) return;
  state.analysis.polling = true;
  let failures = 0;
  try {
    while (state.analysis?.id === id) {
      let result;
      try {
        result = await API.get(`/api/student/theses/${id}/status`);
        failures = 0;
      } catch (e) {
        if (e.status === 401) { state.user = null; render(); return; }
        failures += 1;
        if (failures >= 4) {
          state.analysis = { ...state.analysis, pollError: e.message || 'Could not refresh analysis status.', polling: false };
          if (here()[0] === 'analysis' && Number(here()[1]) === id) analysisView(id);
          toast('Could not refresh your analysis status. Please try again from this page.', true);
          return;
        }
        await sleep(Math.min(1000 * (2 ** failures), 8000));
        continue;
      }
      state.analysis = { ...state.analysis, ...result, polling: true };
      if (here()[0] === 'analysis' && Number(here()[1]) === id) analysisView(id);
      if (result.done) {
        state.analysis.polling = false;
        if (result.status === 'analyzed') {
          toast('Analysis complete. Your report is ready.');
          if (here()[0] === 'analysis' && Number(here()[1]) === id) location.hash = '#/report/' + id;
        } else toast(result.analysis_note || 'Analysis failed. You can retry from this page.', true);
        return;
      }
      if (result.stale) {
        state.analysis.polling = false;
        if (here()[0] === 'analysis' && Number(here()[1]) === id) analysisView(id);
        return;
      }
      await sleep(2500);
    }
  } finally {
    if (state.analysis?.id === id) state.analysis.polling = false;
  }
}
const resubmitBtn = (x) => {
  const inp = h('input', { type: 'file', accept: '.docx,.pdf', hidden: true, onchange: guard(async () => {
    if (!inp.files[0]) return;
    const fd = new FormData(); fd.append('file', inp.files[0]); fd.append('thesis_id', x.id);
    startAnalysis((await API.post('/api/student/resubmit', fd)).thesis.id);
  }) });
  return [inp, btn('Resubmit revised draft', () => inp.click(), 'small ghost')];
};
const thesisActions = (x) => h('div', { class: 'row' },
  x.status === 'analyzed' && [
    h('a', { class: 'btn small', href: '#/report/' + x.id }, 'Open report'),
    btn('PDF', () => download(`/api/student/theses/${x.id}/export/pdf`, `thesis_${x.id}_feedback.pdf`), 'small ghost'),
    x.file_type === 'docx' && btn('Annotated DOCX', () => download(`/api/student/theses/${x.id}/export/docx`, `thesis_${x.id}_annotated.docx`), 'small ghost'),
    x.file_type === 'docx' && btn('DOCX, accepted fixes only', () => download(`/api/student/theses/${x.id}/export/docx?scope=accepted`, `thesis_${x.id}_accepted_fixes.docx`), 'small ghost'),
    btn(x.review_requested_at ? 'Sent to supervisor' : 'Send to supervisor', async () => { await API.post(`/api/student/theses/${x.id}/submit-for-review`); toast('Sent to your supervisor.'); render(); }, 'small ghost'),
    resubmitBtn(x)],
  x.status === 'analysis_failed' && btn('Retry analysis', async () => {
    await API.post(`/api/student/theses/${x.id}/reanalyze`);
    startAnalysis(x.id);
  }, 'small'),
  ['queued', 'processing'].includes(x.status) && h('a', { class: 'btn small ghost', href: '#/analysis/' + x.id }, 'View analysis'));

async function studentHome() {
  const [s, t] = await Promise.all([API.get('/api/student/summary'), API.get('/api/student/theses')]);
  const i = s.issues_in_latest, query = h('input', { type: 'search', placeholder: 'Search your thesis drafts', 'aria-label': 'Search thesis drafts' });
  const list = h('div', { class: 'thesis-grid' });
  const draw = () => {
    const found = t.theses.filter((x) => `${x.title} ${x.status} ${x.version}`.toLowerCase().includes(query.value.toLowerCase()));
    replace(list, ...(found.length ? found.map((x) => h('article', { class: 'thesis-card' },
      h('div', { class: 'thesis-card-top' }, h('div', {}, h('span', { class: 'eyebrow' }, `VERSION ${x.version}`), h('h3', {}, x.title)), badge(x.status)),
      h('p', { class: 'muted' }, `Submitted ${date(x.created_at)}${x.file_type ? ` · ${x.file_type.toUpperCase()}` : ''}`),
      researchQualityNotice(x),
      h('div', { class: 'row thesis-actions' }, thesisActions(x)))) : [empty(query.value ? 'No drafts match your search.' : 'You have not uploaded a draft yet. Choose Upload draft to start.')]));
  };
  query.addEventListener('input', draw);
  draw();
  mount(h('div', { class: 'page-heading' }, h('div', {}, h('p', { class: 'eyebrow' }, 'STUDENT WORKSPACE'), h('h2', {}, `Welcome, ${state.user.name.split(' ')[0]}`), h('p', { class: 'muted' }, 'Manage drafts, review feedback and track your thesis progress.')),
    h('a', { class: 'btn', href: '#/upload' }, '+ Upload draft')),
    h('div', { class: 'stats' }, stat('Submissions', s.submissions, '↗'), stat('Analysed', s.analyzed, '✓'), stat('Issues in latest version', i.total, '≡'), stat('High priority', i.by_level.high, '!')),
    h('div', { class: 'section-heading' }, h('div', {}, h('h3', {}, 'Your drafts'), h('p', { class: 'muted' }, `${t.theses.length} submission${t.theses.length === 1 ? '' : 's'} in your workspace`)), query), list);
}
function uploadView() {
  const title = h('input', { name: 'title', required: true, maxlength: 300, placeholder: 'e.g. An NLP-driven thesis support system' });
  const file = h('input', { name: 'file', type: 'file', accept: '.docx,.pdf', required: true, 'aria-label': 'Choose a DOCX or PDF thesis file' });
  const fileNote = h('span', { class: 'file-note muted' }, 'Choose a DOCX or PDF file, or drop it here.');
  file.addEventListener('change', () => { fileNote.textContent = file.files[0] ? `${file.files[0].name} · ${(file.files[0].size / 1024 / 1024).toFixed(1)} MB` : 'Choose a DOCX or PDF file, or drop it here.'; });
  const dropZone = h('label', { class: 'drop-zone' }, h('span', { class: 'upload-icon', 'aria-hidden': 'true' }, '↑'),
    h('b', {}, 'Choose your thesis file'), file, fileNote);
  dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('is-dragging'); });
  dropZone.addEventListener('dragleave', (e) => {
    if (!dropZone.contains(e.relatedTarget)) dropZone.classList.remove('is-dragging');
  });
  dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('is-dragging');
    if (e.dataTransfer?.files?.length) {
      file.files = e.dataTransfer.files;
      file.dispatchEvent(new Event('change', { bubbles: true }));
    }
  });
  const f = h('form', { class: 'panel upload-form', onsubmit: guard(async (e) => {
    e.preventDefault();
    if (!file.files[0]) return toast('Choose a thesis file to upload.', true);
    const ext = file.files[0].name.split('.').pop().toLowerCase();
    if (!['docx', 'pdf'].includes(ext)) return toast('Upload a DOCX or PDF file.', true);
    const r = await API.post('/api/student/upload', new FormData(f));
    toast(r.message || 'Upload received. Analysis is starting.');
    startAnalysis(r.thesis.id);
  }) },
    h('label', {}, 'Thesis title', title),
    dropZone,
    h('div', { class: 'file-guidance' }, h('h3', {}, 'What we check'), h('p', { class: 'muted' }, 'DOCX allows checks for margins, spacing and page numbers. PDF checks text, fonts and paper size.')),
    h('button', { class: 'btn', type: 'submit' }, 'Upload and analyse'));
  mount(h('div', { class: 'page-heading' }, h('div', {}, h('p', { class: 'eyebrow' }, 'NEW SUBMISSION'), h('h2', {}, 'Upload a thesis draft'), h('p', { class: 'muted' }, 'Submit your document for formatting checks and formative feedback.'))),
    h('div', { class: 'upload-layout' }, f, h('aside', { class: 'panel upload-aside' }, h('h3', {}, 'Before you upload'), h('ul', {}, h('li', {}, 'Use a readable, unencrypted DOCX or PDF.'), h('li', {}, 'Keep your original file; reports do not modify it.'), h('li', {}, 'AI-generated feedback is formative and should be reviewed with your supervisor.')))));
}
async function reportView(id) {
  const [r, st] = await Promise.all([API.get('/api/student/reports?thesis_id=' + id), API.get(`/api/student/theses/${id}/status`)]);
  const counts = Object.fromEntries(['low', 'high'].map((level) => [level, r.feedback.filter((i) => i.level === level).length]));
  const search = h('input', { type: 'search', placeholder: 'Search feedback', 'aria-label': 'Search feedback' });
  const level = h('select', { 'aria-label': 'Filter by priority' }, ['all', 'low', 'high'].map((x) => h('option', { value: x }, x === 'all' ? 'All priorities' : `${x[0].toUpperCase()}${x.slice(1)} priority`)));
  const out = h('div', { class: 'feedback-list' });
  const draw = () => {
    const query = search.value.toLowerCase(), selected = level.value;
    const items = r.feedback.filter((i) => (selected === 'all' || i.level === selected) && `${i.content} ${i.suggestion || ''} ${i.severity}`.toLowerCase().includes(query));
    replace(out, ...(items.length ? items.map((i) => itemCard(i, i.level === 'low' && !i.student_status
      ? btn('Accept fix', async () => { await API.post(`/api/student/theses/${id}/accept-fixes`, { item_ids: [i.id] }); await reportView(id); }, 'small ghost')
      : null)) : [empty('No feedback matches these filters.')]));
  };
  search.addEventListener('input', draw); level.addEventListener('change', draw); draw();
  mount(h('div', { class: 'page-heading' }, h('div', {}, h('p', { class: 'eyebrow' }, 'THESIS REPORT'), h('h2', {}, 'Feedback and findings'), h('p', { class: 'muted' }, 'Review each finding, accept safe formatting fixes and discuss research feedback with your supervisor.')),
    h('a', { class: 'btn ghost', href: '#/' }, '← My theses')),
    st.analysis_note && h('p', { class: 'note' }, st.analysis_note),
    h('div', { class: 'stats compact-stats' }, stat('Formatting', counts.low, '≡'), stat('Research quality', counts.high, '✦')),
    h('div', { class: 'section-heading' }, h('div', { class: 'row' },
      btn('Export PDF', () => download(`/api/student/theses/${id}/export/pdf`, `thesis_${id}_feedback.pdf`), 'ghost'),
      st.status === 'analyzed' && state.user.role === 'student' && r.feedback.some((i) => i.level === 'low' && !i.student_status) &&       btn('Accept all format fixes', async () => { const result = await API.post(`/api/student/theses/${id}/accept-fixes`, {}); toast(result.message); await reportView(id); }, 'ok'),
      btn('Send to supervisor', async () => { await API.post(`/api/student/theses/${id}/submit-for-review`); toast('Sent to your supervisor.'); }, 'ghost')),
      h('div', { class: 'row filter-controls' }, level, search)), out);
}
async function studentProgress() {
  const { chains } = await API.get('/api/student/progress');
  mount(h('div', { class: 'page-heading' }, h('div', {}, h('p', { class: 'eyebrow' }, 'REVISION HISTORY'), h('h2', {}, 'My progress'), h('p', { class: 'muted' }, 'See how feedback and compliance checks change across submitted versions.'))), chainsView(chains));
}

/* ---------- supervisor ---------- */
async function supDash() {
  const d = await API.get('/api/supervisor/dashboard');
  const pending = d.feedback.filter((i) => !['accepted', 'rejected'].includes(i.status)).length;
  const summary = h('div', { class: 'stats compact-stats' }, stat('Assigned theses', d.theses.length, '≡'),
    stat('Students', d.students.length, '♙'), stat('Awaiting review', pending, '…'));
  if (!d.theses.length) return mount(h('div', { class: 'page-heading' }, h('div', {}, h('p', { class: 'eyebrow' }, 'SUPERVISOR WORKSPACE'), h('h2', {}, 'Review dashboard'), h('p', { class: 'muted' }, 'Review feedback and support students through thesis revisions.'))),
    summary, empty('No theses yet. Students appear here once an administrator assigns them to you.'));
  let cur = d.theses[0].id;
  let picked = new Set();
  const out = h('div', { class: 'review-columns' }), search = h('input', { type: 'search', placeholder: 'Search feedback text', 'aria-label': 'Search feedback text' });
  const sel = h('select', { 'aria-label': 'Select a thesis', onchange: () => { cur = +sel.value; picked = new Set(); draw(); } },
    d.theses.map((t) => h('option', { value: t.id }, `${t.title}, version ${t.version}${t.review_requested_at ? ' (review requested)' : ''}`)));
  const review = guard(async (i, action, extra = {}) => {
    Object.assign(i, (await API.post('/api/supervisor/feedback/' + i.id, { action, ...extra })).feedback); toast('Saved.'); draw();
  });
  function draw() {
    const query = search.value.toLowerCase();
    const items = d.feedback.filter((i) => i.thesis_id === cur && (!query || `${i.content} ${i.suggestion || ''} ${i.status}`.toLowerCase().includes(query)));
    const low = items.filter((i) => i.level === 'low'), high = items.filter((i) => i.level === 'high');
    const bulk = btn('Accept selected', async () => {
      if (!picked.size) return toast('Tick at least one item first.', true);
      const count = picked.size;
      await API.post('/api/supervisor/feedback/bulk', { item_ids: [...picked], action: 'accept' });
      low.forEach((i) => picked.has(i.id) && (i.status = 'accepted')); picked.clear(); toast(count + ' items accepted.'); draw();
    }, 'ok small');
    const selectAll = h('input', {
      type: 'checkbox',
      'aria-label': 'Select all formatting and citation items',
      disabled: low.length === 0,
      checked: low.length > 0 && low.every((i) => picked.has(i.id)),
      onchange: (e) => {
        low.forEach((i) => e.target.checked ? picked.add(i.id) : picked.delete(i.id));
        draw();
      }
    });
    selectAll.indeterminate = low.some((i) => picked.has(i.id)) && !low.every((i) => picked.has(i.id));
    const lowCards = low.map((i) => itemCard(i, [h('label', { class: 'tick' }, h('input', { type: 'checkbox', checked: picked.has(i.id), onchange: (e) => { e.target.checked ? picked.add(i.id) : picked.delete(i.id); draw(); } }), 'Select'),
      btn('Accept', () => review(i, 'accept'), 'ok small'), btn('Reject', () => review(i, 'reject'), 'ghost small')]));
    const highCards = high.map((i) => {
      const ta = h('textarea', { rows: 2, placeholder: 'Comment for the student', 'aria-label': 'Comment for the student' }, i.supervisor_comment || '');
      return itemCard(i, [ta, btn('Accept', () => review(i, 'accept', { comment: ta.value }), 'ok small'), btn('Save comment', () => review(i, 'comment', { comment: ta.value }), 'small'),
        btn('Edit suggestion', () => { const suggestion = prompt('Edited suggestion', i.suggestion || ''); return suggestion && suggestion.trim() ? review(i, 'edit', { suggestion }) : null; }, 'ghost small'),
        btn('Reject', () => review(i, 'reject', { comment: ta.value }), 'ghost small')]);
    });
    replace(out,
      h('section', { class: 'review-group' }, h('div', { class: 'section-heading' }, h('h3', {}, `Formatting and citation (${low.length})`), h('label', { class: 'tick' }, selectAll, 'Select all'), bulk), ...(lowCards.length ? lowCards : [empty('No matching low-priority feedback.')]),
        picked.size > 0 && h('p', { class: 'muted selection-note' }, `${picked.size} item${picked.size === 1 ? '' : 's'} selected`)),
      h('section', { class: 'review-group' }, h('h3', {}, `Research quality (${high.length})`), ...(highCards.length ? highCards : [empty('No matching high-priority feedback.')]))
    );
  }
  search.addEventListener('input', () => { picked.clear(); draw(); });
  mount(h('div', { class: 'page-heading' }, h('div', {}, h('p', { class: 'eyebrow' }, 'SUPERVISOR WORKSPACE'), h('h2', {}, 'Review dashboard'), h('p', { class: 'muted' }, 'Review feedback, add context and help students prioritise their next revision.'))),
    summary,
    h('div', { class: 'panel review-toolbar' }, h('label', {}, 'Thesis', sel), h('label', {}, 'Find feedback', search)), out);
  draw();
}
async function history() {
  const { feedback } = await API.get('/api/supervisor/feedback/history');
  const q = h('input', { type: 'search', placeholder: 'Search reviewed feedback', 'aria-label': 'Search reviewed feedback' }), out = h('div');
  const draw = () => {
    const found = feedback.filter((i) => `${i.content} ${i.suggestion || ''} ${i.status}`.toLowerCase().includes(q.value.toLowerCase()));
    replace(out, ...(found.length ? found.map((i) => itemCard(i)) : [empty('No reviewed feedback matches your search.')]));
  };
  q.addEventListener('input', draw);
  mount(h('div', { class: 'page-heading' }, h('div', {}, h('p', { class: 'eyebrow' }, 'SUPERVISOR WORKSPACE'), h('h2', {}, 'Feedback history'), h('p', { class: 'muted' }, 'Find comments and decisions previously made on student drafts.'))), h('div', { class: 'bar search-bar' }, q), out); draw();
}
async function supProgress() {
  const { students } = await API.get('/api/supervisor/progress');
  mount(h('div', { class: 'page-heading' }, h('div', {}, h('p', { class: 'eyebrow' }, 'SUPERVISOR WORKSPACE'), h('h2', {}, 'Student progress'), h('p', { class: 'muted' }, 'Follow revision cycles for students assigned to you.'))),
    students.length ? h('div', { class: 'student-progress-grid' }, students.map((s) => h('section', { class: 'panel' }, h('h3', {}, s.name), chainsView(s.chains)))) : empty('No assigned students yet.'));
}

/* ---------- admin ---------- */
const supSelect = (u, sups) => {
  const s = h('select', { 'aria-label': `Assigned supervisor for ${u.name}`, onchange: guard(async () => { await API.put(`/api/admin/students/${u.id}/supervisor`, { supervisor_id: s.value ? +s.value : null }); toast('Supervisor updated.'); }) },
    h('option', { value: '' }, 'No supervisor'), sups.map((x) => h('option', { value: x.id, selected: x.id === u.supervisor_id }, x.name)));
  return s;
};
async function adminUsers() {
  const { users } = await API.get('/api/admin/users');
  const sups = users.filter((u) => u.role === 'supervisor');
  const q = h('input', { type: 'search', placeholder: 'Search by name, email or role', 'aria-label': 'Search users' }), out = h('div');
  const f = h('form', { class: 'panel', onsubmit: guard(async (e) => {
    e.preventDefault(); const d = Object.fromEntries(new FormData(f));
    await API.post('/api/admin/users', d); toast('Account created.'); render();
  }) }, h('h3', {}, 'Create a supervisor or admin account'), field('name', 'Full name'), field('email', 'Email', 'email'), field('password', 'Temporary password', 'password'),
  h('label', {}, 'Role', h('select', { name: 'role' }, ['supervisor', 'admin'].map((r) => h('option', { value: r }, r)))), h('button', { class: 'btn' }, 'Create account'));
  const draw = () => {
    const found = users.filter((u) => `${u.name} ${u.email} ${u.role}`.toLowerCase().includes(q.value.toLowerCase()));
    replace(out, found.length ? table(['Name', 'Email', 'Role', 'Supervisor', 'Account'], found.map((u) => [u.name, u.email, badge(u.role), u.role === 'student' ? supSelect(u, sups) : 'not applicable', resetBtn(u)])) : empty('No users match your search.'));
  };
  q.addEventListener('input', draw); draw();
  mount(h('div', { class: 'page-heading' }, h('div', {}, h('p', { class: 'eyebrow' }, 'ADMINISTRATION'), h('h2', {}, 'Users and access'), h('p', { class: 'muted' }, `${users.length} accounts · assign supervisors and manage staff access.`))),
    h('div', { class: 'stats compact-stats' }, stat('Students', users.filter((u) => u.role === 'student').length, '♙'),
      stat('Supervisors', sups.length, '✎'), stat('Administrators', users.filter((u) => u.role === 'admin').length, '⚙')),
    h('div', { class: 'admin-layout' }, h('section', { class: 'panel admin-users' }, h('div', { class: 'section-heading' }, h('h3', {}, 'Accounts'), q), out), h('section', { class: 'admin-create' }, f)));
}
async function usage() {
  const m = await API.get('/api/admin/reports/usage'), rows = [];
  (function walk(p, n) { if (n && typeof n === 'object') Object.entries(n).forEach(([k, v]) => walk(p ? p + ', ' + k : k, v)); else rows.push([p, n ?? 'none']); })('', m);
  mount(h('div', { class: 'page-heading' }, h('div', {}, h('p', { class: 'eyebrow' }, 'ADMINISTRATION'), h('h2', {}, 'Usage report'), h('p', { class: 'muted' }, 'Activity and analysis totals for the system.')),
    h('a', { class: 'btn', href: '/api/admin/reports/usage?format=csv' }, 'Download CSV')), h('section', { class: 'panel' }, table(['Measure', 'Value'], rows)));
}

const resetBtn = (u) => btn('Reset password', async () => {
  if (!confirm(`Reset the password for ${u.name}? They must choose a new one at their next login.`)) return;
  const r = await API.post(`/api/admin/users/${u.id}/reset-password`);
  mount(h('h2', {}, 'Temporary password'), h('p', { class: 'note' }, `Give this to ${u.name} securely. It is shown only once: `, h('b', {}, r.temporary_password)), btn('Back to users', adminUsers));
}, 'small ghost');
async function rulesView() {
  const { rules } = await API.get('/api/admin/rules');
  const query = h('input', { type: 'search', placeholder: 'Search rules', 'aria-label': 'Search compliance rules' }), out = h('div', { class: 'rules-list' });
  const filtered = () => rules.filter((r) => `${r.rule_key} ${r.description} ${r.category}`.toLowerCase().includes(query.value.toLowerCase()));
  const draw = () => {
    replace(out, ...filtered().map((r) => {
    const sev = h('select', {}, ['low', 'medium', 'high'].map((s) => h('option', { value: s, selected: s === r.severity }, s)));
    const act = h('input', { type: 'checkbox', checked: r.active });
    const desc = h('textarea', { rows: 2 }, r.description), sug = h('textarea', { rows: 3 }, r.suggestion);
    return h('details', { class: 'panel rule' },
      h('summary', {}, h('b', {}, r.rule_key), ' ', r.description.slice(0, 90), ' ', badge(r.severity), !r.active && badge('inactive')),
      h('div', { class: 'edit' }, h('p', { class: 'muted' }, `${r.category}. ${r.manual_reference || ''}`),
        h('label', {}, 'Description', desc), h('label', {}, 'Suggested fix shown to students', sug),
        h('div', { class: 'row' }, h('label', { class: 'tick' }, act, 'Active'), h('label', {}, 'Severity', sev)),
        btn('Save rule', async () => { await API.put('/api/admin/rules/' + r.rule_key, { description: desc.value, suggestion: sug.value, severity: sev.value, active: act.checked }); toast('Rule saved.'); })));
    }));
    if (!out.childElementCount) out.append(empty('No rules match your search.'));
  };
  query.addEventListener('input', draw); draw();
  mount(h('div', { class: 'page-heading' }, h('div', {}, h('p', { class: 'eyebrow' }, 'ADMINISTRATION'), h('h2', {}, 'Compliance rules'), h('p', { class: 'muted' }, 'Changes apply to future analyses. Inactive rules are skipped.'))),
    h('div', { class: 'bar search-bar' }, query), out);
}
async function logsView() {
  const { logs } = await API.get('/api/admin/logs?limit=300');
  const q = h('input', { type: 'search', placeholder: 'Filter by action, details or user id' }), out = h('div');
  const draw = () => { const t = q.value.toLowerCase(); replace(out, table(['Time', 'User', 'Action', 'Details', 'IP address'],
    logs.filter((l) => !t || [l.action, l.details, l.user_id].join(' ').toLowerCase().includes(t))
      .map((l) => [new Date(l.created_at).toLocaleString('en-GB'), l.user_id ?? 'none', l.action, l.details || '', l.ip_address || '']))); };
  q.addEventListener('input', draw); mount(h('div', { class: 'page-heading' }, h('div', {}, h('p', { class: 'eyebrow' }, 'ADMINISTRATION'), h('h2', {}, 'Audit log'), h('p', { class: 'muted' }, 'Review recent account and system activity.'))), h('div', { class: 'bar search-bar' }, q), out); draw();
}
function accountView() {
  const forced = state.user.must_change_password;
  const f = h('form', { class: 'panel auth', onsubmit: guard(async (e) => {
    e.preventDefault(); const d = Object.fromEntries(new FormData(f));
    await API.post('/api/auth/change-password', { current_password: d.current, new_password: d.next });
    state.user.must_change_password = false; toast('Password changed.'); location.hash = '#/'; render();
  }) }, h('h2', {}, forced ? 'Choose a new password' : 'Change password'),
  forced && h('p', { class: 'note' }, 'An administrator reset your password. Set a new one to continue.'),
  field('current', forced ? 'Temporary password' : 'Current password', 'password'), field('next', 'New password', 'password'),
  h('p', { class: 'muted' }, 'At least 8 characters, with letters and numbers.'), h('button', { class: 'btn', type: 'submit' }, 'Change password'));
  mount(h('div', { class: 'page-heading' }, h('div', {}, h('p', { class: 'eyebrow' }, 'ACCOUNT SETTINGS'), h('h2', {}, forced ? 'Update your password' : 'Account security'), h('p', { class: 'muted' }, 'Keep your account secure with a strong password.'))), f);
}

/* ---------- router ---------- */
const ROUTES = {
  student: { nav: [['', 'My theses'], ['upload', 'Upload draft'], ['progress', 'Progress']], views: { '': studentHome, upload: uploadView, analysis: analysisView, report: reportView, progress: studentProgress } },
  supervisor: { nav: [['', 'Dashboard'], ['history', 'History'], ['progress', 'Progress']], views: { '': supDash, history, progress: supProgress } },
  admin: { nav: [['', 'Users'], ['rules', 'Rules'], ['logs', 'Audit log'], ['usage', 'Usage report']], views: { '': adminUsers, rules: rulesView, logs: logsView, usage } },
};
const here = () => location.hash.replace(/^#\/?/, '').split('/');
function drawHeader() {
  const u = state.user, cur = here()[0];
  const brand = h('a', { class: 'brand', href: '#/' }, h('span', { class: 'brand-icon', 'aria-hidden': 'true' }, 'T'), h('span', {}, 'Thesis', h('em', {}, 'Support')));
  const nav = u && !u.must_change_password && h('nav', { 'aria-label': 'Main navigation' }, [...ROUTES[u.role].nav, ['account', 'Account']].map(([p, l]) => h('a', { href: '#/' + p, 'aria-current': p === cur ? 'page' : null }, l)));
  const user = u && h('div', { class: 'who' }, h('span', { class: 'user-avatar', 'aria-hidden': 'true' }, u.name.split(/\s+/).map((part) => part[0]).slice(0, 2).join('').toUpperCase()), h('span', { class: 'user-label' }, h('b', {}, u.name), h('small', {}, u.role)),
    btn('Log out', async () => { await API.post('/api/auth/logout'); await API.csrf(); state.user = null; location.hash = '#/'; render(); }, 'ghost small'));
  replace($('#top'), brand, nav, user);
}
async function render() {
  drawHeader();
  if (!state.user) return mount(loginView());
  const [page, arg] = here(), forced = state.user.must_change_password;
  const view = forced || page === 'account' ? accountView : ROUTES[state.user.role].views[page || ''];
  mount(h('div', { class: 'loading-state', role: 'status' }, h('span', { class: 'spinner' }), 'Loading your workspace…'));
  try { view ? await view(arg) : mount(empty('That page does not exist.')); } catch (e) {
    if (e.status === 401) { state.user = null; return render(); }
    mount(h('p', { class: 'note' }, e.message));
  }
}
addEventListener('hashchange', render);
(async () => { try { await API.csrf(); state.user = (await API.get('/api/auth/me')).user; } catch (e) { /* not logged in */ } render(); })();
