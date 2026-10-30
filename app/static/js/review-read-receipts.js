(function () {
    'use strict';

    const pending = new Map();
    const acknowledged = new Map();

    function identity(form) {
        return {
            id: String(form.form_id ?? form.id ?? ''),
            revision: String(form.expected_form_updated_at ?? ''),
        };
    }

    function receiptKey(form) {
        const value = identity(form);
        return JSON.stringify([value.id, value.revision]);
    }

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, character => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        })[character]);
    }

    function badgeClass(receipt) {
        return `badge ms-1 ${receipt.state === 'read' ? 'bg-light text-secondary border' : 'bg-warning-subtle text-dark border'}`;
    }

    function receiptTitle(receipt) {
        return receipt.state === 'read'
            ? (receipt.read_at ? `我已阅 · ${receipt.read_at}` : '我已阅（本人已审核）')
            : '此版本尚未记录为我已阅';
    }

    function renderBadge(form) {
        if (!form.department_read) return '';
        const receipt = acknowledged.get(receiptKey(form)) || form.department_read;
        const value = identity(form);
        return `<span class="${badgeClass(receipt)}" data-read-receipt-badge data-read-receipt-form-id="${escapeHtml(value.id)}" data-read-receipt-revision="${escapeHtml(value.revision)}" title="${escapeHtml(receiptTitle(receipt))}">${receipt.state === 'read' ? '我已阅' : '我未阅'}</span>`;
    }

    function updateBadges(form, receipt) {
        const value = identity(form);
        document.querySelectorAll('[data-read-receipt-badge]').forEach(badge => {
            if (badge.dataset.readReceiptFormId !== value.id || badge.dataset.readReceiptRevision !== value.revision) return;
            badge.textContent = receipt.state === 'read' ? '我已阅' : '我未阅';
            badge.className = badgeClass(receipt);
            badge.title = receiptTitle(receipt);
        });
    }

    function isDisplayed(container, options) {
        return container && container.isConnected !== false
            && document.visibilityState !== 'hidden'
            && container.getClientRects().length > 0
            && (!options.isCurrent || options.isCurrent());
    }

    function afterPaint() {
        return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    }

    function clearFeedback(container) {
        container.querySelector('[data-read-receipt-feedback]')?.remove();
    }

    function showFailure(form, container, options, stale) {
        clearFeedback(container);
        const feedback = document.createElement('div');
        feedback.className = 'alert alert-warning py-2 my-2 small d-flex flex-wrap align-items-center gap-2';
        feedback.dataset.readReceiptFeedback = '';
        feedback.setAttribute('role', 'status');
        const message = document.createElement('span');
        message.textContent = stale ? '表单已更新，请重新打开详情后重试。' : '已阅标记未保存，请重试。';
        feedback.appendChild(message);
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.className = 'btn btn-sm btn-outline-secondary';
        retry.textContent = stale && options.onReload ? '重新打开详情' : '重试';
        retry.addEventListener('click', () => {
            clearFeedback(container);
            if (stale && options.onReload) options.onReload();
            else acknowledgeDisplayed(form, container, options);
        });
        feedback.appendChild(retry);
        container.appendChild(feedback);
    }

    async function saveReceipt(form) {
        const value = identity(form);
        const token = document.querySelector('meta[name="csrf-token"]')?.content;
        const headers = {'Content-Type': 'application/json'};
        if (token) headers['X-CSRFToken'] = token;
        const response = await fetch(`/admin/api/review/form/${encodeURIComponent(value.id)}/read-receipt`, {
            method: 'POST',
            headers,
            body: JSON.stringify({expected_form_id: value.id, expected_form_updated_at: value.revision}),
        });
        const result = await response.json();
        if (!response.ok || !result.success || result.department_read?.state !== 'read') {
            const error = new Error('Read receipt was not saved');
            error.stale = response.status === 409;
            throw error;
        }
        acknowledged.set(receiptKey(form), result.department_read);
        return result.department_read;
    }

    async function acknowledgeDisplayed(form, container, options = {}) {
        if (!form.department_read || !identity(form).id) return null;
        await afterPaint();
        if (!isDisplayed(container, options)) return null;
        const key = receiptKey(form);
        if (form.department_read.state === 'read') {
            updateBadges(form, form.department_read);
            return form.department_read;
        }
        try {
            let receipt = acknowledged.get(key);
            if (!receipt) {
                if (!pending.has(key)) {
                    pending.set(key, saveReceipt(form).finally(() => pending.delete(key)));
                }
                receipt = await pending.get(key);
            }
            form.department_read = receipt;
            updateBadges(form, receipt);
            if (isDisplayed(container, options)) clearFeedback(container);
            if (options.onRead) options.onRead(receipt);
            return receipt;
        } catch (error) {
            if (isDisplayed(container, options)) showFailure(form, container, options, error.stale === true);
            return null;
        }
    }

    function initializeStandalone() {
        document.querySelectorAll('[data-read-receipt-auto] [data-read-receipt-detail]').forEach(container => {
            const state = container.dataset.readReceiptState;
            if (!state) return;
            acknowledgeDisplayed({
                id: container.dataset.readReceiptFormId,
                expected_form_updated_at: container.dataset.readReceiptRevision,
                department_read: {state, read_at: container.dataset.readReceiptReadAt || null},
            }, container, {onReload: () => window.location.reload()});
        });
    }

    window.ReviewReadReceipts = {renderBadge, acknowledgeDisplayed, updateBadges};
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initializeStandalone);
    else initializeStandalone();
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'hidden') initializeStandalone();
    });
})();
