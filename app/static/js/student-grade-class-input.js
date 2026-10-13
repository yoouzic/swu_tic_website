(function (window) {
    'use strict';

    function createStudentClassFormatter(input, fetcher, url, eventFactory) {
        let revision = 0;
        return {
            invalidate() { revision += 1; },
            async normalize() {
                const value = input.value;
                const requestedRevision = ++revision;
                if (!value.trim()) return false;
                try {
                    const response = await fetcher(url, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({value})
                    });
                    const result = await response.json();
                    if (!response.ok || !result.success || typeof result.value !== 'string' ||
                        requestedRevision !== revision || input.value !== value) return false;
                    if (result.value !== value) {
                        input.value = result.value;
                        input.dispatchEvent(eventFactory ? eventFactory('input') :
                            new window.Event('input', {bubbles: true}));
                    }
                    return true;
                } catch (_) {
                    // Keep the user's input; draft/submission also use the same server formatter.
                    return false;
                }
            }
        };
    }

    window.createStudentClassFormatter = createStudentClassFormatter;
    if (!window.document) return;
    function initialize() {
        const input = window.document.getElementById('student_grade_class');
        if (!input || !input.dataset.formatUrl) return;
        const formatter = createStudentClassFormatter(input,
            (...args) => window.fetch(...args), input.dataset.formatUrl);
        input.addEventListener('input', formatter.invalidate);
        input.addEventListener('blur', formatter.normalize);
        formatter.normalize();
    }
    if (window.document.readyState === 'loading') {
        window.document.addEventListener('DOMContentLoaded', initialize);
    } else {
        initialize();
    }
})(window);
