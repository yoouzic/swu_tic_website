(function (root) {
    'use strict';
    const MINIMUM_FEEDBACK_CHARACTERS = 50;

    function countFeedbackCharacters(value) {
        const text = String(value ?? '').replace(/[0-9#*]\uFE0F?\u20E3/gu, '');
        return (text.match(/[\p{L}\p{N}]/gu) || []).length;
    }

    const api = {MINIMUM_FEEDBACK_CHARACTERS, countFeedbackCharacters};
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    if (root) root.SwuFeedbackText = api;
}(typeof window !== 'undefined' ? window : null));
