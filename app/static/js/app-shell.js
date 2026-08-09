document.addEventListener('DOMContentLoaded', () => {
    const shell = document.querySelector('.app-shell');
    if (!shell) return;
    const toggle = shell.querySelector('[data-shell-toggle]');
    const closeButtons = shell.querySelectorAll('[data-shell-close]');
    const setOpen = (open) => {
        shell.classList.toggle('is-nav-open', open);
        if (toggle) toggle.setAttribute('aria-expanded', String(open));
    };
    toggle?.addEventListener('click', () => setOpen(!shell.classList.contains('is-nav-open')));
    closeButtons.forEach((button) => button.addEventListener('click', () => setOpen(false)));
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape') setOpen(false); });
});
