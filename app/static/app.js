let ME = null, deck = [], empDeck = [], empJob = null, deckType = 'jobs', filterTimer = null;
const $ = id => document.getElementById(id);
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(p, o) { const r = await fetch(p, o); let j = null; try { j = await r.json(); } catch (e) {} return { status: r.status, json: j }; }
async function apiJ(p, o) { const r = await api(p, o); return r.json; }
const POST = (p, body) => api(p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });

function initials(name) { return (name || '?').split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase(); }
function avatarHTML(person, cls) {
  // v9/N1: a profile VIDEO plays when present; the picture is the fallback.
  if (person.video_url) return `<video class="avatar ${cls || ''}" src="${esc(person.video_url)}" autoplay muted loop playsinline></video>`;
  if (person.photo_url) return `<img class="avatar ${cls || ''}" src="${esc(person.photo_url)}" alt="">`;
  if (person.logo_url) return `<img class="avatar ${cls || ''}" src="${esc(person.logo_url)}" alt="">`;
  return `<span class="avatar initials ${cls || ''}">${esc(initials(person.name || person.company || '?'))}</span>`;
}
function heroMediaHTML(mediaUrl, mediaType, initial) {
  if (mediaUrl && mediaType === 'video') return `<video class="hero-img" src="${esc(mediaUrl)}" autoplay muted loop playsinline></video>`;
  if (mediaUrl) return `<img class="hero-img" src="${esc(mediaUrl)}" alt="">`;
  return `<div class="hero-center"><span class="hero-initial">${esc(initial || '?')}</span></div>`;
}
const MODE_LABEL = { remote: '🏠 Remote', hybrid: '🔀 Hybrid', in_person: '🏢 In person', unspecified: '' };
function modeLabel(j) { return MODE_LABEL[j.work_mode || 'unspecified'] || (j.remote ? '🏠 Remote' : ''); }

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
    ? `<button data-view="discovery" onclick="showView('discovery')"><span class="nav-ic">🔥</span><span>For You</span></button><button data-view="applications" onclick="showView('applications')"><span class="nav-ic">📨</span><span>Applications</span></button><button data-view="documents" onclick="showView('documents')"><span class="nav-ic">🗂️</span><span>Documents</span></button><button data-view="profile" onclick="showView('profile')"><span class="nav-ic">👤</span><span>Profile</span></button><button data-view="connections" onclick="showView('connections')"><span class="nav-ic">🔌</span><span>Connections</span></button><button data-view="matches" onclick="showView('matches')"><span class="nav-ic">💬</span><span>Matches</span></button>`
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
  if (v === 'connections') loadConnections();
}

// ---------- candidate discovery ----------
function whyHTML(j) { return `<div class="why"><b>Why this job</b><ul>${(j.why || []).map(w => `<li>${esc(w)}</li>`).join('')}</ul></div>`; }
function badgesHTML(scoreLabel, feedScore, isStretch) {
  return `<div class="hero-badges"><span class="score-pill">${scoreLabel}</span><span class="feed-pill">For You ${feedScore}</span>${isStretch ? '<span class="stretch-pill">Stretch pick</span>' : ''}</div>`;
}
function cardHTML(j) {
  const initial = esc((j.company || '?').trim().charAt(0).toUpperCase() || '?');
  const ml = modeLabel(j);
  return `<div class="card job-card" id="top-card"><div class="stamp stamp-like">Like</div><div class="stamp stamp-nope">Nope</div><div class="card-hero${j.media_url ? ' photo' : ''}"><div class="segments"><i></i></div>${badgesHTML(j.score + '% match', j.feed_score, j.is_stretch)}${heroMediaHTML(j.media_url, j.media_type, initial)}<div class="hero-scrim"><h2>${esc(j.title)}</h2><p class="hero-sub">${esc(j.company)}</p></div></div><div class="card-body"><p class="meta-line">${j.requires_login ? '<span class="lock-badge">🔒 Login required</span>' : ''}<span>📍 ${esc(j.location || 'Unspecified')}</span>${ml ? `<span class="chip">${ml}</span>` : ''}${j.salary_text ? `<span class="salary">${esc(j.salary_text)}</span>` : ''}<span>Source: ${esc(j.source)}${j.source === 'seeded' ? ' (curated fallback pool)' : ''}</span></p><p class="desc">${esc((j.description || '').slice(0, 400))}</p><div class="chips">${(j.tags || []).map(t => `<span class="chip">${esc(t)}</span>`).join('')}</div>${whyHTML(j)}${j.url ? `<a class="apply-link" href="${esc(j.url)}" target="_blank" rel="noopener">View / apply on source ↗</a>` : ''}</div></div>`;
}
function recruiterCardHTML(r) {
  const initial = esc((r.company || '?').trim().charAt(0).toUpperCase() || '?');
  const titles = (r.job_titles || []).map(t => `<span class="chip">${esc(t)}</span>`).join('');
  const why = `<div class="why"><b>Why this recruiter</b><ul>${(r.why || []).map(w => `<li>${esc(w)}</li>`).join('')}</ul></div>`;
  return `<div class="card recruiter-card" id="top-card"><div class="stamp stamp-like">Like</div><div class="stamp stamp-nope">Nope</div><div class="card-hero${r.media_url ? ' photo' : ''}"><div class="segments"><i></i></div>${badgesHTML('Recruiter', r.feed_score, false)}${heroMediaHTML(r.media_url, r.media_type, initial)}<div class="hero-scrim"><h2>${esc(r.company)}</h2><p class="hero-sub">${esc(r.name || 'Hiring team')} · ${r.open_jobs} open role${r.open_jobs === 1 ? '' : 's'}</p></div></div><div class="card-body"><p class="desc">Right-swipe to tell ${esc(r.company)} you're interested in working there — no application is sent. If they right-swipe you for any of their jobs, it's a Match.</p><div class="chips">${titles}</div>${why}</div></div>`;
}
function peekHTML(item) {
  if (!item) return '';
  const isRec = item.kind === 'recruiter';
  const initial = esc((item.company || '?').trim().charAt(0).toUpperCase() || '?');
  const title = isRec ? item.company : item.title;
  const sub = isRec ? (item.open_jobs + ' open roles') : item.company;
  return `<div class="card peek" aria-hidden="true"><div class="card-hero">${heroMediaHTML(item.media_url, item.media_type, initial)}<div class="hero-scrim"><h2>${esc(title)}</h2><p class="hero-sub">${esc(sub)}</p></div></div></div>`;
}
function discoveryParams() {
  const p = new URLSearchParams();
  const q = $('f-q').value.trim(), loc = $('f-location').value.trim(), mode = $('f-mode').value;
  if (q) p.set('q', q); if (loc) p.set('location', loc); if (mode) p.set('work_mode', mode);
  const s = p.toString();
  return s ? '?' + s : '';
}
function filtersActive() { return !!($('f-q').value.trim() || $('f-location').value.trim() || $('f-mode').value); }
function setDeckType(t) {
  deckType = t;
  document.querySelectorAll('#deck-toggle button').forEach(b => b.classList.toggle('active', b.dataset.dt === t));
  $('filter-bar').style.display = t === 'recruiters' ? 'none' : '';
  loadDeck();
}
function filterChanged() { clearTimeout(filterTimer); filterTimer = setTimeout(() => { if (deckType !== 'recruiters') loadDeck(); }, 300); }
async function loadDeck() {
  const likes = await apiJ('/api/me/likes');
  if (likes && likes.likes > 0) { $('my-likes').textContent = `♥ ${likes.likes} like${likes.likes === 1 ? '' : 's'}`; $('my-likes').classList.remove('hidden'); }
  let items = [];
  if (deckType === 'jobs' || deckType === 'both') {
    const r = await api('/api/discovery' + discoveryParams());
    items = (r.json || []).map(j => ({ ...j, kind: 'job' }));
  }
  if (deckType === 'recruiters' || deckType === 'both') {
    const recs = (await apiJ('/api/recruiters') || []).map(r => ({ ...r, kind: 'recruiter' }));
    if (deckType === 'recruiters') items = recs;
    else { // Both: a recruiter card after every 3 job cards, leftovers last
      const mixed = []; let ri = 0;
      items.forEach((j, i) => { mixed.push(j); if (i % 3 === 2 && ri < recs.length) mixed.push(recs[ri++]); });
      while (ri < recs.length) mixed.push(recs[ri++]);
      items = mixed;
    }
  }
  deck = items;
  $('pool-count').textContent = deckType === 'recruiters' ? deck.length + ' recruiters in your feed' : deck.length + ' cards in your feed';
  renderDeck();
}
function renderDeck() {
  if (!deck.length) {
    $('deck').innerHTML = deckType !== 'recruiters' && filtersActive()
      ? '<div class="card"><h2>No matches for those filters 🔍</h2><p>Clear the keyword / location / work-mode filters to see your whole feed.</p></div>'
      : '<div class="card"><h2>No more cards 🎉</h2><p>Rescan the boards or check back later.</p></div>';
    return;
  }
  const top = deck[0];
  $('deck').innerHTML = peekHTML(deck[1]) + (top.kind === 'recruiter' ? recruiterCardHTML(top) : cardHTML(top));
  attachDrag('top-card', swipeCurrent);
}
async function rescan() { $('scan-result').textContent = 'Scanning…'; const r = await POST('/api/jobs/scan'); const rep = r.json || {}; $('scan-result').textContent = Object.entries(rep).filter(([k, v]) => v && typeof v === 'object').map(([k, v]) => `${k}: +${v.new} new (${v.status})`).join(' · ') + (rep.pool_total ? ` · Pool total: ${rep.pool_total}` : ''); loadDeck(); }
async function swipeCurrent(dir) {
  if (!deck.length) return;
  const item = deck[0];
  if (item.kind === 'recruiter') {
    const r = await POST('/api/recruiter-swipe', { employer_id: item.employer_id, direction: dir });
    deck.shift();
    if (r.json && r.json.match) showMatch({ name: item.company, sub: 'You liked their recruiter profile — and they liked you back', otherInitial: (item.company || '?').charAt(0).toUpperCase(), otherPhoto: item.media_type === 'image' ? item.media_url : '' });
    renderDeck();
    return;
  }
  const j = item;
  const r = await POST('/api/swipe', { job_id: j.id, direction: dir });
  deck.shift();
  if (r.json && r.json.match) showMatch({ name: j.title, sub: j.company, otherInitial: (j.company || '?').charAt(0).toUpperCase(), otherPhoto: '' });
  else if (dir === 'right' && r.json && r.json.needs_extension) showExtModal(j);
  else if (dir === 'right' && r.json && r.json.application) alert('Application: ' + statusLabel(r.json.application) + ' — with a tailored resume built from your Master Profile facts only. Gaps are listed, never invented.');
  renderDeck();
}
function statusLabel(a) {
  if (a.status === 'submitted') return 'Submitted';
  if (a.status === 'queued_extension') return 'Queued for extension';
  if (a.status === 'needs_human') return 'Needs you';
  if (a.status === 'needs_review') return 'Needs review';
  if (a.method === 'extension_required') return 'Needs extension';
  return 'Prepared';
}
function statusPill(a) { return `<span class="status-pill st-${esc(a.status)}${a.method === 'extension_required' ? ' st-extreq' : ''}">${esc(statusLabel(a))}</span>`; }
function showExtModal(j) {
  openModal(`<div class="ext-modal"><h3>🔒 This one needs your login</h3><p><b>${esc(j.title)}</b> @ ${esc(j.company)} is on a job network / portal (${esc(j.source)}) that applies through <b>your</b> account — LinkedIn, Indeed, or a Workday-style portal.</p><p>Your application package is saved as <b>Prepared — Needs extension</b>, with your tailored resume ready. The free SwipeHire browser extension applies <b>autonomously</b> in <b>your own logged-in browser</b> — it fills and submits for you, and only parks a job for you when a site throws a CAPTCHA, a login wall, or a question your profile can't answer. We never see or store your job-board passwords.</p><div class="ext-btns"><a href="/extension/download"><button>⬇ Install the SwipeHire extension</button></a><button onclick="closeModal();showView('connections')">Set it up on Connections</button><button class="pass" onclick="closeModal()">Later — keep it prepared</button></div></div>`);
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
  $('apps').innerHTML = apps.length ? apps.map(a => `<div class="row"><div><b>${esc(a.title)}</b> @ ${esc(a.company)}<br>${statusPill(a)} <span class="meta">${esc(a.method)} · ${esc(a.created_at || '')} ${a.tailored ? '<span class="stretch-pill">Tailored for this job</span>' : ''}</span><br><span class="meta">${esc(a.proof || '')}</span>${a.coverage && (a.coverage.missing || []).length ? `<br><span class="meta">Keyword gaps (left out, never invented): ${esc((a.coverage.missing || []).join(', '))}</span>` : ''}</div>${a.has_tailored ? `<button onclick="viewTailored(${a.id})">View tailored resume</button>` : ''}</div>`).join('') : '<p>No applications yet — swipe right on a job.</p>';
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
  const f = $('photo-file').files[0]; if (!f) { alert('Pick a picture or video first'); return; }
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
  $('emp-profile').innerHTML = `<div class="card"><div class="profile-head">${avatarHTML(e, 'large')}<div><h3>${esc(e.company || '')}</h3><p>${esc(e.name || '')} · ${esc(e.email || '')}</p><p><span class="likes-pill">♥ ${e.likes_count || 0} recruiter-profile like${e.likes_count === 1 ? '' : 's'}</span></p></div></div></div>`;
  $('emp-media-status').textContent = e.has_media ? '✅ Recruiter media on file — your jobs inherit it.' : '⚠️ No recruiter media yet: a picture or video is required before a job can be published without media attached to the job itself.';
  const jobs = await apiJ('/api/employer/jobs') || [];
  $('my-jobs').innerHTML = '<h4>My jobs</h4>' + (jobs.length ? jobs.map(j => `<p>• ${esc(j.title)} — ${esc(j.location || '')} ${modeLabel(j)} (${esc(j.source)}) <span class="likes-pill">♥ ${j.likes_count || 0}</span></p>`).join('') : '<p class="meta">None yet.</p>');
  $('emp-job-select').innerHTML = jobs.map(j => `<option value="${j.id}">${esc(j.title)} (♥ ${j.likes_count || 0})</option>`).join('');
  loadEmpDeck();
}
async function postJob() {
  const file = $('ej-media').files[0];
  let r;
  if (file) { // attach media to the posting itself (multipart variant)
    const fd = new FormData();
    fd.append('title', $('ej-title').value); fd.append('location', $('ej-loc').value);
    fd.append('description', $('ej-desc').value); fd.append('tags', $('ej-tags').value);
    fd.append('work_mode', $('ej-mode').value); fd.append('remote', $('ej-remote').checked ? 'true' : '');
    fd.append('media', file);
    const resp = await fetch('/api/employer/jobs', { method: 'POST', body: fd });
    let j = null; try { j = await resp.json(); } catch (e) {}
    r = { status: resp.status, json: j };
  } else {
    const tags = $('ej-tags').value.split(',').map(x => x.trim()).filter(Boolean);
    r = await POST('/api/employer/jobs', { title: $('ej-title').value, location: $('ej-loc').value, description: $('ej-desc').value, tags: tags, remote: $('ej-remote').checked, work_mode: $('ej-mode').value });
  }
  if (r.status !== 201) { alert((r.json && r.json.error) || 'Failed'); return; }
  loadEmployer();
}
function empCardHTML(c, job) {
  // v9/N1: candidate VIDEO plays when present, photo otherwise.
  const media = c.video_url ? [c.video_url, 'video'] : (c.photo_url ? [c.photo_url, 'image'] : [null, '']);
  const hero = `<div class="card-hero${media[0] ? ' photo' : ''}"><div class="segments"><i></i></div>${badgesHTML(c.score + '% fit', c.feed_score, false)}${heroMediaHTML(media[0], media[1], initials(c.name || '?'))}<div class="hero-scrim"><h2>${esc(c.name)}</h2><p class="hero-sub">${esc(c.title || '')} · ${c.years_exp || 0} yrs</p></div></div>`;
  return `<div class="card cand-card" id="emp-top-card"><div class="stamp stamp-like">Like</div><div class="stamp stamp-nope">Nope</div>${hero}<div class="card-body"><p class="desc">${esc(c.summary || '')}</p><div class="chips">${(c.skills || []).map(s => `<span class="chip">${esc(s)}</span>`).join('')}</div><div class="why"><b>Why this candidate</b><ul>${(c.why || []).map(w => `<li>${esc(w)}</li>`).join('')}</ul></div></div></div>`;
}
function empPeekHTML(c) {
  if (!c) return '';
  const media = c.video_url ? [c.video_url, 'video'] : (c.photo_url ? [c.photo_url, 'image'] : [null, '']);
  const hero = `<div class="card-hero${media[0] ? ' photo' : ''}">${heroMediaHTML(media[0], media[1], initials(c.name || '?'))}<div class="hero-scrim"><h2>${esc(c.name)}</h2></div></div>`;
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
  const f = $('logo-file').files[0]; if (!f) { alert('Pick a picture or video first'); return; }
  const fd = new FormData(); fd.append('file', f);
  const r = await fetch('/api/employer/logo', { method: 'POST', body: fd }); const j = await r.json();
  if (r.status !== 200) { alert(j.error || 'Upload failed'); return; }
  loadEmployer();
}

// ---------- connections (apply tiers) ----------
async function loadConnections() {
  const data = await apiJ('/api/connections') || {};
  const conns = data.connections || [];
  const ext = data.extension || {};
  const byBoard = {}; conns.forEach(c => byBoard[c.board] = c);
  const boardRow = (board, label) => {
    const c = byBoard[board];
    return c
      ? `<div class="row"><div><b>${label}</b> — ${esc(c.identifier)}<br><span class="meta">Status: ${esc(c.status)} · added ${esc(c.created_at || '')}</span></div><button class="pass" onclick="disconnectBoard('${board}')">Disconnect</button></div>`
      : `<div class="conn-form"><label>${label} account (username or email — <b>never</b> your password)<input id="conn-${board}" placeholder="you@example.com"></label><button onclick="linkBoard('${board}')">Link ${label}</button></div>`;
  };
  const extState = ext.connected
    ? `<span class="status-pill st-submitted">Extension connected</span> <span class="meta">last used ${esc(ext.last_used_at || '')}</span>`
    : ext.has_token
      ? `<span class="status-pill st-queued_extension">Pairing token issued</span> <span class="meta">paste it into the extension popup to finish connecting</span>`
      : `<span class="status-pill st-prepared">Not connected</span>`;
  $('connections').innerHTML = `
  <div class="card conn-tier"><h3>Tier 0 — No-account jobs <span class="status-pill st-submitted">Active</span></h3>
   <p>Greenhouse, Lever, Ashby, Workable and plain company application forms need <b>no account at all</b>. Right-swipes to these jobs are prepared for the form executor — tailored resume + answers from your stored facts. Nothing to set up, and the browser extension is never involved in these.</p></div>
  <div class="card conn-tier" id="extension-setup"><h3>Tier 1 — Browser extension <span class="meta">(only for 🔒 login-required jobs)</span></h3>
   <p>For LinkedIn / Indeed / Workday-style jobs, the free SwipeHire extension applies <b>autonomously</b> in <b>your own browser, using the sessions you are already logged into</b> — SwipeHire never sees or stores those passwords. It claims your queued jobs one at a time, fills the forms from your application package, and <b>makes the final submit click itself</b>, pacing itself like a human (≥90s between submissions, per-board daily caps). The exception path is <b>Needs you</b>: a CAPTCHA, a login wall / security checkpoint, or a required question your stored facts can't answer parks the job in your tracker — the extension never tries to defeat those.</p>
   <p>${extState}</p>
   <div class="ext-btns"><a href="/extension/download"><button>⬇ Download extension (.zip)</button></a><button onclick="genPairingToken()">Generate pairing token</button>${ext.has_token ? '<button class="pass" onclick="revokePairingToken()">Revoke token</button>' : ''}</div>
   <div id="pairing-token" class="token-box hidden"></div>
   <p class="meta">Install: unzip → chrome://extensions → Developer mode → Load unpacked → pick the folder → click the SwipeHire icon → paste the pairing token → Save. Full steps are in the zip's README.</p></div>
  <div class="card conn-tier"><h3>Tier 2 — Link an account <span class="meta">(fallback)</span></h3>
   <p>If you'd rather a server worker apply for you, record which account a worker session will be established for. <b>We store only the board + the identifier you type — never a password.</b> A worker session is established separately; until then the status stays <i>pending_session</i>.</p>
   ${boardRow('linkedin', 'LinkedIn')}
   ${boardRow('indeed', 'Indeed')}
  </div>`;
}
async function genPairingToken() {
  const r = await POST('/api/ext/pairing-token');
  if (r.status !== 201) { alert((r.json && r.json.error) || 'Failed'); return; }
  const box = $('pairing-token');
  box.innerHTML = `Your pairing token (shown once — a new one revokes this):<br><b class="token-code">${esc(r.json.token)}</b><br><button onclick="navigator.clipboard && navigator.clipboard.writeText('${esc(r.json.token)}')">Copy</button>`;
  box.classList.remove('hidden');
}
async function revokePairingToken() {
  await fetch('/api/ext/pairing-token', { method: 'DELETE' });
  loadConnections();
}
async function linkBoard(board) {
  const identifier = $('conn-' + board).value.trim();
  if (!identifier) { alert('Enter the account username/email first'); return; }
  const r = await POST('/api/connections', { board: board, identifier: identifier });
  if (r.status !== 201) { alert((r.json && r.json.error) || 'Failed'); return; }
  loadConnections();
}
async function disconnectBoard(board) {
  await fetch('/api/connections/' + board, { method: 'DELETE' });
  loadConnections();
}
async function deleteAccount() {
  if (!confirm('Delete your SwipeHire account and ALL your data? This cannot be undone.')) return;
  if (!confirm('Really sure? Documents, applications, connections, matches — everything goes.')) return;
  const r = await fetch('/api/account', { method: 'DELETE' });
  if (r.status !== 200) { alert('Delete failed'); return; }
  location.reload();
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
