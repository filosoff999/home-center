(() => {
  const stableReleases = 'https://github.com/ControlCenterSoft/home-center-stable/releases';
  const stableVersion = '0.56.0';
  const legacyStableVersion = '0.15.0';

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

    if (href.includes(`/home-center-stable/releases/tag/v${legacyStableVersion}`)) {
      link.href = href.replace(`v${legacyStableVersion}`, `v${stableVersion}`);
    }
  }

  // До статической синхронизации страниц не показываем пользователю устаревший Stable
  // и не направляем чистую установку 0.56.0 на устаревший runtime/updater path.
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    const value = node.nodeValue || '';
    const updated = value
      .replaceAll('home-center-0.15.0-linux-amd64.tar.gz', 'home-center-0.56.0-source.tar.gz')
      .replaceAll(legacyStableVersion, stableVersion)
      .replaceAll('0.15 stable', `${stableVersion} stable`)
      .replaceAll('Опубликован 9 сентября 2026 года.', 'Опубликован 12 сентября 2026 года.')
      .replaceAll('docs/INSTALL-AND-UPDATE.md', 'INSTALL.md');
    if (updated !== value) {
      node.nodeValue = updated;
    }
  }

  const downloadText = document.querySelector('#download p');
  if (downloadText) {
    downloadText.textContent = 'Для чистой установки Home Center 0.56.0 скачайте официальный исходный архив и SHA256SUMS из того же stable-релиза.';
  }

  const updateText = document.querySelector('#update p');
  if (updateText) {
    updateText.innerHTML = 'Перед обновлением сделайте backup, скачайте новый официальный Stable, проверьте <code>SHA256SUMS</code> и следуйте <code>UPGRADE.md</code> именно этого релиза. Для multi-node обновляйте по одному узлу и останавливайтесь при любой проблеме со здоровьем, репликацией или сервисами.';
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
