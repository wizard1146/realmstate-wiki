// Sidebar drawer (phones, < 768px). From 768px up the sidebar is a persistent rail and none of this runs.
// Nothing else on the site needs JavaScript except search.
(() => {
  const openBtn = document.getElementById('nav-open-button');
  const closeBtn = document.getElementById('nav-close-button');
  const backdrop = document.getElementById('site-backdrop');
  const sidebar = document.getElementById('site-sidebar');
  if (!openBtn || !sidebar) return;
  const set = open => {
    document.body.classList.toggle('sidebar-open', open);
    openBtn.setAttribute('aria-expanded', open);
    if (backdrop) backdrop.hidden = !open;
    if (open) closeBtn?.focus(); else if (document.activeElement && sidebar.contains(document.activeElement)) openBtn.focus();
  };
  openBtn.addEventListener('click', () => set(true));
  closeBtn?.addEventListener('click', () => set(false));
  backdrop?.addEventListener('click', () => set(false));
  sidebar.addEventListener('click', e => { if (e.target.closest('.nav-group a')) set(false); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') set(false); });
  window.matchMedia('(min-width: 48rem)').addEventListener('change', e => { if (e.matches) set(false); });
})();

// Remember the nav's scroll position for the next page (restored by the inline script in template.html).
(() => {
  const nav = document.getElementById('site-nav');
  if (!nav) return;
  const save = () => { try { sessionStorage.setItem('realmstate-nav-scroll', nav.scrollTop); } catch {} };
  nav.addEventListener('scroll', save, { passive: true });
  addEventListener('pagehide', save);
})();

// Below 80rem the table of contents is a block above the article; start it collapsed so it doesn't push the text down.
(() => {
  const toc = document.getElementById('toc');
  if (toc && matchMedia('(max-width: 79.99rem)').matches) toc.removeAttribute('open');
})();

// Scroll-to-top button: appears once you are a screenful or so down the page.
(() => {
  const btn = document.getElementById('scroll-top');
  if (!btn) return;
  let queued = false;
  const update = () => { btn.classList.toggle('is-visible', window.scrollY > 400); queued = false; };
  addEventListener('scroll', () => { if (!queued) { queued = true; requestAnimationFrame(update); } }, { passive: true });
  btn.addEventListener('click', () => {
    const calm = matchMedia('(prefers-reduced-motion: reduce)').matches;
    scrollTo({ top: 0, behavior: calm ? 'auto' : 'smooth' });
  });
  update();
})();

// Title in the header (desktop): show the condensed title in the header once the big page title has scrolled out from under it.
// Enabled by class="title-in-header" on <html>; without it none of this runs.
(() => {
  if (!document.documentElement.classList.contains('title-in-header')) return;
  const header = document.getElementById('site-header');
  const title = document.querySelector('.page__title');
  if (!header || !title || title.classList.contains('page__title--hidden')) return;   // pages with a hidden title (home) have nothing to condense
  const show = entry => header.classList.toggle('is-title-shown', !entry.isIntersecting && entry.boundingClientRect.bottom < header.offsetHeight + 1);
  new IntersectionObserver(entries => show(entries[entries.length - 1]),
    { rootMargin: `-${header.offsetHeight}px 0px 0px 0px`, threshold: 0 }).observe(title);
})();
