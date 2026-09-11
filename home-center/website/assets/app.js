(() => {
  const stableReleases = 'https://github.com/ControlCenterSoft/home-center-stable/releases';
  const stableVersion = '0.22.3';
  const previousStableVersion = '0.15.0';

  // Старые публичные ссылки автоматически переводим в актуальный stable-канал.
  for (const link of document.querySelectorAll('a[href]')) {
    const href = link.getAttribute('href') || '';
    if (
      href.includes('github.com/ControlCenterSoft/home-center-free') ||
      href.includes('github.com/ControlCenterSoft/home-center/releases')
    ) {
      link.href = stableReleases;
      continue;
    }

    if (href.includes(`/home-center-stable/releases/tag/v${previousStableVersion}`)) {
      link.href = href.replace(`v${previousStableVersion}`, `v${stableVersion}`);
    }
  }

  // До статической синхронизации страниц не показываем пользователю устаревший Stable.
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    const value = node.nodeValue || '';
    const updated = value
      .replaceAll(previousStableVersion, stableVersion)
      .replaceAll('0.15 stable', `${stableVersion} stable`)
      .replaceAll('Опубликован 9 сентября 2026 года.', 'Опубликован 11 сентября 2026 года.');
    if (updated !== value) {
      node.nodeValue = updated;
    }
  }

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
