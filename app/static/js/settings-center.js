document.addEventListener('DOMContentLoaded', () => {
    const center = document.querySelector('.settings-center');
    if (!center) return;
    const activate = (name, updateUrl = true) => {
        const button = center.querySelector(`[data-settings-tab="${name}"]`);
        if (!button) return;
        center.dataset.activeSettingsTab = name;
        center.querySelectorAll('[data-settings-tab]').forEach((tab) => {
            const active = tab === button;
            tab.setAttribute('aria-selected', String(active));
            tab.tabIndex = active ? 0 : -1;
        });
        center.querySelectorAll('[data-settings-panel]').forEach((panel) => {
            const active = panel.dataset.settingsPanel === name;
            panel.hidden = !active;
            if (active && panel.dataset.initialized !== 'true') {
                panel.dataset.initialized = 'true';
                panel.dispatchEvent(new CustomEvent('settings:shown', {bubbles: true}));
            }
        });
        if (updateUrl) {
            const url = new URL(window.location.href);
            url.searchParams.set('tab', name);
            window.history.replaceState({}, '', url);
        }
    };
    center.querySelectorAll('[data-settings-tab]').forEach((button, index, buttons) => {
        button.addEventListener('click', () => activate(button.dataset.settingsTab));
        button.addEventListener('keydown', (event) => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const nextIndex = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
            buttons[nextIndex].focus();
            activate(buttons[nextIndex].dataset.settingsTab);
        });
    });
    activate(center.dataset.activeSettingsTab || 'teaching', false);
});
