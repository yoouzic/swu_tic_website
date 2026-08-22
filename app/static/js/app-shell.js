document.addEventListener('DOMContentLoaded', () => {
    const shell = document.querySelector('.app-shell');
    if (shell) {
        const toggle = shell.querySelector('[data-shell-toggle]');
        const closeButtons = shell.querySelectorAll('[data-shell-close]');
        const sidebar = shell.querySelector('.app-sidebar');
        const frame = shell.querySelector('.app-frame');
        let previouslyFocused = null;

        const navigationTargets = () => sidebar
            ? Array.from(sidebar.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'))
            : [];
        const setOpen = (open) => {
            const wasOpen = shell.classList.contains('is-nav-open');
            shell.classList.toggle('is-nav-open', open);
            if (toggle) toggle.setAttribute('aria-expanded', String(open));
            document.body.classList.toggle('is-shell-nav-open', open);
            if (frame) {
                frame.inert = open;
                if (open) frame.setAttribute('aria-hidden', 'true');
                else frame.removeAttribute('aria-hidden');
            }
            if (open) {
                previouslyFocused = document.activeElement;
                navigationTargets()[0]?.focus();
            } else if (wasOpen && previouslyFocused?.focus) {
                previouslyFocused.focus();
                previouslyFocused = null;
            }
        };
        toggle?.addEventListener('click', () => setOpen(!shell.classList.contains('is-nav-open')));
        closeButtons.forEach((button) => button.addEventListener('click', () => setOpen(false)));
        sidebar?.querySelectorAll('a[href]').forEach((link) => link.addEventListener('click', () => setOpen(false)));
        document.addEventListener('keydown', (event) => {
            if (!shell.classList.contains('is-nav-open')) return;
            if (event.key === 'Escape') {
                event.preventDefault();
                setOpen(false);
                return;
            }
            if (event.key !== 'Tab') return;
            const targets = navigationTargets();
            if (!targets.length) return;
            const first = targets[0];
            const last = targets[targets.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        });
    }

    document.querySelectorAll('.page-header__overflow').forEach((menu) => {
        const toggle = menu.querySelector('[data-bs-toggle="dropdown"]');
        menu.addEventListener('hidden.bs.dropdown', () => {
            toggle.focus();
        });
    });

    document.querySelectorAll('[data-statistics-subnav]').forEach((nav) => {
        const frame = nav.closest('[data-statistics-subnav-frame]');
        const active = nav.querySelector('[aria-current="page"]');
        const syncEdges = () => {
            if (!frame) return;
            const maxScroll = Math.max(0, nav.scrollWidth - nav.clientWidth);
            frame.classList.toggle('statistics-subnav-frame--overflowing', maxScroll > 1);
            frame.classList.toggle('statistics-subnav-frame--at-start', nav.scrollLeft <= 1);
            frame.classList.toggle('statistics-subnav-frame--at-end', nav.scrollLeft >= maxScroll - 1);
        };
        const revealActive = () => {
            active?.scrollIntoView({ block: 'nearest', inline: 'center' });
            if (active) {
                const maxScroll = Math.max(0, nav.scrollWidth - nav.clientWidth);
                const targetLeft = active.offsetLeft - ((nav.clientWidth - active.clientWidth) / 2);
                nav.scrollTo({ left: Math.max(0, Math.min(maxScroll, targetLeft)), behavior: 'auto' });
            }
            requestAnimationFrame(syncEdges);
        };
        requestAnimationFrame(revealActive);
        window.addEventListener('load', revealActive, { once: true });
        if (document.fonts?.ready) document.fonts.ready.then(revealActive);
        window.setTimeout(revealActive, 250);
        nav.addEventListener('scroll', syncEdges, { passive: true });
        window.addEventListener('resize', revealActive);
    });

    const confirmDialog = document.getElementById('appConfirmDialog');
    if (confirmDialog && window.bootstrap) {
        const modal = bootstrap.Modal.getOrCreateInstance(confirmDialog);
        const title = confirmDialog.querySelector('#appConfirmTitle');
        const message = confirmDialog.querySelector('#appConfirmMessage');
        const input = confirmDialog.querySelector('#appConfirmInput');
        const inputLabel = confirmDialog.querySelector('#appConfirmInputLabel');
        const submit = confirmDialog.querySelector('[data-confirm-submit]');
        let resolveRequest = null;

        const closeRequest = (value) => {
            if (resolveRequest) resolveRequest(value);
            resolveRequest = null;
            modal.hide();
        };

        submit.addEventListener('click', () => {
            closeRequest(input.hidden ? true : input.value);
        });
        confirmDialog.addEventListener('hidden.bs.modal', () => {
            if (resolveRequest) closeRequest(false);
        });

        window.appConfirm = (text, heading = '确认操作') => new Promise((resolve) => {
            resolveRequest = resolve;
            title.textContent = heading;
            message.textContent = text;
            input.hidden = true;
            inputLabel.hidden = true;
            input.value = '';
            modal.show();
        });

        window.appPrompt = (text, initialValue = '', heading = '请输入内容') => new Promise((resolve) => {
            resolveRequest = resolve;
            title.textContent = heading;
            message.textContent = text;
            input.hidden = false;
            inputLabel.hidden = false;
            inputLabel.textContent = '内容';
            input.value = initialValue;
            modal.show();
            input.focus();
        });
    }

    document.addEventListener('click', (event) => {
        const close = event.target.closest('[data-app-message-dismiss]');
        if (close) close.closest('.app-message')?.remove();
    });

    window.appFeedback = (text, variant = 'info') => {
        const container = document.querySelector('.app-messages') || (() => {
            const created = document.createElement('div');
            created.className = 'app-messages';
            created.setAttribute('aria-live', 'polite');
            const main = document.querySelector('main');
            if (main) main.prepend(created);
            return created;
        })();
        const item = document.createElement('div');
        item.className = `app-message app-message--${variant}`;
        item.setAttribute('role', variant === 'error' || variant === 'danger' ? 'alert' : 'status');
        const content = document.createElement('span');
        content.textContent = text;
        const close = document.createElement('button');
        close.type = 'button';
        close.setAttribute('data-app-message-dismiss', '');
        close.setAttribute('aria-label', '关闭');
        close.innerHTML = '<i class="bi bi-x-lg" aria-hidden="true"></i>';
        item.append(content, close);
        container.appendChild(item);
        return item;
    };

    // Same-origin CSRF token injection for HTML forms, fetch and jQuery AJAX.
    const csrfMeta = document.querySelector('meta[name="csrf-token"]');
    const csrfToken = csrfMeta ? csrfMeta.getAttribute('content') : '';
    const isUnsafeMethod = (method) => ['POST', 'PUT', 'PATCH', 'DELETE'].includes(String(method || '').toUpperCase());
    const isSameOrigin = (url) => {
        try {
            return new URL(url, window.location.href).origin === window.location.origin;
        } catch (error) {
            return false;
        }
    };

    if (csrfToken) {
        document.querySelectorAll('form').forEach((form) => {
            const method = String(form.getAttribute('method') || 'get').toLowerCase();
            if (method === 'post' && !form.querySelector('input[name="csrf_token"]')) {
                const input = document.createElement('input');
                input.type = 'hidden';
                input.name = 'csrf_token';
                input.value = csrfToken;
                form.appendChild(input);
            }
        });

        const originalFetch = window.fetch;
        window.fetch = function (input, init) {
            const nextInit = init ? { ...init } : {};
            const method = nextInit.method || (input && input.method) || 'GET';
            const url = typeof input === 'string' ? input : (input && input.url) || '';
            if (isUnsafeMethod(method) && isSameOrigin(url)) {
                const headers = new Headers(nextInit.headers || {});
                headers.set('X-CSRFToken', csrfToken);
                nextInit.headers = headers;
            }
            return originalFetch.call(this, input, nextInit);
        };

        if (window.jQuery) {
            window.jQuery.ajaxSetup({
                beforeSend(xhr, settings) {
                    if (isUnsafeMethod(settings.type) && isSameOrigin(settings.url)) {
                        xhr.setRequestHeader('X-CSRFToken', csrfToken);
                    }
                },
            });
        }
    }
});
