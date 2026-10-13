const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function load() {
    const source = path.join(__dirname, '../app/static/js/student-grade-class-input.js');
    assert.ok(fs.existsSync(source), 'the input formatter must be available');
    const window = {};
    vm.runInNewContext(fs.readFileSync(source, 'utf8'), {window});
    return window.createStudentClassFormatter;
}

test('formats on request and sends a change event for draft saving', async () => {
    const create = load();
    const events = [];
    const input = {value: '2024英语(师)1班', dispatchEvent: event => events.push(event.type)};
    const formatter = create(input, async (url, options) => {
        assert.equal(url, '/format');
        assert.equal(JSON.parse(options.body).value, input.value);
        return {ok: true, json: async () => ({success: true, value: '2024级英语（师范）01班'})};
    }, '/format', type => ({type}));
    assert.equal(await formatter.normalize(), true);
    assert.equal(input.value, '2024级英语（师范）01班');
    assert.deepEqual(events, ['input']);
});

test('late responses cannot overwrite further typing even if text returns to the old value', async () => {
    const create = load();
    let resolve;
    const input = {value: '原输入', dispatchEvent: () => assert.fail('stale response')};
    const formatter = create(input, () => new Promise(done => {resolve = done;}), '/format');
    const pending = formatter.normalize();
    formatter.invalidate();
    input.value = '原输入';
    resolve({ok: true, json: async () => ({success: true, value: '过期结果'})});
    assert.equal(await pending, false);
    assert.equal(input.value, '原输入');
});

test('network failure preserves the users text', async () => {
    const create = load();
    const input = {value: '创新实验班', dispatchEvent: () => assert.fail('failed response')};
    const formatter = create(input, async () => {throw new Error('offline');}, '/format');
    assert.equal(await formatter.normalize(), false);
    assert.equal(input.value, '创新实验班');
});
