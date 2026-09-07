(() => {
  const scriptSrc = document.currentScript?.src || '';
  let assetVersion = '';
  if (scriptSrc) {
    try {
      assetVersion = new URL(scriptSrc, window.location.href).searchParams.get('v') || '';
    } catch {
      assetVersion = '';
    }
  }

  const lightTheme = document.createElement('link');
  lightTheme.rel = 'stylesheet';
  lightTheme.href = `/assets/light.css${assetVersion ? `?v=${encodeURIComponent(assetVersion)}` : ''}`;
  document.head.appendChild(lightTheme);
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', '#f6fbf8');

  const header = document.querySelector('[data-header]');
  const menuButton = document.querySelector('[data-menu-button]');
  const menu = document.querySelector('[data-menu]');

  const syncHeader = () => header?.classList.toggle('scrolled', window.scrollY > 12);
  syncHeader();
  window.addEventListener('scroll', syncHeader, { passive: true });

  if (menuButton && menu) {
    menuButton.addEventListener('click', () => {
      const open = menu.classList.toggle('open');
      menuButton.setAttribute('aria-expanded', String(open));
    });
    menu.addEventListener('click', event => {
      if (event.target instanceof HTMLAnchorElement) {
        menu.classList.remove('open');
        menuButton.setAttribute('aria-expanded', 'false');
      }
    });
  }

  const items = [...document.querySelectorAll('.reveal')];
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          entry.target.classList.add('visible');
          observer.unobserve(entry.target);
        }
      }
    }, { threshold: 0.08 });
    items.forEach(item => observer.observe(item));
  } else {
    items.forEach(item => item.classList.add('visible'));
  }
})();
