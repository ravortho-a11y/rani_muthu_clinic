document.addEventListener('DOMContentLoaded', () => {
  const menu = document.querySelector('[data-menu]');
  const nav = document.querySelector('[data-nav]');
  if (menu && nav) menu.addEventListener('click', () => nav.classList.toggle('open'));
});
