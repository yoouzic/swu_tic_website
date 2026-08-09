const reviewQueueState = 'swu-tic:review-queue';

function currentQueueUrl() {
    return `${window.location.pathname}${window.location.search}`;
}

function readReviewQueueState() {
    try {
        return JSON.parse(sessionStorage.getItem(reviewQueueState) || 'null');
    } catch (error) {
        return null;
    }
}

function restoreReviewQueueFields() {
    const params = new URLSearchParams(window.location.search);
    const fields = {
        start_date: 'startDate',
        end_date: 'endDate',
        time_filter_type: 'timeFilterType',
        status: 'statusFilter'
    };
    Object.entries(fields).forEach(([param, id]) => {
        const element = document.getElementById(id);
        const value = params.get(param);
        if (element && value !== null) {
            element.value = value;
        }
    });
}

function syncReviewQueueUrl() {
    const url = new URL(window.location.href);
    const values = {
        start_date: document.getElementById('startDate')?.value || '',
        end_date: document.getElementById('endDate')?.value || '',
        time_filter_type: document.getElementById('timeFilterType')?.value || 'created',
        status: document.getElementById('statusFilter')?.value || ''
    };

    Object.entries(values).forEach(([param, value]) => {
        if (value) {
            url.searchParams.set(param, value);
        } else {
            url.searchParams.delete(param);
        }
    });
    url.searchParams.delete('user_ids');
    document.querySelectorAll('.user-checkbox:checked').forEach((checkbox) => {
        url.searchParams.append('user_ids', checkbox.value);
    });
    url.searchParams.set('review_queue', '1');
    window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
    return currentQueueUrl();
}

function saveQueueState(formId) {
    try {
        sessionStorage.setItem(reviewQueueState, JSON.stringify({
            url: currentQueueUrl(),
            scrollY: window.scrollY,
            selectedFormId: String(formId || '')
        }));
    } catch (error) {
        // Session storage can be unavailable in private browsing. Navigation still works.
    }
}

function markReviewQueueSelection(formId, persist = true) {
    const selectedId = String(formId || '');
    document.querySelectorAll('[data-review-row-form-id]').forEach((row) => {
        const selected = row.dataset.reviewRowFormId === selectedId;
        row.classList.toggle('review-row--current', selected);
        if (selected) {
            row.setAttribute('aria-current', 'true');
        } else {
            row.removeAttribute('aria-current');
        }
    });
    if (persist && selectedId) {
        saveQueueState(selectedId);
    }
}

function restoreReviewQueueSelection() {
    const state = readReviewQueueState();
    if (!state || state.url !== currentQueueUrl()) {
        return;
    }

    const rows = Array.from(document.querySelectorAll('[data-review-row-form-id]'));
    if (rows.length === 0) {
        return;
    }

    const previousIndex = rows.findIndex((row) => row.dataset.reviewRowFormId === String(state.selectedFormId || ''));
    const candidatesAfter = previousIndex >= 0 ? rows.slice(previousIndex + 1) : rows;
    const candidatesBefore = previousIndex >= 0 ? rows.slice(0, previousIndex) : [];
    const nextRow = candidatesAfter.find((row) => row.dataset.reviewable === '1')
        || candidatesBefore.find((row) => row.dataset.reviewable === '1')
        || rows.find((row) => row.dataset.reviewable === '1')
        || rows[0];

    markReviewQueueSelection(nextRow.dataset.reviewRowFormId, false);
    requestAnimationFrame(() => {
        if (Number.isFinite(Number(state.scrollY))) {
            window.scrollTo({ top: Number(state.scrollY), behavior: 'auto' });
        }
        nextRow.scrollIntoView({ block: 'nearest', behavior: 'auto' });
    });
}

function clearReviewQueueUrl() {
    const url = new URL(window.location.href);
    ['start_date', 'end_date', 'time_filter_type', 'status', 'user_ids', 'review_queue'].forEach((param) => {
        url.searchParams.delete(param);
    });
    window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
    try {
        sessionStorage.removeItem(reviewQueueState);
    } catch (error) {
        // Ignore storage cleanup errors.
    }
}

function openFullReview(formId) {
    const returnUrl = syncReviewQueueUrl();
    saveQueueState(formId);
    const target = new URL(`/admin/review/form/${formId}`, window.location.origin);
    target.searchParams.set('return_to', returnUrl);
    window.location.assign(target.toString());
}

function handleReviewOpenClick(event) {
    const button = event.target.closest('[data-open-review]');
    if (!button) {
        return;
    }
    event.preventDefault();
    const formId = button.dataset.openReview;
    if (typeof window.reviewForm === 'function') {
        window.reviewForm(formId);
    } else {
        openFullReview(formId);
    }
}

function initReviewQueue() {
    restoreReviewQueueFields();
    document.addEventListener('click', handleReviewOpenClick);
}

window.currentQueueUrl = currentQueueUrl;
window.syncReviewQueueUrl = syncReviewQueueUrl;
window.saveQueueState = saveQueueState;
window.markReviewQueueSelection = markReviewQueueSelection;
window.restoreReviewQueueSelection = restoreReviewQueueSelection;
window.clearReviewQueueUrl = clearReviewQueueUrl;
window.openFullReview = openFullReview;

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initReviewQueue);
} else {
    initReviewQueue();
}
