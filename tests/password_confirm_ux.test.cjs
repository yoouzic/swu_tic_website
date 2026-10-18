const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const TEMPLATE = 'app/templates/admin/manage_departments.html';

function createElement(id) {
    const element = {
        id,
        tag: '',
        hidden: true,
        textContent: '',
        innerHTML: '',
        value: '',
        disabled: false,
        className: '',
        href: '',
        type: '',
        children: [],
        attributes: {},
        listeners: {},
        focused: false,
        classList: {
            set: new Set(),
            toggle(name, force) {
                if (force === undefined ? !this.set.has(name) : force) {
                    this.set.add(name);
                } else {
                    this.set.delete(name);
                }
            },
            contains(name) {
                return this.set.has(name);
            },
        },
        setAttribute(name, value) {
            this.attributes[name] = value;
        },
        addEventListener(name, handler) {
            (this.listeners[name] ||= []).push(handler);
        },
        appendChild(child) {
            this.children.push(child);
            return child;
        },
        replaceChildren() {
            this.children = [];
        },
        focus() {
            this.focused = true;
        },
        dispatch(name) {
            (this.listeners[name] || []).forEach((handler) => handler());
        },
    };
    return element;
}

function loadSubject() {
    const source = fs.readFileSync(TEMPLATE, 'utf8');
    const start = source.indexOf('// 密码确认对话框内的行内反馈');
    const end = source.indexOf('// 显示添加用户模态框内的错误信息', start);
    assert.ok(start >= 0 && end > start, 'password confirm helper block must exist');
    const code = source.slice(start, end);

    const nodes = new Map();
    [
        'confirmDeleteModal', 'confirmActionTitle', 'confirmDeleteBtn',
        'deletePassword', 'deleteMessage', 'confirmDeleteFeedback',
        'confirmDeleteAction', 'confirmDeleteForm',
    ].forEach((id) => nodes.set(id, createElement(id)));
    nodes.get('deletePassword').value = '';

    const created = [];
    const modal = {showCount: 0, hideCount: 0};
    const alerts = [];
    const departCalls = [];
    const context = {
        console,
        document: {
            getElementById(id) {
                return nodes.get(id) || null;
            },
            createElement(tag) {
                const element = createElement(`created-${tag}`);
                element.tag = tag;
                created.push(element);
                return element;
            },
        },
        bootstrap: {
            Modal: {
                getOrCreateInstance(element) {
                    return {
                        show() {
                            modal.showCount += 1;
                            element.classList.set.add('show');
                        },
                        hide() {
                            modal.hideCount += 1;
                            element.classList.set.delete('show');
                        },
                    };
                },
                getInstance() {
                    return null;
                },
            },
        },
        showAlert(message, level) {
            alerts.push({message, level});
        },
        departUser(userId, userName) {
            departCalls.push({userId, userName});
        },
    };
    vm.runInNewContext(code, context);
    return {context, nodes, created, modal, alerts, departCalls};
}

function flush() {
    return new Promise((resolve) => setImmediate(resolve));
}

test('departure failure keeps the password dialog open and reports the reason inline', async () => {
    const {context, nodes, created, modal, alerts} = loadSubject();
    const successCalls = [];
    const message = '该用户还有1份未结听课表，请先在审核队列完成审核或驳回，再办理离任。';
    const action = {
        label: '处理未结听课表',
        url: '/admin/review_forms?review_queue=1&user_ids=166',
    };

    context.showPasswordConfirm('确定将用户设为离任吗？', '离任', async () => ({
        success: false,
        code: 'outstanding_reviews',
        message,
        action,
    }), (result) => successCalls.push(result));

    assert.equal(modal.showCount, 1);
    nodes.get('deletePassword').value = 'correct-password';
    await nodes.get('confirmDeleteBtn').onclick();

    assert.equal(modal.hideCount, 0, 'dialog must stay open when the server rejects the action');
    const feedback = nodes.get('confirmDeleteFeedback');
    assert.equal(feedback.hidden, false);
    assert.equal(feedback.textContent, message);
    assert.equal(feedback.attributes.role, 'alert');
    assert.match(feedback.className, /activity-feedback--error/);

    const actionHolder = nodes.get('confirmDeleteAction');
    assert.equal(actionHolder.hidden, false);
    assert.equal(actionHolder.children.length, 1);
    const link = actionHolder.children[0];
    assert.equal(link.tag, 'a');
    assert.equal(link.href, action.url);
    assert.equal(link.textContent, action.label);

    assert.equal(alerts.length, 0);
    assert.equal(successCalls.length, 0);
    assert.equal(nodes.get('confirmDeleteBtn').disabled, false);
    assert.equal(nodes.get('confirmDeleteBtn').textContent, '确认离任');
    assert.ok(created.length >= 1);
});

test('successful departure closes the dialog, shows the page feedback and refreshes the list', async () => {
    const {context, nodes, modal, alerts} = loadSubject();
    const successCalls = [];
    const message = '用户已离任，后续不再参与活动或页面展示';

    context.showPasswordConfirm('确定将用户设为离任吗？', '离任', async () => ({
        success: true,
        message,
    }), (result) => successCalls.push(result));

    nodes.get('deletePassword').value = 'correct-password';
    await nodes.get('confirmDeleteBtn').onclick();

    assert.equal(modal.hideCount, 1);
    assert.deepEqual(alerts, [{message, level: 'success'}]);
    assert.equal(successCalls.length, 1);
    assert.equal(successCalls[0].success, true);
});

test('an empty password is rejected before any request is sent', async () => {
    const {context, nodes, modal} = loadSubject();
    let calls = 0;
    context.showPasswordConfirm('确定吗？', '离任', async () => {
        calls += 1;
        return {success: true, message: 'ok'};
    });
    nodes.get('deletePassword').value = '';
    await nodes.get('confirmDeleteBtn').onclick();

    assert.equal(calls, 0);
    assert.equal(modal.hideCount, 0);
    const feedback = nodes.get('confirmDeleteFeedback');
    assert.equal(feedback.hidden, false);
    assert.match(feedback.textContent, /请输入密码/);
    assert.equal(feedback.attributes.role, 'status');
});

test('submitting the password form by keyboard runs the same guarded action', async () => {
    const {context, nodes, created} = loadSubject();
    let calls = 0;
    let prevented = false;
    context.showPasswordConfirm('确定吗？', '删除', async () => {
        calls += 1;
        return {success: true, message: 'ok'};
    });
    nodes.get('deletePassword').value = 'correct-password';
    nodes.get('confirmDeleteForm').onsubmit({preventDefault() {
        prevented = true;
    }});
    await flush();
    await flush();

    assert.equal(prevented, true, 'Enter must not reload the page as a GET form submission');
    assert.equal(calls, 1);
});

test('delete failure offers switching to the departure flow for users with history', async () => {
    const {context, nodes, departCalls} = loadSubject();
    context.showPasswordConfirm('确定删除吗？', '删除', async () => ({
        success: false,
        code: 'has_lecture_forms',
        message: '无法删除用户：该用户有 3 条听课记录，历史记录必须保留，请改用「离任」。',
        action: {
            label: '改为办理离任',
            kind: 'depart',
            user_id: 166,
            user_name: '谭江波',
        },
    }));

    nodes.get('deletePassword').value = 'correct-password';
    await nodes.get('confirmDeleteBtn').onclick();

    const actionHolder = nodes.get('confirmDeleteAction');
    assert.equal(actionHolder.hidden, false);
    assert.equal(actionHolder.children.length, 1);
    const button = actionHolder.children[0];
    assert.equal(button.tag, 'button');
    assert.equal(button.textContent, '改为办理离任');

    assert.equal(button.listeners.click.length, 1);
    button.listeners.click[0]();
    nodes.get('confirmDeleteModal').dispatch('hidden.bs.modal');
    assert.deepEqual(departCalls, [{userId: 166, userName: '谭江波'}]);
});

test('switching from delete to departure waits for the closing transition', async () => {
    const {context, nodes, departCalls, modal} = loadSubject();
    context.showPasswordConfirm('确定删除吗？', '删除', async () => ({
        success: false,
        message: '该用户有历史听课记录，请改用离任。',
        action: {
            label: '改为办理离任',
            kind: 'depart',
            user_id: 166,
            user_name: '谭江波',
        },
    }));

    nodes.get('confirmDeleteModal').classList.set.add('show');
    nodes.get('deletePassword').value = 'correct-password';
    await nodes.get('confirmDeleteBtn').onclick();

    const button = nodes.get('confirmDeleteAction').children[0];
    button.listeners.click[0]();

    assert.equal(departCalls.length, 0, 'do not open a second dialog before the first one is hidden');
    assert.equal(modal.hideCount, 1);

    nodes.get('confirmDeleteModal').dispatch('hidden.bs.modal');
    assert.deepEqual(departCalls, [{userId: 166, userName: '谭江波'}]);
});

test('a failure that arrives after the dialog was closed still reaches the page', async () => {
    const {context, nodes, alerts} = loadSubject();
    context.showPasswordConfirm('确定吗？', '离任', async () => {
        nodes.get('confirmDeleteModal').classList.set.delete('show');
        return {success: false, message: '该用户还有1份未结听课表。'};
    });

    nodes.get('deletePassword').value = 'correct-password';
    await nodes.get('confirmDeleteBtn').onclick();

    assert.deepEqual(alerts, [{message: '该用户还有1份未结听课表。', level: 'error'}]);
});

test('password action normalizes non-JSON server errors into readable feedback', async () => {
    const {context} = loadSubject();

    context.fetch = async () => ({
        status: 500,
        json: async () => {
            throw new Error('not json');
        },
    });
    const failed = await context.postPasswordAction('/admin/api/users/1/depart', 'pw');
    assert.equal(failed.success, false);
    assert.match(failed.message, /HTTP 500/);

    context.fetch = async () => ({
        status: 401,
        json: async () => {
            throw new Error('not json');
        },
    });
    const expired = await context.postPasswordAction('/admin/api/users/1/depart', 'pw');
    assert.equal(expired.success, false);
    assert.match(expired.message, /登录状态已失效/);

    context.fetch = async () => ({
        status: 200,
        json: async () => ({success: true, message: 'ok'}),
    });
    const ok = await context.postPasswordAction('/admin/api/users/1/depart', 'pw');
    assert.deepEqual(ok, {success: true, message: 'ok'});
});
