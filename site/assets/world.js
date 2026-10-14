// The world now: a band under the header with the live game's state (the age and its day, players and houses,
// wars under way and how many began today, the leading state, the latest world news). Read from the game's public
// API (api.realmstate.net; the local game when the wiki is served locally), kept for a minute so moving between
// pages doesn't ask again. If the game can't be reached, the band stays hidden and nothing else changes.
(() => {
  const band = document.getElementById('world-now');
  if (!band) return;
  const local = ['localhost', '127.0.0.1'].includes(location.hostname);
  const API = local ? 'http://localhost:3300' : 'https://api.realmstate.net';
  const KEY = 'realmstate-world-now', KEEP_MS = 60000;
  const DAY = 86400000;
  const fmt = (n) => Number(n || 0).toLocaleString('en-GB');
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

  async function get(path) {
    const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), 6000);
    try {
      const r = await fetch(API + path, { signal: ctl.signal });
      if (!r.ok) throw new Error(r.status);
      return await r.json();
    } finally { clearTimeout(t); }
  }
  async function load() {
    try {
      const kept = JSON.parse(sessionStorage.getItem(KEY));
      if (kept && Date.now() - kept.at < KEEP_MS) return kept.data;
    } catch {}
    const [age, world, wars, news] = await Promise.all([get('/age'), get('/world'), get('/wars'), get('/world/news?limit=1').catch(() => [])]);
    const data = { age, houses: world.houses, wars, news };
    try { sessionStorage.setItem(KEY, JSON.stringify({ at: Date.now(), data })); } catch {}
    return data;
  }

  // A state by name (the age's standings carry them), else its address.
  let names = {};
  const st = (s) => (s ? esc(names[`${s.realm}:${s.state}`] || `State ${s.realm}:${s.state}`) : 'a state');
  /** The latest world news, as a short line (the game's own wording, shortened). */
  function line(n) {
    switch (n.type) {
      case 'war_declared': return `${st(n.by)} declared war on ${st(n.on)}`;
      case 'war_ended': return `The war between ${st(n.a)} and ${st(n.b)} ended${n.winner ? `: ${st(n.winner)} won` : ''}`;
      case 'ceasefire_agreed': return `${st(n.a)} and ${st(n.b)} agreed a ceasefire`;
      case 'ceasefire_broken': return `${st(n.by)} broke a ceasefire`;
      case 'ceasefire_ended': return `The ceasefire between ${st(n.a)} and ${st(n.b)} ended`;
      case 'truth_announced': return `${st(n.by)} uncovered a Truth: ${esc(n.truth)}`;
      case 'dragon_sighted': return `${n.dragon ? esc(n.dragon) : 'A dragon'} struck in ${esc(n.realm_name || `realm ${n.realm}`)}`;
      case 'market_trade': return `A ${n.side === 'buy' ? 'buyer took' : 'seller sold'} ${fmt(n.units)} ${esc(n.material)} at ${fmt(n.price)} gold`;
      case 'market_swing': return `${esc(n.material)} ${n.to > n.from ? 'rose' : 'fell'} from ${fmt(n.from)} to ${fmt(n.to)} gold`;
      default: return '';
    }
  }

  function draw({ age, houses, wars, news, more = {} }) {
    names = { ...Object.fromEntries((age.standings || []).map((s) => [`${s.state.realm}:${s.state.state}`, s.name])), ...more };
    const parts = [];
    if (!age.started) parts.push(`<b>${esc(age.name)}</b> hasn't begun yet`);
    else if (age.ended) parts.push(`<b>${esc(age.name)}</b> has ended`);
    else {
      const day = Math.floor((age.ticks * age.tick_ms) / DAY) + 1, days = Math.ceil((age.age_ticks * age.tick_ms) / DAY);
      parts.push(`<b>${esc(age.name)}</b>, day ${fmt(day)} of ${fmt(days)}`);
    }
    parts.push(`${fmt(age.people)} player${age.people === 1 ? '' : 's'}, ${fmt(houses)} houses`);
    const all = wars.wars || [], today = all.filter((w) => w.started_tick >= (wars.ticks ?? age.ticks) - DAY / age.tick_ms).length;
    parts.push(`${fmt(all.length)} war${all.length === 1 ? '' : 's'}${today ? ` (${fmt(today)} new today)` : ''}`);
    const lead = (age.standings || [])[0];
    if (lead && age.started) parts.push(`Leading: ${esc(lead.name || `State ${lead.state.realm}:${lead.state.state}`)}`);
    const latest = (news || []).map(line).find(Boolean);
    if (latest) parts.push(`<span class="world-now__latest">Latest: ${latest}</span>`);
    band.querySelector('.world-now__parts').innerHTML = parts.map((p) => `<span>${p}</span>`).join('');
    band.hidden = false;
  }

  // A state the standings don't list (one with few houses) is named from its realm, for the latest news line.
  async function nameTheRest(data) {
    const known = new Set((data.age.standings || []).map((s) => `${s.state.realm}:${s.state.state}`));
    const n = (data.news || [])[0] || {};
    const realms = [...new Set([n.by, n.on, n.a, n.b, n.winner].filter((s) => s && !known.has(`${s.realm}:${s.state}`) && !(data.more || {})[`${s.realm}:${s.state}`]).map((s) => s.realm))];
    if (!realms.length) return;
    const got = await Promise.all(realms.map((r) => get(`/realms/${r}`).catch(() => null)));
    data.more = { ...(data.more || {}) };
    got.forEach((x, i) => { for (const s of x?.states || []) if (s.name) data.more[`${realms[i]}:${s.state}`] = s.name; });
    try { sessionStorage.setItem(KEY, JSON.stringify({ at: Date.now(), data })); } catch {}
    draw(data);
  }

  load().then((data) => { draw(data); nameTheRest(data); }, () => { band.hidden = true; });
})();
