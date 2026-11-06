(function (window) {
    'use strict';
    const document = window.document;
    const version = '20261107-class-trace-2';
    const events = [];
    const openedAt = new Date().toISOString();
    let field;

    function limited(value) { return String(value).slice(0, 256); }

    function safeStackLine(line) {
        return limited(line.replace(/https?:\/\/[^\s)]+/g, url => {
            const position = (url.match(/:\d+:\d+$/) || [''])[0];
            const base = position ? url.slice(0, -position.length) : url;
            return base.split(/[?#]/)[0] + position;
        }));
    }

    function record(kind, details) {
        const safe = {...details};
        for (const key of ['value', 'before', 'after']) {
            if (typeof safe[key] !== 'string') continue;
            if (safe[key].length > 256) safe[key + '_length'] = safe[key].length;
            safe[key] = limited(safe[key]);
        }
        events.push({time: new Date().toISOString(), kind, ...safe});
        if (events.length > 80) events.shift();
    }

    function snapshot() {
        return {
            version, opened_at: openedAt, origin: limited(window.location.origin || ''),
            path: limited(window.location.pathname),
            browser: limited(window.navigator.userAgent),
            field: 'student_grade_class', value: field ? limited(field.value) : null,
            draft_protocol: field?.form?.dataset?.draftProtocol || 'legacy',
            value_length: field ? field.value.length : 0,
            events: events.slice(),
        };
    }

    function attach() {
        if (field) return true;
        const input = document.getElementById('student_grade_class');
        if (!input) return false;
        field = input;
        const descriptor = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
        if (descriptor && descriptor.get && descriptor.set) {
            try { Object.defineProperty(input, 'value', {
                configurable: true,
                get() { return descriptor.get.call(this); },
                set(value) {
                    const before = descriptor.get.call(this);
                    descriptor.set.call(this, value);
                    const after = descriptor.get.call(this);
                    if (before !== after) {
                        const stack = String(new Error().stack || '').split('\n').slice(2, 6)
                            .map(safeStackLine);
                        record('script-value', {before, after, stack});
                    }
                },
            }); } catch (error) {
                record('setter-unavailable', {reason: limited(error.name)});
            }
        }
        record('field-found', {value: input.value});
        for (const type of ['beforeinput', 'input', 'change', 'blur', 'compositionstart', 'compositionend']) {
            input.addEventListener(type, event => record(type, {
                value: input.value, trusted: event.isTrusted,
                input_type: event.inputType || '',
            }), true);
        }
        return true;
    }

    function showControls() {
        if (!attach()) return;
        const controls = document.createElement('div');
        controls.className = 'form-text';
        const label = document.createElement('span');
        label.textContent = '班级诊断已开启，仅记录此栏变化。';
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn btn-sm btn-outline-secondary ms-2';
        button.textContent = '复制诊断信息';
        button.addEventListener('click', async () => {
            const text = JSON.stringify(snapshot(), null, 2);
            try {
                await window.navigator.clipboard.writeText(text);
                button.textContent = '已复制';
            } catch (_) {
                const link = document.createElement('a');
                const url = window.URL.createObjectURL(new window.Blob([text], {type: 'application/json'}));
                link.href = url;
                link.download = 'class-input-diagnostics.json';
                link.click();
                window.setTimeout(() => window.URL.revokeObjectURL(url), 1000);
                button.textContent = '已下载诊断记录';
            }
        });
        controls.append(label, button);
        field.insertAdjacentElement('afterend', controls);
    }

    window.SwuClassInputDiagnostics = Object.freeze({snapshot});
    const observer = new window.MutationObserver(() => {
        if (attach()) observer.disconnect();
    });
    if (!attach()) observer.observe(document.documentElement, {childList: true, subtree: true});
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', showControls, {once: true});
    else showControls();
})(window);
