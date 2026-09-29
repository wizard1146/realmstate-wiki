// Client-side search over search-index.json (built by build.py). Loaded lazily on first focus.
(() => {
  const input = document.querySelector('.site-search__input');
  const list = document.querySelector('.site-search__results');
  if (!input || !list) return;
  let index = null;

  const load = () => index ||= fetch('search-index.json').then(r => r.json());
  input.addEventListener('focus', load, { once: true });

  const esc = s => s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  input.addEventListener('input', async () => {
    const q = input.value.trim().toLowerCase();
    if (q.length < 2) { list.hidden = true; return; }
    const docs = await load();
    const words = q.split(/\s+/);
    const hits = [];
    for (const d of docs) {
      const title = d.t.toLowerCase(), text = d.x.toLowerCase();
      if (!words.every(w => title.includes(w) || text.includes(w))) continue;
      const score = words.reduce((s, w) => s + (title === w ? 100 : title.startsWith(w) ? 40 : title.includes(w) ? 20 : 0) + (text.includes(w) ? 1 : 0), 0);
      const at = text.indexOf(words[0]);
      const snippet = at < 0 ? d.x.slice(0, 110) : d.x.slice(Math.max(0, at - 40), at + 90);
      hits.push({ d, score: d.s === 'Retired' ? score - 50 : score, snippet });   // retired pages rank below live ones
    }
    hits.sort((a, b) => b.score - a.score);
    list.innerHTML = hits.slice(0, 12).map(h =>
      `<li class="search-result"><a class="search-result__link" href="${h.d.u}"><span class="search-result__title">${esc(h.d.t)}${h.d.s ? ` <span class="status-badge status-badge--${h.d.s === 'Retired' ? 'retired' : 'needs-update'}">${esc(h.d.s.toLowerCase())}</span>` : ''}</span><span class="search-result__snippet">${esc(h.snippet)}</span></a></li>`
    ).join('') || '<li class="search-result search-result--empty">No matches.</li>';
    list.hidden = false;
  });

  document.addEventListener('click', e => { if (!e.target.closest('.site-search')) list.hidden = true; });
  input.addEventListener('keydown', e => { if (e.key === 'Escape') { list.hidden = true; input.blur(); } });
})();
