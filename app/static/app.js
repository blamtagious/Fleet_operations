/* Fleet Quote Builder — UI */
'use strict';

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

let BOOT = { sellers: [], settings: {}, years: [], quotes: [], upfits: [] };
let Q = null;              // current quote record
let SPEC = null;           // parsed spec proposal
let SPEC_FILE = null;      // staged path of the dropped PDF
let UPFIT_FILES = [];      // pending files for a library record

const money = v => (v === null || v === undefined || v === '' || isNaN(+v))
  ? '' : '$' + (+v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const num = v => { const n = parseFloat(String(v ?? '').replace(/[$,\s]/g, '')); return isNaN(n) ? 0 : n; };
const shortPath = p => String(p ?? '').split(/[\\/]/).filter(Boolean).slice(-2).join('/');
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function toast(msg, bad) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'on' + (bad ? ' bad' : '');
  clearTimeout(t._t);
  t._t = setTimeout(() => (t.className = ''), bad ? 6000 : 2800);
}

async function api(path, body) {
  const opt = body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : {};
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({ ok: false, error: 'Bad response from server' }));
  if (!j.ok) throw new Error(j.error || 'Request failed');
  return j;
}

const readAsB64 = f => new Promise((res, rej) => {
  const fr = new FileReader();
  fr.onload = () => res(String(fr.result).split(',')[1]);
  fr.onerror = rej;
  fr.readAsDataURL(f);
});

/* ---------------- tabs ---------------- */
$$('.tabs button').forEach(b => b.onclick = () => {
  $$('.tabs button').forEach(x => x.classList.toggle('on', x === b));
  $$('.view').forEach(v => v.classList.toggle('on', v.id === 'view-' + b.dataset.tab));
  if (b.dataset.tab === 'saved') loadSaved();
});

/* ---------------- drag & drop ---------------- */
function wireDrop(zone, input, handler) {
  zone.onclick = () => input.click();
  input.onchange = () => { if (input.files.length) handler([...input.files]); input.value = ''; };
  ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, e => {
    e.preventDefault(); e.stopPropagation(); zone.classList.add('hot');
  }));
  ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, e => {
    e.preventDefault(); e.stopPropagation();
    if (ev === 'dragleave' && zone.contains(e.relatedTarget)) return;
    zone.classList.remove('hot');
  }));
  zone.addEventListener('drop', e => {
    const fs = [...(e.dataTransfer?.files || [])].filter(f => /\.pdf$/i.test(f.name));
    if (fs.length) handler(fs); else toast('Drop a PDF file.', true);
  });
}
// Never let a stray drop navigate the page away.
['dragover', 'drop'].forEach(ev =>
  window.addEventListener(ev, e => { if (!e.target.closest('.drop')) e.preventDefault(); }));

const DROP_IDLE =
  '<h2>Drop a Ford spec proposal PDF here</h2>' +
  '<p>It reads the configuration, matches the State of Tennessee bid sheet, ' +
  'and finds the FIN code. Or click to choose a file.</p>';

function setDropBusy(name) {
  $('#drop').querySelector('h2').innerHTML =
    '<span class="spin" style="border-color:#bbb;border-top-color:#066fd1"></span> Reading ' + esc(name) + '…';
  $('#drop').querySelector('p').textContent = 'Parsing configuration and matching bid pricing.';
}

function resetDropZone() {
  const z = $('#drop');
  z.querySelector('h2').textContent = 'Drop a Ford spec proposal PDF here';
  z.querySelector('p').textContent =
    'It reads the configuration, matches the State of Tennessee bid sheet, and finds the FIN code. Or click to choose a file.';
}

wireDrop($('#drop'), $('#file'), async files => {
  setDropBusy(files[0].name);
  try {
    const payload = [];
    for (const f of files) payload.push({ name: f.name, data: await readAsB64(f) });
    const j = await api('/api/parse-spec', { files: payload });
    const r = j.results[0];
    SPEC = r.spec; SPEC_FILE = r.source; Q = r.quote;
    $('#recalled').innerHTML = '';
    $('#specname').textContent = r.source_name;
    renderSuggestedUpfits(r.applicable_upfits);
    showEditor();
    toast('Loaded ' + (SPEC.vehicle_title || 'spec proposal'));
  } catch (e) {
    toast(e.message, true);
  } finally {
    resetDropZone();
  }
});

/* ---------------- editor ---------------- */
function showEditor() {
  $('#editor').style.display = '';
  $('#q_id').value = Q.quote_id || '';
  $('#q_date').value = Q.quote_date || '';
  $('#c_org').value = Q.customer.org || '';
  $('#c_name').value = Q.customer.name || '';
  $('#c_title').value = Q.customer.title || '';
  $('#c_fin').value = Q.customer.fin || '';
  $('#v_qty').value = Q.vehicle.quantity || 1;
  $('#q_notes').value = Q.notes || '';
  $('#inc_dest').checked = !!Q.include_destination;
  $('#attach_spec').checked = Q.attach_spec !== false;
  $('#desthint').textContent = Q.destination
    ? 'Proposal shows ' + money(Q.destination) + '. State bid prices are normally quoted delivered.'
    : 'No destination charge on the proposal.';

  $('#q_seller').innerHTML = BOOT.sellers
    .map(s => `<option value="${esc(s.id)}">${esc(s.name)}${s.title ? ' — ' + esc(s.title) : ''}</option>`).join('');
  if (Q.seller && Q.seller.id) $('#q_seller').value = Q.seller.id;

  $('#q_year').innerHTML = BOOT.years.map(y => `<option>${esc(y)}</option>`).join('');
  if (Q.contract_year) $('#q_year').value = Q.contract_year;

  renderWarnings(); renderSellerWarning(); renderVehicle(); renderOptions(); renderUpfits(); renderAdjustments(); recalc();
  $('#editor').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderSellerWarning() {
  const s = (Q && Q.seller) || {};
  const missing = ['email', 'phone'].filter(k => !String(s[k] || '').trim());
  const el = $('#sellerwarn');
  if (!el) return;
  if (!missing.length) { el.innerHTML = ''; return; }
  el.innerHTML =
    `<div class="note warn" style="margin:8px 0 0">
       No ${missing.join(' or ')} on file for ${esc(s.name || 'this seller')} — the quote will
       print without ${missing.length > 1 ? 'them' : 'it'}. Add ${missing.length > 1 ? 'them' : 'it'}
       to <code>app/config/sellers.json</code>.
     </div>`;
}

function renderWarnings() {
  const w = (Q.warnings || []).map(x => `<div class="note warn">${esc(x)}</div>`).join('');
  $('#warnings').innerHTML = w;
}

function renderVehicle() {
  const ps = Q.pricing_source || {};
  $('#veh_rows').innerHTML = (Q.vehicle.lines || []).map((l, i) => `
    <tr>
      <td>${esc(l.line || i + 1)}</td>
      <td>
        <input data-veh="${i}" data-k="description" value="${esc(l.description)}">
        <input data-veh="${i}" data-k="detail" value="${esc(l.detail || '')}" class="code" style="margin-top:4px">
      </td>
      <td class="r"><input class="money" data-veh="${i}" data-k="price" value="${l.price ?? ''}"></td>
    </tr>`).join('');

  if (ps.matched) {
    $('#bidinfo').textContent = ps.model_number + ' · ' + ps.year;
    $('#pricingnote').innerHTML =
      `<div class="note good">Matched State of Tennessee bid line <strong>${esc(ps.model_number)}</strong> — ${esc(ps.description)}.
       Sheet MSRP ${money(ps.sheet_msrp)} → <strong>state bid ${money(ps.sheet_bid)}</strong>
       (${(ps.discount * 100).toFixed(2)}% off).</div>`;
    $('#bidhint').textContent = 'Quoting the contract price, not proposal MSRP.';
  } else {
    $('#bidinfo').textContent = 'no bid match';
    $('#pricingnote').innerHTML =
      `<div class="note bad">No bid line matched this configuration — the vehicle is priced at proposal MSRP. Pick a contract line below.</div>`;
    $('#bidhint').textContent = '';
  }
  bindInputs();
}

function renderOptions() {
  $('#opt_lump').checked = !!Q.options_lump;
  const rows = (Q.options || []).map((o, i) => `
    <tr class="${o.include ? '' : 'off'}">
      <td class="c"><input type="checkbox" data-opt="${i}" data-k="include" ${o.include ? 'checked' : ''} style="width:auto"></td>
      <td class="desc">
        <input data-opt="${i}" data-k="description" value="${esc(o.description)}">
        ${(o.flags || []).map(f => `<div class="hint" style="color:var(--warn)">⚠ ${esc(f)}</div>`).join('')}
      </td>
      <td><input data-opt="${i}" data-k="code" value="${esc(o.code || '')}"></td>
      <td class="r code">${money(o.msrp)}</td>
      <td class="r"><input class="money" data-opt="${i}" data-k="price" value="${o.price ?? ''}"></td>
      <td class="c"><button class="link" data-del-opt="${i}">✕</button></td>
    </tr>`).join('');
  $('#opt_rows').innerHTML = rows || '<tr><td colspan="6" class="empty">No priced options on this proposal.</td></tr>';
  bindInputs();
}

function renderUpfits() {
  const rows = (Q.upfits || []).map((u, i) => `
    <tr class="${u.include !== false ? '' : 'off'}">
      <td class="c"><input type="checkbox" data-up="${i}" data-k="include" ${u.include !== false ? 'checked' : ''} style="width:auto"></td>
      <td class="desc"><input data-up="${i}" data-k="description" value="${esc(u.description)}">
        <textarea data-up="${i}" data-k="details" rows="${Math.min(8, Math.max(2, (u.details || '').split('\n').length))}"
          placeholder="Extended description for the customer. Prints in small type under this line."
          style="margin-top:4px;width:100%;font-size:12px">${esc(u.details || '')}</textarea></td>
      <td><input data-up="${i}" data-k="code" value="${esc(u.code || '')}"></td>
      <td class="r"><input class="money" data-up="${i}" data-k="price" value="${u.price ?? ''}"></td>
      <td class="c"><button class="link" data-del-up="${i}">✕</button></td>
    </tr>`).join('');
  $('#upfit_rows').innerHTML = rows || '<tr><td colspan="5" class="empty">No upfitting on this quote.</td></tr>';
  bindInputs();
}

function renderSuggestedUpfits(list) {
  if (!list || !list.length) { $('#upfit_suggest').innerHTML = ''; return; }
  $('#upfit_suggest').innerHTML =
    `<div class="note good" style="background:#fff;border-color:var(--line);color:var(--ink)">
       <strong>${list.length}</strong> upfit${list.length > 1 ? 's' : ''} in the library fit this chassis:
       <div style="margin-top:7px">` +
    list.slice(0, 8).map((u, i) =>
      `<span class="chip"><span class="tag ${u._fit}">${u._fit}</span>
        ${esc(u.upfitter)} — ${esc(u.title)} ${u.price ? '· ' + money(u.price) : ''}
        <button class="link" data-addup="${i}">add</button></span>`).join('') +
    `</div></div>`;
  $('#upfit_suggest')._list = list;
  $$('#upfit_suggest [data-addup]').forEach(b => b.onclick = () => {
    const u = list[+b.dataset.addup];
    Q.upfits.push({
      description: (u.upfitter ? u.upfitter + ' — ' : '') + (u.title || ''),
      code: u.reference || '', price: u.price, include: true, upfit_id: u.id,
      details: u.details || '',
    });
    renderUpfits(); recalc(); toast('Added ' + u.title);
  });
}

function renderAdjustments() {
  $('#adj_rows').innerHTML = (Q.adjustments || []).map((a, i) => `
    <tr>
      <td><input data-adj="${i}" data-k="label" value="${esc(a.label || '')}" placeholder="Fleet discount"></td>
      <td class="r"><input class="money" data-adj="${i}" data-k="amount" value="${a.amount ?? ''}" placeholder="-500.00"></td>
      <td class="c"><button class="link" data-del-adj="${i}">✕</button></td>
    </tr>`).join('');
  bindInputs();
}

/* ---------------- binding ---------------- */
function bindInputs() {
  $$('[data-veh],[data-opt],[data-up],[data-adj]').forEach(el => {
    if (el._b) return; el._b = 1;
    const ev = el.type === 'checkbox' ? 'change' : 'input';
    el.addEventListener(ev, () => {
      const k = el.dataset.k;
      const val = el.type === 'checkbox' ? el.checked
        : (k === 'price' || k === 'amount') ? num(el.value) : el.value;
      if (el.dataset.veh !== undefined) Q.vehicle.lines[+el.dataset.veh][k] = val;
      else if (el.dataset.opt !== undefined) { Q.options[+el.dataset.opt][k] = val; if (k === 'include') el.closest('tr').classList.toggle('off', !val); }
      else if (el.dataset.up !== undefined) { Q.upfits[+el.dataset.up][k] = val; if (k === 'include') el.closest('tr').classList.toggle('off', !val); }
      else if (el.dataset.adj !== undefined) Q.adjustments[+el.dataset.adj][k] = val;
      recalc();
    });
  });
  $$('[data-del-opt]').forEach(b => b.onclick = () => { Q.options.splice(+b.dataset.delOpt, 1); renderOptions(); recalc(); });
  $$('[data-del-up]').forEach(b => b.onclick = () => { Q.upfits.splice(+b.dataset.delUp, 1); renderUpfits(); recalc(); });
  $$('[data-del-adj]').forEach(b => b.onclick = () => { Q.adjustments.splice(+b.dataset.delAdj, 1); renderAdjustments(); recalc(); });
}

['q_id:quote_id', 'q_date:quote_date', 'q_notes:notes'].forEach(pair => {
  const [id, key] = pair.split(':');
  document.addEventListener('input', e => { if (e.target.id === id && Q) Q[key] = e.target.value; });
});
document.addEventListener('input', e => {
  if (!Q) return;
  const id = e.target.id;
  if (id === 'c_org') Q.customer.org = e.target.value;
  if (id === 'c_name') Q.customer.name = e.target.value;
  if (id === 'c_title') Q.customer.title = e.target.value;
  if (id === 'c_fin') Q.customer.fin = e.target.value;
  if (id === 'v_qty') { Q.vehicle.quantity = Math.max(1, parseInt(e.target.value) || 1); recalc(); }
});
document.addEventListener('change', e => {
  if (!Q) return;
  if (e.target.id === 'q_seller') {
    Q.seller = BOOT.sellers.find(s => s.id === e.target.value) || Q.seller;
    renderSellerWarning();
  }
  if (e.target.id === 'inc_dest') { Q.include_destination = e.target.checked; recalc(); }
  if (e.target.id === 'q_year') Q.contract_year = e.target.value;
});

$('#opt_lump').onchange = e => { Q.options_lump = e.target.checked; recalc(); };
$('#add_opt').onclick = () => { Q.options.push({ code: '', description: '', price: 0, include: true, flags: [] }); renderOptions(); };
$('#add_upfit').onclick = () => { Q.upfits.push({ code: '', description: '', price: 0, include: true }); renderUpfits(); };
$('#add_adj').onclick = () => { Q.adjustments.push({ label: '', amount: 0 }); renderAdjustments(); };

/* ---------------- totals ---------------- */
async function recalc() {
  if (!Q) return;
  try {
    const j = await api('/api/recalc', { quote: Q });
    Q.totals = j.quote.totals;
    const t = Q.totals, qty = t.quantity || 1;
    $('#totals').innerHTML =
      `<div><span class="muted">Vehicle</span><span>${money(t.vehicle)}</span></div>
       <div><span class="muted">Options &amp; accessories</span><span>${money(t.options)}</span></div>
       ${t.upfitting ? `<div><span class="muted">Upfitting</span><span>${money(t.upfitting)}</span></div>` : ''}
       ${t.destination ? `<div><span class="muted">Destination</span><span>${money(t.destination)}</span></div>` : ''}
       ${t.adjustments ? `<div><span class="muted">Adjustments</span><span>${money(t.adjustments)}</span></div>` : ''}
       ${qty > 1 ? `<div><span class="muted">Per vehicle</span><span>${money(t.unit_total)}</span></div>
                    <div><span class="muted">× quantity</span><span>${qty}</span></div>` : ''}
       <div class="grand"><span>TOTAL</span><span>${money(t.grand_total)}</span></div>`;
  } catch (e) { /* transient */ }
}

/* ---------------- autocompletes ---------------- */
function autocomplete(input, acEl, fetcher, renderItem, onPick) {
  let timer, items = [], sel = -1;
  const close = () => { acEl.style.display = 'none'; sel = -1; };
  input.addEventListener('input', () => {
    clearTimeout(timer);
    const v = input.value.trim();
    if (v.length < 2) return close();
    timer = setTimeout(async () => {
      try {
        items = await fetcher(v);
        if (!items.length) return close();
        acEl.innerHTML = items.map((it, i) => `<div data-i="${i}">${renderItem(it)}</div>`).join('');
        acEl.style.display = '';
        $$('div[data-i]', acEl).forEach(d => d.onmousedown = e => {
          e.preventDefault(); onPick(items[+d.dataset.i]); close();
        });
      } catch { close(); }
    }, 180);
  });
  input.addEventListener('keydown', e => {
    if (acEl.style.display === 'none') return;
    const ds = $$('div[data-i]', acEl);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      sel = Math.max(0, Math.min(ds.length - 1, sel + (e.key === 'ArrowDown' ? 1 : -1)));
      ds.forEach((d, i) => d.classList.toggle('sel', i === sel));
    } else if (e.key === 'Enter' && sel >= 0) { e.preventDefault(); onPick(items[sel]); close(); }
    else if (e.key === 'Escape') close();
  });
  input.addEventListener('blur', () => setTimeout(close, 120));
}

autocomplete($('#c_fin'), $('#fin_ac'),
  async v => (await api('/api/fin?q=' + encodeURIComponent(v))).results,
  r => `<strong>${esc(r.fin)}</strong> <span class="m">${esc(r.agency)}</span>`,
  r => { $('#c_fin').value = r.fin; Q.customer.fin = r.fin; toast(r.agency); });

$('#pick_bid').onclick = () => {
  const w = $('#bidsearch_wrap');
  w.style.display = w.style.display === 'none' ? '' : 'none';
  if (w.style.display === '') $('#bidsearch').focus();
};

autocomplete($('#bidsearch'), $('#bid_ac'),
  async v => (await api('/api/pricing?q=' + encodeURIComponent(v) + '&year=' + encodeURIComponent($('#q_year').value || ''))).results,
  r => `<strong>${esc(r.model_number)}</strong> <span class="m">${esc(r.description)}</span><br>
        <span class="m">${esc(r.year)} · MSRP ${money(r.msrp)} → bid <strong>${money(r.bid)}</strong></span>`,
  r => {
    Q.vehicle.lines[0].price = r.bid;
    Q.vehicle.lines[0].bid = r.bid;
    Q.vehicle.model_number = r.model_number;
    Q.pricing_source = {
      matched: true, model_number: r.model_number, description: r.description,
      sheet_msrp: r.msrp, sheet_bid: r.bid, discount: r.discount, year: r.year, candidates: [],
    };
    Q.warnings = [];
    renderWarnings(); renderSellerWarning(); renderVehicle(); recalc();
    toast('Now quoting ' + r.model_number);
  });

/* ---------------- actions ---------------- */
$('#attach_spec').onchange = e => { Q.attach_spec = e.target.checked; };
$('#btn_save').onclick = async () => {
  try {
    const j = await api('/api/save', { quote: Q, spec_files: SPEC_FILE ? [SPEC_FILE] : [] });
    Q = j.quote; $('#q_id').value = Q.quote_id;
    $('#savedpath').textContent = 'Saved → ' + Q._dir.split('/').slice(-2).join('/');
    toast('Draft saved');
  } catch (e) { toast(e.message, true); }
};

$('#btn_pdf').onclick = async ev => {
  const b = ev.target; const old = b.textContent;
  b.disabled = true; b.innerHTML = '<span class="spin"></span> Generating…';
  try {
    const j = await api('/api/pdf', { quote: Q, spec_files: SPEC_FILE ? [SPEC_FILE] : [] });
    Q = j.quote; $('#q_id').value = Q.quote_id;
    $('#savedpath').innerHTML = 'Saved → ' + esc(shortPath(Q._dir)) +
      ` · <button class="link" id="reveal">show in Finder</button>`;
    $('#reveal').onclick = () => api('/api/reveal', { path: j.pdf });
    window.open('/api/file?path=' + encodeURIComponent(j.pdf), '_blank');
    toast(j.attached_pages ? 'Quote PDF generated, with ' + j.attached_pages + ' pages attached' : 'Quote PDF generated');
  } catch (e) { toast(e.message, true); }
  finally { b.disabled = false; b.textContent = old; }
};

$('#btn_reset').onclick = () => {
  if (!confirm('Discard this quote and start over?')) return;
  Q = null; SPEC = null; SPEC_FILE = null;
  $('#recalled').innerHTML = '';
  $('#editor').style.display = 'none';
  $('#specname').textContent = '';
  $('#upfit_suggest').innerHTML = '';
};

/* ---------------- upfit library ---------------- */
let EDITING = null;      // id of the record being edited, or null when adding
let U_ITEMS = [];        // breakdown rows in the form

wireDrop($('#updrop'), $('#upfile'), async files => {
  for (const f of files) UPFIT_FILES.push({ name: f.name, data: await readAsB64(f) });
  renderPendingFiles();
  const first = files[0];
  if (!$('#u_title').value) $('#u_title').value = first.name.replace(/\.pdf$/i, '').replace(/[_-]+/g, ' ');
  toast('Reading ' + first.name + '...');
  try {
    const j = await api('/api/upfit-parse', { files: [{ name: first.name, data: UPFIT_FILES[UPFIT_FILES.length - files.length].data }] });
    applyParsedUpfit(j.parsed);
  } catch (e) { toast('Attached ' + first.name + ' (could not read it: ' + e.message + ')', true); }
});

/* Fill the library form from a parsed upfitter quote. Only blank fields are
   touched, so anything already typed is kept. The person reviews, then saves. */
function applyParsedUpfit(p) {
  const put = (id, v) => { if (v && !$('#' + id).value.trim()) $('#' + id).value = v; };
  const a = p.applies_to || {};
  $('#u_title').value = ($('#u_title').value && !p.title) ? $('#u_title').value : (p.title || $('#u_title').value);
  put('u_upfitter', p.upfitter); put('u_ref', p.reference); put('u_body', p.body_type);
  put('u_qdate', p.quote_date); put('u_valid', p.valid_until); put('u_details', p.details);
  put('u_codes', (a.model_codes || []).join(', ')); put('u_wb', (a.wheelbase || []).join(', '));
  if (a.cab && !$('#u_cab').value) $('#u_cab').value = a.cab;
  if (a.drw !== undefined && $('#u_drw').value === '') $('#u_drw').value = a.drw ? 'true' : 'false';
  if ((p.line_items || []).length && !U_ITEMS.length) { U_ITEMS = p.line_items.map(i => ({ ...i })); renderItems(); }
  else if (p.price && !$('#u_price').value) $('#u_price').value = Number(p.price).toFixed(2);
  const notes = (p.confidence || []).concat(p.notes ? [p.notes] : []);
  $('#u_editing').innerHTML = `<div class="note ${notes.length ? 'warn' : 'good'}">
    Read from the PDF. <strong>Check it before saving</strong>: price, lines and the customer description.
    ${notes.map(n => '<div>' + esc(n) + '</div>').join('')}</div>`;
  toast('Filled in from the PDF. Please check it.');
}

function renderPendingFiles() {
  $('#upfiles').innerHTML = UPFIT_FILES.map((f, i) =>
    `<span class="chip">${esc(f.name)} <button class="link" data-rmpend="${i}">✕</button></span>`).join('');
  $$('[data-rmpend]').forEach(b => b.onclick = () => {
    UPFIT_FILES.splice(+b.dataset.rmpend, 1); renderPendingFiles();
  });
}

/* --- breakdown rows --- */
function renderItems() {
  $('#u_items').innerHTML = U_ITEMS.map((it, i) => `
    <tr>
      <td><input data-it="${i}" data-k="description" value="${esc(it.description || '')}" placeholder="Body, install, paint…"></td>
      <td style="width:118px"><input class="money" data-it="${i}" data-k="price" value="${it.price ?? ''}"></td>
      <td class="c" style="width:32px"><button class="link" data-delit="${i}">✕</button></td>
    </tr>`).join('');
  $$('#u_items [data-it]').forEach(el => el.addEventListener('input', () => {
    const k = el.dataset.k;
    U_ITEMS[+el.dataset.it][k] = k === 'price' ? num(el.value) : el.value;
    syncItemTotal();
  }));
  $$('#u_items [data-delit]').forEach(b => b.onclick = () => {
    U_ITEMS.splice(+b.dataset.delit, 1); renderItems(); syncItemTotal();
  });
  syncItemTotal();
}

function syncItemTotal() {
  const live = U_ITEMS.filter(i => String(i.description || '').trim());
  if (!live.length) { $('#u_price').readOnly = false; return; }
  const total = live.reduce((a, i) => a + num(i.price), 0);
  $('#u_price').value = total.toFixed(2);
  $('#u_price').readOnly = true;
}

$('#u_additem').onclick = () => { U_ITEMS.push({ description: '', price: 0 }); renderItems(); };

/* --- form <-> record --- */
const U_FIELDS = ['u_upfitter','u_ref','u_title','u_body','u_price','u_year',
                  'u_qdate','u_valid','u_notes','u_details','u_codes','u_chassis','u_wb'];

function clearForm() {
  EDITING = null; UPFIT_FILES = []; U_ITEMS = [];
  U_FIELDS.forEach(id => { $('#' + id).value = ''; });
  $('#u_cab').value = ''; $('#u_drw').value = '';
  $('#u_year').value = String(new Date().getFullYear());
  $('#u_price').readOnly = false;
  $('#u_mode').textContent = 'Add an upfitter quote';
  $('#u_cancel').style.display = 'none';
  $('#u_save').textContent = 'Save to library';
  $('#u_editing').innerHTML = '';
  renderPendingFiles(); renderItems(); renderLibrary();
}

$('#u_cancel').onclick = clearForm;

function editUpfit(id, asCopy) {
  const u = (BOOT.upfits || []).find(x => x.id === id);
  if (!u) return;
  const a = u.applies_to || {};
  EDITING = asCopy ? null : id;
  UPFIT_FILES = [];
  U_ITEMS = (u.line_items || []).map(i => ({ ...i }));

  $('#u_upfitter').value = u.upfitter || '';
  $('#u_ref').value = asCopy ? '' : (u.reference || '');
  $('#u_title').value = (u.title || '') + (asCopy ? ' (copy)' : '');
  $('#u_body').value = u.body_type || '';
  $('#u_price').value = u.price ?? '';
  $('#u_year').value = u.year || '';
  $('#u_qdate').value = asCopy ? '' : (u.quote_date || '');
  $('#u_valid').value = asCopy ? '' : (u.valid_until || '');
  $('#u_notes').value = u.notes || '';
  $('#u_details').value = u.details || '';
  $('#u_codes').value = (a.model_codes || []).join(', ');
  $('#u_chassis').value = a.chassis || '';
  $('#u_wb').value = (a.wheelbase || []).join(', ');
  $('#u_cab').value = a.cab || '';
  $('#u_drw').value = a.drw === true ? 'true' : a.drw === false ? 'false' : '';

  $('#u_mode').textContent = asCopy ? 'Duplicate an upfitter quote' : 'Edit upfitter quote';
  $('#u_cancel').style.display = '';
  $('#u_save').textContent = asCopy ? 'Save as new record' : 'Save changes';

  const attached = (u._files || []).map(f =>
    `<span class="chip">${esc(f)}
       <button class="link" data-openf="${esc(u._dir)}/${esc(f)}">open</button>
       <button class="link danger" data-rmf="${esc(f)}">✕</button></span>`).join('');
  $('#u_editing').innerHTML = asCopy
    ? `<div class="note">Copying <strong>${esc(u.id)}</strong>. Give it a new reference — the original is untouched.</div>`
    : `<div class="note">Editing <strong>${esc(u.id)}</strong>.` +
      (u._used_by && u._used_by.length
        ? ` Used on ${u._used_by.length} quote${u._used_by.length > 1 ? 's' : ''} — those keep the price they were written with.`
        : '') +
      `</div>` + (attached ? `<div style="margin-bottom:10px">${attached}</div>` : '');

  $$('[data-openf]').forEach(b => b.onclick = () =>
    window.open('/api/file?path=' + encodeURIComponent(b.dataset.openf), '_blank'));
  $$('[data-rmf]').forEach(b => b.onclick = async () => {
    if (!confirmInline(b, 'Remove ' + b.dataset.rmf + '?')) return;
    try {
      const j = await api('/api/upfit-file-delete', { id, file: b.dataset.rmf });
      BOOT.upfits = j.upfits; editUpfit(id, false); toast('Removed');
    } catch (e) { toast(e.message, true); }
  });

  renderPendingFiles(); renderItems(); renderLibrary();
  $('#u_upfitter').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// confirm() is unavailable in some embeds; use a two-click confirm instead.
function confirmInline(btn, msg) {
  if (btn._armed) { clearTimeout(btn._t); btn._armed = false; return true; }
  const old = btn.textContent;
  btn._armed = true; btn.textContent = 'sure?';
  btn._t = setTimeout(() => { btn._armed = false; btn.textContent = old; }, 3500);
  return false;
}

$('#u_save').onclick = async () => {
  const upfitter = $('#u_upfitter').value.trim();
  const title = $('#u_title').value.trim();
  if (!upfitter || !title) return toast('Upfitter and title are required.', true);
  const rec = {
    upfitter, title,
    reference: $('#u_ref').value.trim(),
    body_type: $('#u_body').value.trim(),
    price: num($('#u_price').value) || null,
    line_items: U_ITEMS.filter(i => String(i.description || '').trim()),
    year: $('#u_year').value.trim() || String(new Date().getFullYear()),
    quote_date: $('#u_qdate').value.trim(),
    valid_until: $('#u_valid').value.trim(),
    notes: $('#u_notes').value.trim(),
    details: $('#u_details').value.trim(),
    applies_to: {
      model_codes: $('#u_codes').value.split(',').map(x => x.trim().toUpperCase()).filter(Boolean),
      chassis: $('#u_chassis').value.trim(),
      cab: $('#u_cab').value,
      wheelbase: $('#u_wb').value.split(',').map(x => x.trim()).filter(Boolean),
      drw: $('#u_drw').value === '' ? null : $('#u_drw').value === 'true',
    },
  };
  try {
    const j = await api('/api/upfit-save', { upfit: rec, files: UPFIT_FILES, original_id: EDITING || '' });
    BOOT.upfits = j.upfits;
    const was = EDITING;
    clearForm();
    toast(was ? 'Updated ' + j.upfit.id : 'Saved ' + j.upfit.id);
  } catch (e) { toast(e.message, true); }
};

/* --- the list --- */
$('#u_search').addEventListener('input', renderLibrary);

function renderLibrary() {
  const all = BOOT.upfits || [];
  const q = ($('#u_search') ? $('#u_search').value : '').trim().toLowerCase();
  const list = !q ? all : all.filter(u => {
    const a = u.applies_to || {};
    const hay = [u.upfitter, u.title, u.reference, u.body_type, u.notes, u.year,
                 a.chassis, a.cab, (a.model_codes || []).join(' '),
                 (a.wheelbase || []).join(' ')].join(' ').toLowerCase();
    return q.split(/\s+/).every(t => hay.includes(t));
  });

  $('#upcount').textContent = q ? `${list.length} of ${all.length}` :
    `${all.length} record${all.length === 1 ? '' : 's'}`;

  if (!list.length) {
    $('#uplist').innerHTML = `<div class="empty">${all.length
      ? 'Nothing matches that search.'
      : 'Nothing saved yet. Drop an upfitter quote on the left.'}</div>`;
    return;
  }

  $('#uplist').innerHTML = list.map(u => {
    const a = u.applies_to || {};
    const fits = [(a.model_codes || []).join('/'), a.cab,
      (a.wheelbase || []).length ? (a.wheelbase || []).join('/') + '" WB' : '',
      a.drw === true ? 'DRW' : a.drw === false ? 'SRW' : ''].filter(Boolean).join(' · ');
    const pills = [];
    if (u._expiry === 'expired') pills.push(`<span class="pill expired">expired ${Math.abs(u._days_left)}d ago</span>`);
    else if (u._expiry === 'soon') pills.push(`<span class="pill soon">expires in ${u._days_left}d</span>`);
    else if (u._expiry === 'ok') pills.push(`<span class="pill ok">valid</span>`);
    if ((u._used_by || []).length) pills.push(`<span class="pill used">on ${u._used_by.length} quote${u._used_by.length > 1 ? 's' : ''}</span>`);
    if ((u._files || []).length) pills.push(`<span class="pill used">${u._files.length} file${u._files.length > 1 ? 's' : ''}</span>`);

    return `<div class="uprow ${EDITING === u.id ? 'sel' : ''}">
      <div class="main">
        <div class="ttl">${esc(u.upfitter)} — ${esc(u.title)}</div>
        <div class="meta">${esc(u.reference || 'no ref')} · ${esc(u.year || '')} · ${esc(fits || 'fits any chassis')}</div>
        <div class="acts">
          ${pills.join(' ')}
          <button class="link" data-edit="${esc(u.id)}">edit</button>
          <button class="link" data-copy="${esc(u.id)}">duplicate</button>
          ${(u._files || []).length ? `<button class="link" data-open="${esc(u._dir)}/${esc(u._files[0])}">pdf</button>` : ''}
          ${hosted() ? '' : `<button class="link" data-folder="${esc(u._dir)}">folder</button>`}
          <button class="link danger" data-del="${esc(u.id)}">delete</button>
        </div>
      </div>
      <div class="amt">${money(u.price)}</div>
    </div>`;
  }).join('');

  $$('[data-edit]').forEach(b => b.onclick = () => editUpfit(b.dataset.edit, false));
  $$('[data-copy]').forEach(b => b.onclick = () => editUpfit(b.dataset.copy, true));
  $$('[data-open]').forEach(b => b.onclick = () =>
    window.open('/api/file?path=' + encodeURIComponent(b.dataset.open), '_blank'));
  $$('#uplist [data-folder]').forEach(b => b.onclick = () => api('/api/reveal', { path: b.dataset.folder }));
  $$('[data-del]').forEach(b => b.onclick = async () => {
    if (!confirmInline(b, '')) return;
    try {
      const j = await api('/api/upfit-delete', { id: b.dataset.del });
      BOOT.upfits = j.upfits;
      if (EDITING === b.dataset.del) clearForm(); else renderLibrary();
      toast('Deleted');
    } catch (e) { toast(e.message, true); }
  });
}

/* ---------------- saved quotes ---------------- */
async function loadSaved() {
  try {
    const j = await api('/api/quotes');
    const l = j.quotes || [];
    $('#savedlist').innerHTML = l.length ? '<table><thead><tr><th>Quote</th><th>Customer</th><th>Vehicle</th><th class="r">Total</th><th></th></tr></thead><tbody>' +
      l.map(q => `<tr>
        <td><strong>${esc(q.quote_id)}</strong><div class="code">${esc((q.updated_at || '').replace('T', ' '))}</div></td>
        <td>${esc(q.customer?.org || q.customer?.name || '')}</td>
        <td>${esc(q.vehicle_title)}${q.qty > 1 ? ` <span class="code">×${q.qty}</span>` : ''}</td>
        <td class="r">${money(q.total)}</td>
        <td class="c"><button class="link" data-edit-q="${esc(q.quote_id)}">Edit</button>
          ${q.has_pdf ? `<button class="link" data-pdf="${esc(q.dir)}/quote.pdf">PDF</button> ` : ''}
          ${hosted() ? '' : `<button class="link" data-rev="${esc(q.dir)}">folder</button>`}</td>
      </tr>`).join('') + '</tbody></table>'
      : '<div class="empty">No saved quotes yet.</div>';
    $$('[data-edit-q]').forEach(b => b.onclick = () => recallQuote(b.dataset.editQ));
    $$('[data-pdf]').forEach(b => b.onclick = () => window.open('/api/file?path=' + encodeURIComponent(b.dataset.pdf), '_blank'));
    $$('#savedlist [data-rev]').forEach(b => b.onclick = () => api('/api/reveal', { path: b.dataset.rev }));
  } catch (e) { toast(e.message, true); }
}

/* Reopen a saved quote in the editor: add or remove upfits and options, then
   save or regenerate the PDF. Saving overwrites it; the old version goes to history/. */
async function recallQuote(id) {
  if (Q && $('#editor').style.display !== 'none' &&
      !confirm('You have a quote open in the editor. Replace it with ' + id + '?')) return;
  try {
    const j = await api('/api/quote?id=' + encodeURIComponent(id));
    Q = j.quote;
    SPEC = { vehicle_title: (Q.vehicle || {}).title || '' };
    SPEC_FILE = null;        // the original spec PDF is already stored with the quote
    $('.tabs button[data-tab="build"]').click();
    showEditor();
    $('#specname').textContent = Q.quote_id;
    $('#recalled').innerHTML =
      `<div class="note warn">Editing saved quote <strong>${esc(Q.quote_id)}</strong>
       (last saved ${esc((Q.updated_at || '').replace('T', ' '))}). Saving or generating the PDF
       replaces it; the previous version is kept in the quote's history folder.</div>`;
    renderSuggestedUpfits(j.applicable_upfits);
    toast('Editing ' + Q.quote_id);
  } catch (e) { toast(e.message, true); }
}

/* ---------------- who is using this copy ---------------- */
function hosted() {
  return !!(BOOT.identity && BOOT.identity.hosted);
}

function renderWhoami() {
  const me = BOOT.identity || {};
  const el = $('#whoami');
  if (hosted()) {
    el.textContent = (me.name || me.username || '') + (me.initials ? ' · ' + me.initials : '');
    el.title = 'Signed in as ' + (me.username || '');
    el.classList.remove('unset');
    $('#signout').style.display = '';
    $('#account').style.display = '';
  } else {
    el.textContent = me.initials ? 'You: ' + me.initials : 'Set your initials';
    el.classList.toggle('unset', !me.initials);
    $('#signout').style.display = 'none';
    $('#account').style.display = 'none';
  }
}

$('#whoami').onclick = () => {
  // Hosted: initials come from the login, so there is nothing to edit here.
  if (hosted()) return toast('Your initials come from your login.');
  openIdentity(false);
};

function openIdentity(force) {
  const me = BOOT.identity || {};
  $('#id_initials').value = me.initials || me.suggested_initials || '';
  $('#id_seller').innerHTML = '<option value="">— no default —</option>' +
    BOOT.sellers.map(x => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('');
  $('#id_seller').value = me.seller_id || '';
  $('#id_force').value = force ? '1' : '';
  $('#id_cancel').style.display = force ? 'none' : '';
  $('#identity').style.display = 'flex';
  setTimeout(() => $('#id_initials').focus(), 50);
}

$('#id_cancel').onclick = () => ($('#identity').style.display = 'none');

$('#id_save').onclick = async () => {
  const initials = $('#id_initials').value.trim();
  if (!initials) return toast('Enter your initials.', true);
  try {
    const j = await api('/api/identity', { initials, seller_id: $('#id_seller').value });
    BOOT.identity = j.identity;
    $('#identity').style.display = 'none';
    renderWhoami();
    toast('Your quotes will be numbered Q-' + new Date().getFullYear() + '-' + j.identity.initials + '-####');
  } catch (e) { toast(e.message, true); }
};

/* ---------------- boot ---------------- */
(async () => {
  try {
    BOOT = await api('/api/bootstrap');
    renderWhoami();
    clearForm();
    if (!hosted() && (!BOOT.identity || !BOOT.identity.configured)) openIdentity(true);
  } catch (e) { toast('Could not reach the server: ' + e.message, true); }
})();
