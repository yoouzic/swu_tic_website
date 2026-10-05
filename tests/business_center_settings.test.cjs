const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('loading teaching settings preserves zero in input and summary', () => {
  const source = fs.readFileSync('app/templates/admin/_settings_teaching.html', 'utf8');
  const start = source.indexOf('    function renderSettings(');
  const end = source.indexOf('    function loadTeachingSettings(', start);
  const nodes = {};
  const context = {
    document: {getElementById(id) { return nodes[id] ||= {}; }},
    fields: () => [],
  };
  vm.runInNewContext(source.slice(start, end), context);
  context.renderSettings({required_submission_count: 0});
  assert.equal(nodes.previewRequiredSubmit.textContent, 0);
  assert.equal(nodes.requiredSubmission.value, 0);
  context.renderSettings({});
  assert.equal(nodes.requiredSubmission.value, 1);
});

async function importResult(payload) {
  const template = fs.readFileSync('app/templates/admin/_settings_imports.html', 'utf8');
  const source = template.slice(template.indexOf('<script>') + 8, template.indexOf('</script>'))
    .replace(/\{\{.*?\}\}/g, '/test-endpoint');
  const nodes = {};
  const node = key => nodes[key] ||= {
    textContent: '', style: {}, events: {},
    addEventListener(name, callback) { this.events[name] = callback; },
    querySelector(selector) { return node(key + ' ' + selector); },
    classList: {add() {}, remove() {}},
  };
  const notices = [];
  let requests = 0;
  const context = {
    document: {getElementById: node, querySelector: node, addEventListener() {}},
    window: {appFeedback: (...args) => notices.push(args)},
    FormData: class {append() {}},
    fetch: async () => ({json: async () => requests++ === 0 ? {success: true} : payload}),
  };
  vm.runInNewContext(source, context);
  nodes.fileInput.events.change({target: {files: [{name: 'test.xlsx'}]}});
  nodes.importBtn.events.click();
  await new Promise(resolve => setImmediate(resolve));
  return {nodes, notices};
}

test('import feedback treats row errors as failure and exposes details', async () => {
  const result = await importResult({success: true, message: '旧后端成功响应', stats: {
    total_rows: 1, teachers_added: 1, courses_added: 0,
    errors: ['第 2 行，教学班人数不是数值'],
  }});
  assert.equal(result.nodes.resultAlert.className, 'alert alert-danger');
  assert.match(result.nodes.resultContent.textContent, /第 2 行/);
  assert.equal(result.notices.at(-1)[1], 'error');
});

test('failed import renders detailed errors as plain text', async () => {
  const result = await importResult({success: false, message: '导入失败', stats: {
    errors: ['第 3 行，教学班人数：<b>未确认</b>'],
  }});
  assert.match(result.nodes.resultContent.textContent, /第 3 行.*<b>未确认<\/b>/);
  assert.equal(result.nodes.resultContent.innerHTML, undefined);
});

function uploadControls() {
  const template = fs.readFileSync('app/templates/admin/_settings_imports.html', 'utf8');
  const source = template.slice(template.indexOf('<script>') + 8, template.indexOf('</script>'))
    .replace(/\{\{.*?\}\}/g, '/test-endpoint');
  const nodes = {};
  let pickerRequests = 0;
  let pickerOpened = 0;
  const node = key => nodes[key] ||= {
    textContent: '', style: {}, events: {}, disabled: true,
    parent: null, clickInProgress: false,
    addEventListener(name, callback) { (this.events[name] ||= []).push(callback); },
    querySelector(selector) { return node(key + ' ' + selector); },
    classList: {add() {}, remove() {}},
    dispatch(name, extra = {}) {
      const event = {
        target: this, defaultPrevented: false, propagationStopped: false, ...extra,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() { this.propagationStopped = true; },
      };
      for (let current = this; current; current = current.parent) {
        event.currentTarget = current;
        for (const listener of current.events[name] || []) listener(event);
        if (event.propagationStopped) break;
      }
      return event;
    },
    click() {
      // Native input.click suppresses recursive activation while the click
      // dispatch is in progress; preventDefault on its bubbled event cancels
      // the picker activation. Reproduce both parts of that browser behavior.
      if (this.clickInProgress) return;
      this.clickInProgress = true;
      try {
        if (key === 'fileInput') pickerRequests += 1;
        const event = this.dispatch('click');
        if (key === 'fileInput' && !event.defaultPrevented) pickerOpened += 1;
        return event;
      } finally {
        this.clickInProgress = false;
      }
    },
  };
  const area = node('uploadArea');
  node('fileInput').parent = area;
  node('chooseScheduleFile').parent = area;
  node('uploadArea h3').parent = area;
  vm.runInNewContext(source, {
    document: {getElementById: node, querySelector: node, addEventListener() {}},
    window: {},
  });
  return {nodes, stats: () => ({pickerRequests, pickerOpened})};
}

test('file input activation is not cancelled by the surrounding upload area', () => {
  const controls = uploadControls();
  const event = controls.nodes.fileInput.click();
  assert.equal(event.defaultPrevented, false);
  assert.deepEqual(controls.stats(), {pickerRequests: 1, pickerOpened: 1});
});

test('both upload area and choose-file button open one picker', () => {
  for (const target of ['uploadArea', 'uploadArea h3', 'chooseScheduleFile']) {
    const controls = uploadControls();
    controls.nodes[target].click();
    assert.deepEqual(controls.stats(), {pickerRequests: 1, pickerOpened: 1}, target);
  }
});

test('Enter and Space activate only the upload area itself', () => {
  for (const key of ['Enter', ' ']) {
    const controls = uploadControls();
    const areaEvent = controls.nodes.uploadArea.dispatch('keydown', {key});
    assert.equal(areaEvent.defaultPrevented, true);
    assert.deepEqual(controls.stats(), {pickerRequests: 1, pickerOpened: 1});
  }
});

test('nested button keyboard activation is left to its native click', () => {
  for (const key of ['Enter', ' ']) {
    const controls = uploadControls();
    const event = controls.nodes.chooseScheduleFile.dispatch('keydown', {key});
    assert.equal(event.defaultPrevented, false);
    assert.deepEqual(controls.stats(), {pickerRequests: 0, pickerOpened: 0});
    controls.nodes.chooseScheduleFile.click();
    assert.deepEqual(controls.stats(), {pickerRequests: 1, pickerOpened: 1});
  }
});

test('dropping a schedule still selects it and enables import', () => {
  const controls = uploadControls();
  const event = controls.nodes.uploadArea.dispatch('drop', {
    dataTransfer: {files: [{name: '拖拽课表.xlsx'}]},
  });
  assert.equal(event.defaultPrevented, true);
  assert.equal(controls.nodes.importBtn.disabled, false);
  assert.match(controls.nodes['uploadArea h3'].textContent, /拖拽课表.xlsx/);
  assert.deepEqual(controls.stats(), {pickerRequests: 0, pickerOpened: 0});
});
