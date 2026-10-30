const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const modulePath = 'app/static/js/review-read-receipts.js';
const revision = '2026-10-16T08:31:12.123456';
const unread = () => ({id: 73, expected_form_updated_at: revision, department_read: {state: 'unread', read_at: null}});

function page(fetchImpl = async () => ({ok: true, json: async () => ({success: true, department_read: {state: 'read', read_at: '2026-10-16T08:35:00'}})})) {
    const calls = [], frames = [], badges = [], listeners = {};
    const node = () => ({
        children: [], dataset: {}, hidden: false, textContent: '', className: '', title: '',
        append(...children) {this.children.push(...children);},
        appendChild(child) {this.children.push(child);},
        remove() {this.removed = true;},
        setAttribute(name, value) {this[name] = value;},
        addEventListener(name, callback) {this[name] = callback;},
    });
    const container = Object.assign(node(), {
        isConnected: true, visible: true,
        getClientRects() {return this.visible ? [{}] : [];},
        querySelector() {return this.children.find(child => child.dataset.readReceiptFeedback !== undefined && !child.removed) || null;},
    });
    const document = {
        readyState: 'loading', visibilityState: 'visible',
        querySelector: selector => selector === 'meta[name="csrf-token"]' ? {content: 'csrf-token-for-test'} : null,
        querySelectorAll: selector => selector === '[data-read-receipt-badge]' ? badges : [],
        createElement: node,
        addEventListener: (name, callback) => {listeners[name] = callback;},
    };
    const context = vm.createContext({
        document, window: {}, console,
        requestAnimationFrame: callback => {frames.push(callback);},
        fetch: async (...args) => {calls.push(args); return fetchImpl(...args);},
    });
    vm.runInContext(fs.existsSync(modulePath) ? fs.readFileSync(modulePath, 'utf8') : '', context);
    const api = context.window.ReviewReadReceipts || {};
    const paint = async () => {
        while (frames.length) {
            frames.splice(0).forEach(callback => callback());
            await Promise.resolve();
        }
        await new Promise(resolve => setImmediate(resolve));
    };
    return {api, calls, paint, container, document, badges, listeners, node};
}

test('eligible badges are personal and preserve the formal status label', () => {
    const {api} = page();
    assert.equal(typeof api.renderBadge, 'function', 'read receipt badge rendering is implemented');
    const html = api.renderBadge(unread());
    assert.match(html, /我未阅/);
    assert.match(html, /data-read-receipt-form-id="73"/);
    assert.doesNotMatch(html, /部门已审核/);
    assert.equal(api.renderBadge({...unread(), department_read: null}), '');
    const read = api.renderBadge({...unread(), department_read: {state: 'read', read_at: '2026-10-16T08:35:00"<bad>'}});
    assert.match(read, /我已阅/);
    assert.match(read, /&quot;&lt;bad&gt;/);
    assert.doesNotMatch(read, /"<bad>/);
});

test('display acknowledgement sends the exact revision only after a paint and updates after server success', async () => {
    let resolveResponse;
    const pending = new Promise(resolve => {resolveResponse = resolve;});
    const p = page(() => pending);
    assert.equal(typeof p.api.acknowledgeDisplayed, 'function', 'display acknowledgements are implemented');
    const matching = Object.assign(p.node(), {dataset: {readReceiptFormId: '73', readReceiptRevision: revision}, textContent: '我未阅'});
    const newer = Object.assign(p.node(), {dataset: {readReceiptFormId: '73', readReceiptRevision: `${revision}9`}, textContent: '我未阅'});
    p.badges.push(matching, newer);
    let acknowledged = null;
    const result = p.api.acknowledgeDisplayed(unread(), p.container, {onRead: value => {acknowledged = value;}});
    assert.equal(p.calls.length, 0);
    await p.paint();
    assert.equal(p.calls.length, 1);
    assert.equal(matching.textContent, '我未阅');
    assert.equal(acknowledged, null);
    assert.equal(p.calls[0][0], '/admin/api/review/form/73/read-receipt');
    const request = p.calls[0][1];
    assert.equal(request.method, 'POST');
    assert.equal(request.headers['X-CSRFToken'], 'csrf-token-for-test');
    assert.deepEqual(JSON.parse(request.body), {expected_form_id: '73', expected_form_updated_at: revision});
    resolveResponse({ok: true, json: async () => ({success: true, department_read: {state: 'read', read_at: '2026-10-16T08:35:00'}})});
    await result;
    assert.equal(matching.textContent, '我已阅');
    assert.equal(newer.textContent, '我未阅', 'an acknowledgement cannot mark a newer list revision as read');
    assert.equal(acknowledged.state, 'read');
    assert.match(matching.title, /2026-10-16T08:35:00/);
});

test('hidden, superseded and ineligible details do not create a receipt', async () => {
    const p = page();
    assert.equal(typeof p.api.acknowledgeDisplayed, 'function', 'display acknowledgements are implemented');
    p.container.visible = false;
    const hidden = p.api.acknowledgeDisplayed(unread(), p.container);
    await p.paint(); await hidden;
    p.container.visible = true;
    let current = true;
    const superseded = p.api.acknowledgeDisplayed(unread(), p.container, {isCurrent: () => current});
    current = false;
    await p.paint(); await superseded;
    const ineligible = p.api.acknowledgeDisplayed({...unread(), department_read: null}, p.container);
    await p.paint(); await ineligible;
    assert.equal(p.calls.length, 0);
});

test('duplicate visible containers and repeat opens reuse the same acknowledgement', async () => {
    const p = page();
    assert.equal(typeof p.api.acknowledgeDisplayed, 'function', 'display acknowledgements are implemented');
    const first = p.api.acknowledgeDisplayed(unread(), p.container);
    const second = p.api.acknowledgeDisplayed(unread(), p.container);
    await p.paint(); await Promise.all([first, second]);
    const third = p.api.acknowledgeDisplayed(unread(), p.container);
    await p.paint(); await third;
    assert.equal(p.calls.length, 1);
});

test('failed writes keep unread and offer a working retry after a transient failure', async () => {
    let fail = true;
    const p = page(async () => {
        if (fail) throw new Error('offline');
        return {ok: true, json: async () => ({success: true, department_read: {state: 'read', read_at: '2026-10-16T08:35:00'}})};
    });
    assert.equal(typeof p.api.acknowledgeDisplayed, 'function', 'display acknowledgements are implemented');
    const badge = Object.assign(p.node(), {dataset: {readReceiptFormId: '73', readReceiptRevision: revision}, textContent: '我未阅'});
    p.badges.push(badge);
    const first = p.api.acknowledgeDisplayed(unread(), p.container);
    await p.paint(); await first;
    assert.equal(badge.textContent, '我未阅');
    const feedback = p.container.querySelector('[data-read-receipt-feedback]');
    assert.ok(feedback);
    assert.match(feedback.children[0].textContent, /已阅.*未保存.*重试/);
    const retry = feedback.children.find(child => child.textContent === '重试');
    assert.ok(retry);
    fail = false;
    retry.click();
    await p.paint();
    assert.equal(p.calls.length, 2);
    assert.equal(badge.textContent, '我已阅');
    assert.equal(feedback.removed, true);
});

test('a stale revision response never marks unread state as read and offers reload', async () => {
    const p = page(async () => ({ok: false, status: 409, json: async () => ({success: false})}));
    assert.equal(typeof p.api.acknowledgeDisplayed, 'function', 'display acknowledgements are implemented');
    let reloaded = false, acknowledged = false;
    const result = p.api.acknowledgeDisplayed(unread(), p.container, {onReload: () => {reloaded = true;}, onRead: () => {acknowledged = true;}});
    await p.paint(); await result;
    assert.equal(acknowledged, false);
    const feedback = p.container.querySelector('[data-read-receipt-feedback]');
    assert.match(feedback.children[0].textContent, /表单已更新/);
    feedback.children.find(child => child.textContent === '重新打开详情').click();
    assert.equal(reloaded, true);
});

test('raw detail pages opt into acknowledgement while injected detail fragments do not auto post', () => {
    const pageTemplate = fs.readFileSync('app/templates/admin/form_detail_page.html', 'utf8');
    const fragment = fs.readFileSync('app/templates/admin/form_detail_content.html', 'utf8');
    const reviewTemplate = fs.readFileSync('app/templates/admin/review_form.html', 'utf8');
    assert.match(pageTemplate, /data-read-receipt-auto/);
    assert.doesNotMatch(fragment, /data-read-receipt-auto/);
    assert.match(fragment, /data-read-receipt-revision/);
    assert.match(reviewTemplate, /ReviewReadReceipts\.acknowledgeDisplayed/);
});

const queueTemplate = fs.readFileSync('app/templates/admin/review_forms.html', 'utf8');
function queueFunction(name, template = queueTemplate) {
    const start = template.indexOf(`function ${name}(`);
    if (start < 0) return '';
    const next = template.indexOf('\nfunction ', start + 1);
    const end = template.indexOf('</script>', start);
    return template.slice(start, next < 0 ? end : Math.min(next, end));
}

function previewPage() {
    const elements = new Map(['formDetailModal', 'formDetailContent', 'reviewPreviewContent'].map(id => [id, {innerHTML: '', dataset: {}}]));
    const requests = new Map(), acknowledgements = [];
    const context = vm.createContext({
        console, reviewDetailRequestId: 0, currentFormGroups: [],
        document: {getElementById: id => elements.get(id)},
        bootstrap: {Modal: {getInstance: () => ({show() {}})}},
        window: {matchMedia: () => ({matches: false}), ReviewReadReceipts: {
            acknowledgeDisplayed: (...args) => {acknowledgements.push(args);},
        }},
        fetch: url => new Promise(resolve => {requests.set(url, resolve);}),
        setReviewFormPreviewError: (element, id) => {element.innerHTML = `error:${id}`;},
    });
    vm.runInContext(queueFunction('viewForm') + queueFunction('updateDepartmentReadReceipt'), context);
    const respond = async (id, html) => {
        requests.get(`/admin/form/${id}?format=json`)({ok: true, json: async () => ({success: true, html, form_id: id, expected_form_updated_at: revision, department_read: {state: 'unread'}})});
        await new Promise(resolve => setImmediate(resolve));
    };
    return {context, elements, acknowledgements, respond, open: id => vm.runInContext(`viewForm(${id})`, context)};
}

test('rapid preview clicks ignore stale responses and acknowledge only the displayed form once', async () => {
    const p = previewPage();
    p.open(73); p.open(74);
    await p.respond(74, 'detail:74');
    await p.respond(73, 'detail:73');
    assert.equal(p.elements.get('reviewPreviewContent').innerHTML, 'detail:74');
    assert.equal(p.acknowledgements.length, 1);
    assert.equal(p.acknowledgements[0][0].form_id, 74);
    assert.equal(p.acknowledgements[0][1], p.elements.get('reviewPreviewContent'));
});

test('queue cache updates only the acknowledged physical revision without reloading the list', () => {
    const p = previewPage();
    assert.equal(typeof p.context.updateDepartmentReadReceipt, 'function', 'queue cache read updates are implemented');
    p.context.currentFormGroups = [{forms: [unread(), {...unread(), expected_form_updated_at: 'newer'}]}];
    vm.runInContext(`updateDepartmentReadReceipt(73, ${JSON.stringify(revision)}, {state: 'read', read_at: 'now'})`, p.context);
    assert.equal(p.context.currentFormGroups[0].forms[0].department_read.state, 'read');
    assert.equal(p.context.currentFormGroups[0].forms[1].department_read.state, 'unread');
});

for (const [templateName, functionName] of [['view_forms', 'viewFormDetail'], ['submission_count_stats', 'viewForm']]) {
    function modalPage() {
        const template = fs.readFileSync(`app/templates/admin/${templateName}.html`, 'utf8');
        const content = {innerHTML: ''};
        const requests = new Map(), acknowledgements = [];
        const context = vm.createContext({
            formDetailRequestId: 0,
            document: {getElementById: id => id === 'formDetailContent' ? content : {}},
            bootstrap: {Modal: {getInstance: () => ({show() {}})}},
            window: {ReviewReadReceipts: {acknowledgeDisplayed: (...args) => {acknowledgements.push(args);}}},
            fetch: url => new Promise((resolve, reject) => {requests.set(url, {resolve, reject});}),
            escapeHtml: value => String(value),
        });
        vm.runInContext(queueFunction(functionName, template), context);
        const respond = async (id, result) => {
            const request = requests.get(`/admin/form/${id}?format=json`);
            if (result instanceof Error) request.reject(result);
            else request.resolve({ok: true, json: async () => ({success: true, html: `detail:${id}`, form_id: id, expected_form_updated_at: revision, department_read: {state: 'unread'}})});
            await new Promise(resolve => setImmediate(resolve));
        };
        return {template, content, requests, acknowledgements, respond, open: id => vm.runInContext(`${functionName}(${id})`, context)};
    }

    test(`${templateName} acknowledges the displayed modal detail and offers reload`, async () => {
        const p = modalPage();
        p.open(73);
        assert.equal(p.acknowledgements.length, 0, 'fetching alone does not count as reading');
        await p.respond(73);
        assert.equal(p.content.innerHTML, 'detail:73');
        assert.equal(p.acknowledgements.length, 1);
        assert.equal(p.acknowledgements[0][0].form_id, 73);
        assert.equal(p.acknowledgements[0][1], p.content);
        const options = p.acknowledgements[0][2];
        assert.equal(options.isCurrent(), true);
        options.onReload();
        assert.match(p.content.innerHTML, /正在加载/);
        assert.match(p.template, /js\/review-read-receipts\.js/);
    });

    test(`${templateName} ignores superseded modal responses and errors`, async () => {
        const p = modalPage();
        p.open(73); p.open(74);
        await p.respond(74); await p.respond(73);
        assert.equal(p.content.innerHTML, 'detail:74');
        assert.equal(p.acknowledgements.length, 1);
        assert.equal(p.acknowledgements[0][0].form_id, 74);
        const current = p.acknowledgements[0][2].isCurrent;
        p.open(75); p.open(76);
        assert.equal(current(), false);
        await p.respond(76); await p.respond(75, new Error('late network failure'));
        assert.equal(p.content.innerHTML, 'detail:76');
        assert.equal(p.acknowledgements.length, 2);
    });
}
