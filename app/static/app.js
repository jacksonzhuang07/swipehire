let ME = null, deck = [], empDeck = [], empJob = null;
const $ = id => document.getElementById(id);
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(p, o) { const r = await fetch(p, o); let j = null; try { j = await r.json(); } catch (e) {} return { status: r.status, json: j }; }
async function apiJ(p, o) { const r = await api(p, o); return r.json; }
const POST = (p, body) => api(p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });

function initials(name) { return (name || '?').split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase(); }
function avatarHTML(person, cls) {
  if (person.photo_url) return `<img class="avatar ${cls || ''}" src="${esc(person.photo_url)}" alt="">`;
  if (person.logo_url) return `<img class="avatar ${cls || ''}" src="${esc(person.logo_url)}" alt="">`;
  return `<span class="avatar initials ${cls || ''}">${esc(initials(person.name || person.company || '?'))}</span>`;
}

// ---------- auth ----------
function authTab(t) {
  for (const k of ['login', 'signup', 'code']) {
    $('tab-' + k).classList.toggle('active', t === k);
    $('auth-' + k).classList.toggle('hidden', t !== k);
  }
  $('auth-error').textContent = '';
}
function resetCodeFlow() {
  $('code-step1').classList.remove('hidden'); $('code-step2').classList.add('hidden');
  $('code-demo').classList.add('hidden'); $('code-msg').textContent = ''; $('code-value').value = '';
}
async function requestCode() {
  const email = $('code-email').value.trim();
  if (!email) { $('code-msg').textContent = 'Enter your email first.'; return; }
  $('code-msg').textContent = 'Sending…';
  const r = await POST('/api/auth/code/request', { email: email });
  if (r.status === 429) { $('code-msg').textContent = (r.json && r.json.error) || 'Too many requests — wait a few minutes.'; return; }
  if (r.status !== 200) { $('code-msg').textContent = (r.json && r.json.error) || 'Could not send a code.'; return; }
  $('code-step1').classList.add('hidden'); $('code-step2').classList.remove('hidden');
  if (r.json.demo_code) {
    const box = $('code-demo');
    box.innerHTML = `🧪 <b>Demo mode:</b> no email service is connected yet, so no email was sent. Your code is <b class="demo-code">${esc(r.json.demo_code)}</b>`;
    box.classList.remove('hidden');
    $('code-msg').textContent = 'Demo mode — copy the code from the yellow box above.';
  } else {
    $('code-demo').classList.add('hidden');
    $('code-msg').textContent = r.json.message || 'Check your email for the code.';
  }
  $('code-value').focus();
}
async function verifyCode() {
  const r = await POST('/api/auth/code/verify', { email: $('code-email').value.trim(), code: $('code-value').value.trim(), role: $('code-role').value });
  if (r.status !== 200 && r.status !== 201) { $('code-msg').textContent = (r.json && r.json.error) || 'That code did not work — codes expire in 10 minutes and work once.'; return; }
  boot(r.json.user);
}
async function doLogin() {
  const r = await POST('/api/auth/login', { email: $('li-email').value, password: $('li-pass').value });
  if (r.status !== 200) { $('auth-error').textContent = (r.json && r.json.error) || 'Login failed'; return; }
  boot(r.json.user);
}
async function demoLogin(email) {
  if (email === jacksonEmail || !email) {
    const list = await apiJ('/api/demo-accounts');
    const j = (list || []).find(a => a.role === 'candidate' && a.email !== 'demo@swipehire.app');
    if (j) email = j.email;
  }
  const r = await POST('/api/auth/login', { email: email, password: 'demo' });
  if (r.status !== 200) { $('auth-error').textContent = 'Demo login failed'; return; }
  boot(r.json.user);
}
async function doSignup() {
  const r = await POST('/api/auth/signup', { email: $('su-email').value, password: $('su-pass').value, role: $('su-role').value, name: $('su-name').value, company: $('su-company').value });
  if (r.status !== 201) { $('auth-error').textContent = (r.json && r.json.error) || 'Signup failed'; return; }
  boot(r.json.user);
}
async function doLogout() { await POST('/api/auth/logout'); ME = null; location.reload(); }

function boot(user) {
  ME = user;
  $('auth-screen').classList.add('hidden'); $('app-screen').classList.remove('hidden');
  const person = user.role === 'candidate' ? user.candidate : user.employer;
  $('user-box').innerHTML = `${avatarHTML(person || {}, 'small')} <span>${esc(user.name || user.email)} · ${esc(user.role)}</span> <button onclick="doLogout()">Log out</button>`;
  const nav = user.role === 'candidate'
    ? `<button data-view="discovery" onclick="showView('discovery')"><span class="nav-ic">🔥</span><span>For You</span></button><button data-view="applications" onclick="showView('applications')"><span class="nav-ic">📨</span><span>Applications</span></button><button data-view="documents" onclick="showView('documents')"><span class="nav-ic">🗂️</span><span>Documents</span></button><button data-view="profile" onclick="showView('profile')"><span class="nav-ic">👤</span><span>Profile</span></button><button data-view="matches" onclick="showView('matches')"><span class="nav-ic">💬</span><span>Matches</span></button>`
    : `<button data-view="employer" onclick="showView('employer')"><span class="nav-ic">🔥</span><span>Employer</span></button><button data-view="matches" onclick="showView('matches')"><span class="nav-ic">💬</span><span>Matches</span></button>`;
  $('main-nav').innerHTML = nav;
  showView(user.role === 'candidate' ? 'discovery' : 'employer');
}
function showView(v) {
  document.querySelectorAll('.view').forEach(e => e.classList.add('hidden'));
  $('view-' + v).classList.remove('hidden');
  document.querySelectorAll('#main-nav button').forEach(b => b.classList.toggle('active', b.dataset.view === v));
  if (v === 'discovery') loadDeck();
  if (v === 'applications') loadApps();
  if (v === 'documents') loadDocuments();
  if (v === 'profile') loadProfile();
  if (v === 'employer') loadEmployer();
  if (v === 'matches') loadMatches();
}

// ---------- candidate discovery ----------
function whyHTML(j) { return `<div class="why"><b>Why this job</b><ul>${(j.why || []).map(w => `<li>${esc(w)}</li>`).join('')}</ul></div>`; }
function badgesHTML(scoreLabel, feedScore, isStretch) {
  return `<div class="hero-badges"><span class="score-pill">${scoreLabel}</span><span class="feed-pill">For You ${feedScore}</span>${isStretch ? '<span class="stretch-pill">Stretch pick</span>' : ''}</div>`;
}
function cardHTML(j) {
  const initial = esc((j.company || '?').trim().charAt(0).toUpperCase() || '?');
  return `<div class="card job-card" id="top-card"><div class="stamp stamp-like">Like</div><div class="stamp stamp-nope">Nope</div><div class="card-hero"><div class="segments"><i></i></div>${badgesHTML(j.score + '% match', j.feed_score, j.is_stretch)}<div class="hero-center"><span class="hero-initial">${initial}</span></div><div class="hero-scrim"><h2>${esc(j.title)}</h2><p class="hero-sub">${esc(j.company)}</p></div></div><div class="card-body"><p class="meta-line"><span>📍 ${esc(j.location || 'Unspecified')}${j.remote ? ' · Remote' : ''}</span>${j.salary_text ? `<span class="salary">${esc(j.salary_text)}</span>` : ''}<span>Source: ${esc(j.source)}${j.source === 'seeded' ? ' (curated fallback pool)' : ''}</span></p><p class="desc">${esc((j.description || '').slice(0, 400))}</p><div class="chips">${(j.tags || []).map(t => `<span class="chip">${esc(t)}</span>`).join('')}</div>${whyHTML(j)}${j.url ? `<a class="apply-link" href="${esc(j.url)}" target="_blank" rel="noopener">View / apply on source ↗</a>` : ''}</div></div>`;
}
function peekHTML(j) {
  if (!j) return '';
  const initial = esc((j.company || '?').trim().charAt(0).toUpperCase() || '?');
  return `<div class="card peek" aria-hidden="true"><div class="card-hero"><div class="hero-center"><span class="hero-initial">${initial}</span></div><div class="hero-scrim"><h2>${esc(j.title)}</h2><p class="hero-sub">${esc(j.company)}</p></div></div></div>`;
}
async function loadDeck() { const r = await api('/api/discovery'); deck = r.json || []; $('pool-count').textContent = deck.length + ' jobs in your feed'; renderDeck(); }
function renderDeck() { $('deck').innerHTML = deck.length ? peekHTML(deck[1]) + cardHTML(deck[0]) : '<div class="card"><h2>No more jobs 🎉</h2><p>Rescan the boards or check back later.</p></div>'; attachDrag('top-card', swipeCurrent); }
async function rescan() { $('scan-result').textContent = 'Scanning…'; const r = await POST('/api/jobs/scan'); const rep = r.json || {}; $('scan-result').textContent = Object.entries(rep).filter(([k, v]) => v && typeof v === 'object').map(([k, v]) => `${k}: +${v.new} new (${v.status})`).join(' · ') + (rep.pool_total ? ` · Pool total: ${rep.pool_total}` : ''); loadDeck(); }
async function swipeCurrent(dir) {
  if (!deck.length) return;
  const j = deck[0];
  const r = await POST('/api/swipe', { job_id: j.id, direction: dir });
  deck.shift();
  if (r.json && r.json.match) showMatch({ name: j.title, sub: j.company, otherInitial: (j.company || '?').charAt(0).toUpperCase(), otherPhoto: '' });
  else if (dir === 'right' && r.json && r.json.application) alert('Application: ' + r.json.application.status + ' (' + r.json.application.method + ') — with a tailored resume built from your Master Profile facts only. Gaps are listed, never invented.');
  renderDeck();
}

// ---------- match overlay ----------
function matchAvatarHTML(photo, initial) {
  return photo ? `<img class="match-avatar" src="${esc(photo)}" alt="">` : `<span class="match-avatar">${esc(initial || '?')}</span>`;
}
function showMatch(opts) {
  const person = ME ? (ME.candidate || ME.employer || {}) : {};
  const myPhoto = person.photo_url || person.logo_url || '';
  const myInitial = ((ME && (ME.name || ME.email)) || '?').charAt(0).toUpperCase();
  $('match-title').textContent = opts.name || '';
  $('match-sub').textContent = opts.sub || '';
  $('match-avatars').innerHTML = matchAvatarHTML(myPhoto, myInitial) + matchAvatarHTML(opts.otherPhoto || '', opts.otherInitial || '?');
  $('match-overlay').classList.remove('hidden');
}
function closeMatch(goMatches) {
  $('match-overlay').classList.add('hidden');
  if (goMatches) showView('matches');
}

// ---------- applications ----------
async function loadApps() {
  const apps = await apiJ('/api/applications') || [];
  $('apps').innerHTML = apps.length ? apps.map(a => `<div class="row"><div><b>${esc(a.title)}</b> @ ${esc(a.company)}<br><span class="meta">${esc(a.status)} · ${esc(a.method)} · ${esc(a.created_at || '')} ${a.tailored ? '<span class="stretch-pill">Tailored for this job</span>' : ''}</span><br><span class="meta">${esc(a.proof || '')}</span>${a.coverage && (a.coverage.missing || []).length ? `<br><span class="meta">Keyword gaps (left out, never invented): ${esc((a.coverage.missing || []).join(', '))}</span>` : ''}</div>${a.has_tailored ? `<button onclick="viewTailored(${a.id})">View tailored resume</button>` : ''}</div>`).join('') : '<p>No applications yet — swipe right on a job.</p>';
}
async function viewTailored(aid) {
  const t = await apiJ('/api/applications/' + aid + '/tailored-resume');
  openModal(`<h3>Tailored resume — ${esc(t.job_title || '')} @ ${esc(t.company || '')}</h3><p class="meta">Covered keywords: ${esc((t.coverage.covered || []).join(', ') || '—')}<br>Gaps (in the job, not in your profile — left out): ${esc((t.coverage.missing || []).join(', ') || 'none')}</p><pre class="resume-box">${esc(t.text)}</pre><a href="/api/applications/${aid}/tailored-resume?download=1"><button>⬇ Download .txt</button></a>`);
}

// ---------- documents / master profile ----------
async function addDocument() {
  const fd = new FormData();
  fd.append('doc_type', $('doc-type').value); fd.append('title', $('doc-title').value);
  const f = $('doc-file').files[0]; if (f) fd.append('file', f);
  fd.append('text', $('doc-text').value);
  const r = await fetch('/api/documents', { method: 'POST', body: fd }); const j = await r.json();
  if (r.status !== 201) { alert(j.error || 'Failed'); return; }
  $('doc-text').value = ''; $('doc-title').value = ''; loadDocuments();
}
async function deleteDocument(did) { await fetch('/api/documents/' + did, { method: 'DELETE' }); loadDocuments(); }
async function loadDocuments() {
  const docs = await apiJ('/api/documents') || [];
  $('doc-list').innerHTML = docs.length ? docs.map(d => `<div class="row"><div><b>${esc(d.title || d.filename || 'Document')}</b> <span class="chip">${esc(d.doc_type)}</span><br><span class="meta">Extracted — skills: ${esc(((d.facts || {}).skills || []).join(', ') || '—')} · experience entries: ${((d.facts || {}).experience || []).length} · certifications: ${esc(((d.facts || {}).certifications || []).join(', ') || '—')} · references: ${esc(((d.facts || {}).references || []).map(r => r.name).join(', ') || '—')}</span></div><button onclick="deleteDocument(${d.id})">Remove</button></div>`).join('') : '<p>No documents yet.</p>';
  const mp = await apiJ('/api/master-profile') || {};
  const src = s => (s || []).map(x => `<span class="chip">${esc(x.doc_title || 'profile')}</span>`).join(' ');
  $('master-profile').innerHTML = `<div class="card">
    <p><b>Skills</b></p>${Object.entries(mp.skills || {}).map(([s, v]) => `<div class="fact"><span class="chip skill">${esc(s)}</span> from ${src(v.sources)}</div>`).join('') || '<p class="meta">No skills yet — add a document.</p>'}
    <p><b>Experience</b></p>${(mp.experience || []).map(e => `<div class="fact">${esc([e.title, e.company, e.period].filter(Boolean).join(' | '))} — from ${src(e.sources)}</div>`).join('') || '<p class="meta">—</p>'}
    <p><b>Education</b></p>${(mp.education || []).map(e => `<div class="fact">${esc(e)}</div>`).join('') || '<p class="meta">—</p>'}
    <p><b>Certifications</b></p>${(mp.certifications || []).map(c => `<div class="fact">${esc(c)}</div>`).join('') || '<p class="meta">—</p>'}
    <p><b>References</b></p>${(mp.references || []).map(r => `<div class="fact">${esc(r.name)}${r.relationship ? ' — ' + esc(r.relationship) : ''} — from ${src(r.sources)}</div>`).join('') || '<p class="meta">—</p>'}
    <p class="meta">Years of experience (max across documents): ${mp.years_exp || 0} · Links: ${esc((mp.links || []).join(', ') || '—')}</p></div>`;
  const cr = await apiJ('/api/compiled-resume');
  $('compiled-resume').textContent = (cr && cr.text) || '';
}
function downloadCompiled(fmt) { window.open('/api/compiled-resume/download?format=' + fmt, '_blank'); }

// ---------- profile ----------
async function loadProfile() {
  const me = await apiJ('/api/auth/me'); ME = me; const c = me.candidate || {};
  $('profile-card').innerHTML = `<div class="card"><div class="profile-head">${avatarHTML(c, 'large')}<div><h3>${esc(c.name || '')}</h3><p>${esc(c.title || '')} · ${esc(c.email || '')}</p><p>${esc(c.summary || '')}</p><div>${(c.skills || []).map(s => `<span class="chip">${esc(s)}</span>`).join('')}</div></div></div></div>`;
  $('pref-titles').value = (c.target_titles || []).join(', '); $('pref-locs').value = (c.locations || []).join(', ');
  $('pref-salary').value = c.min_salary || ''; $('pref-remote').checked = !!c.remote_only;
}
async function uploadPhoto() {
  const f = $('photo-file').files[0]; if (!f) { alert('Pick an image first'); return; }
  const fd = new FormData(); fd.append('file', f);
  const r = await fetch('/api/profile/photo', { method: 'POST', body: fd }); const j = await r.json();
  if (r.status !== 200) { alert(j.error || 'Upload failed'); return; }
  loadProfile();
}
async function uploadResume() {
  const fd = new FormData(); const f = $('resume-file').files[0]; if (f) fd.append('file', f); fd.append('text', $('resume-text').value);
  const r = await fetch('/api/resume', { method: 'POST', body: fd }); const j = await r.json();
  if (r.status !== 201) { alert(j.error || 'Parse failed'); return; }
  loadProfile();
}
async function savePrefs() {
  const split = s => s.split(',').map(x => x.trim()).filter(Boolean);
  await POST('/api/candidate/preferences', { target_titles: split($('pref-titles').value), locations: split($('pref-locs').value), min_salary: parseInt($('pref-salary').value || '0'), remote_only: $('pref-remote').checked });
  alert('Preferences saved — your For You feed re-ranks immediately.');
}

// ---------- employer ----------
async function loadEmployer() {
  const me = await apiJ('/api/auth/me'); ME = me; const e = me.employer || {};
  $('emp-profile').innerHTML = `<div class="card"><div class="profile-head">${avatarHTML(e, 'large')}<div><h3>${esc(e.company || '')}</h3><p>${esc(e.name || '')} · ${esc(e.email || '')}</p></div></div></div>`;
  const jobs = await apiJ('/api/employer/jobs') || [];
  $('my-jobs').innerHTML = '<h4>My jobs</h4>' + (jobs.length ? jobs.map(j => `<p>• ${esc(j.title)} — ${esc(j.location || '')} (${esc(j.source)})</p>`).join('') : '<p class="meta">None yet.</p>');
  $('emp-job-select').innerHTML = jobs.map(j => `<option value="${j.id}">${esc(j.title)}</option>`).join('');
  loadEmpDeck();
}
async function postJob() {
  const tags = $('ej-tags').value.split(',').map(x => x.trim()).filter(Boolean);
  const r = await POST('/api/employer/jobs', { title: $('ej-title').value, location: $('ej-loc').value, description: $('ej-desc').value, tags: tags, remote: $('ej-remote').checked });
  if (r.status !== 201) { alert((r.json && r.json.error) || 'Failed'); return; }
  loadEmployer();
}
function empCardHTML(c, job) {
  const hero = c.photo_url
    ? `<div class="card-hero photo"><img class="hero-img" src="${esc(c.photo_url)}" alt=""><div class="segments"><i></i></div>${badgesHTML(c.score + '% fit', c.feed_score, false)}<div class="hero-scrim"><h2>${esc(c.name)}</h2><p class="hero-sub">${esc(c.title || '')} · ${c.years_exp || 0} yrs</p></div></div>`
    : `<div class="card-hero"><div class="segments"><i></i></div>${badgesHTML(c.score + '% fit', c.feed_score, false)}<div class="hero-center"><span class="hero-initial">${esc(initials(c.name || '?'))}</span></div><div class="hero-scrim"><h2>${esc(c.name)}</h2><p class="hero-sub">${esc(c.title || '')} · ${c.years_exp || 0} yrs</p></div></div>`;
  return `<div class="card cand-card" id="emp-top-card"><div class="stamp stamp-like">Like</div><div class="stamp stamp-nope">Nope</div>${hero}<div class="card-body"><p class="desc">${esc(c.summary || '')}</p><div class="chips">${(c.skills || []).map(s => `<span class="chip">${esc(s)}</span>`).join('')}</div><div class="why"><b>Why this candidate</b><ul>${(c.why || []).map(w => `<li>${esc(w)}</li>`).join('')}</ul></div></div></div>`;
}
function empPeekHTML(c) {
  if (!c) return '';
  const hero = c.photo_url
    ? `<div class="card-hero photo"><img class="hero-img" src="${esc(c.photo_url)}" alt=""><div class="hero-scrim"><h2>${esc(c.name)}</h2></div></div>`
    : `<div class="card-hero"><div class="hero-center"><span class="hero-initial">${esc(initials(c.name || '?'))}</span></div><div class="hero-scrim"><h2>${esc(c.name)}</h2></div></div>`;
  return `<div class="card peek" aria-hidden="true">${hero}</div>`;
}
async function loadEmpDeck() {
  const sel = $('emp-job-select').value;
  const data = await apiJ('/api/employer/candidates' + (sel ? '?job_id=' + sel : '')) || {};
  empJob = data.job; empDeck = data.candidates || [];
  $('emp-deck').innerHTML = empDeck.length && empJob ? empPeekHTML(empDeck[1]) + empCardHTML(empDeck[0], empJob) : '<div class="card"><h2>No more candidates 🎉</h2><p>Post a job first if you have none.</p></div>';
  attachDrag('emp-top-card', empSwipe);
}
async function empSwipe(dir) {
  if (!empDeck.length || !empJob) return;
  const c = empDeck[0];
  const r = await POST('/api/employer/swipe', { job_id: empJob.id, candidate_id: c.id, direction: dir });
  empDeck.shift();
  if (r.json && r.json.match) showMatch({ name: c.name, sub: empJob.title + ' @ ' + (empJob.company || ''), otherPhoto: c.photo_url || '', otherInitial: (c.name || '?').charAt(0).toUpperCase() });
  loadEmpDeckRender();
}
function loadEmpDeckRender() {
  $('emp-deck').innerHTML = empDeck.length && empJob ? empPeekHTML(empDeck[1]) + empCardHTML(empDeck[0], empJob) : '<div class="card"><h2>No more candidates 🎉</h2></div>';
  attachDrag('emp-top-card', empSwipe);
}
async function uploadLogo() {
  const f = $('logo-file').files[0]; if (!f) { alert('Pick an image first'); return; }
  const fd = new FormData(); fd.append('file', f);
  const r = await fetch('/api/employer/logo', { method: 'POST', body: fd }); const j = await r.json();
  if (r.status !== 200) { alert(j.error || 'Upload failed'); return; }
  loadEmployer();
}

// ---------- matches ----------
async function loadMatches() {
  const ms = await apiJ('/api/matches') || [];
  $('matches').innerHTML = ms.length ? ms.map(m => `<div class="row"><div class="profile-head">${m.candidate_photo_url ? `<img class="avatar" src="${esc(m.candidate_photo_url)}">` : `<span class="avatar initials">${esc(initials(m.candidate_name))}</span>`}<div><b>${esc(m.candidate_name)}</b> × <b>${esc(m.job_title)}</b> @ ${esc(m.company)}</div></div><div><input id="msg-${m.id}" placeholder="Say hi…"><button onclick="sendMsg(${m.id})">Send</button><div id="msgs-${m.id}"></div></div></div>`).join('') : '<p>No matches yet. A match needs a mutual right-swipe.</p>';
  for (const m of ms) { const msgs = await apiJ(`/api/matches/${m.id}/messages`) || []; const el = $('msgs-' + m.id); if (el) el.innerHTML = msgs.map(x => `<p class="meta">${esc(x.sender)}: ${esc(x.body)}</p>`).join(''); }
}
async function sendMsg(mid) { const v = $('msg-' + mid).value; if (!v) return; await POST(`/api/matches/${mid}/messages`, { body: v }); loadMatches(); }

// ---------- shared ----------
function attachDrag(elId, onSwipe) {
  const el = $(elId); if (!el) return;
  let sx = 0, dx = 0;
  const like = el.querySelector('.stamp-like'), nope = el.querySelector('.stamp-nope');
  const stamps = () => { if (like) like.style.opacity = dx > 8 ? Math.min(dx / 110, 1) : 0; if (nope) nope.style.opacity = dx < -8 ? Math.min(-dx / 110, 1) : 0; };
  const start = x => sx = x;
  const move = x => { dx = x - sx; el.style.transform = `translateX(${dx}px) rotate(${dx / 18}deg)`; stamps(); };
  const end = () => { if (dx > 110) onSwipe('right'); else if (dx < -110) onSwipe('left'); else { el.style.transform = ''; if (like) like.style.opacity = 0; if (nope) nope.style.opacity = 0; } dx = 0; };
  el.addEventListener('mousedown', e => start(e.clientX));
  window.addEventListener('mousemove', e => { if (sx) move(e.clientX); });
  window.addEventListener('mouseup', () => { if (sx) { end(); sx = 0; } });
  el.addEventListener('touchstart', e => start(e.touches[0].clientX), { passive: true });
  el.addEventListener('touchmove', e => move(e.touches[0].clientX), { passive: true });
  el.addEventListener('touchend', end);
}
function openModal(html) { $('modal-content').innerHTML = html; $('modal').classList.remove('hidden'); }
function closeModal() { $('modal').classList.add('hidden'); }
document.addEventListener('keydown', e => {
  if (!ME || ME.role !== 'candidate' || $('view-discovery').classList.contains('hidden')) return;
  if (e.key === 'ArrowRight') swipeCurrent('right');
  if (e.key === 'ArrowLeft') swipeCurrent('left');
});
(async () => { const r = await api('/api/auth/me'); if (r.status === 200) boot(r.json); })();
