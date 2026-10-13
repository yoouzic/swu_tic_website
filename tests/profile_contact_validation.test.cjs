const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const template = fs.readFileSync('app/templates/user/edit_profile.html', 'utf8');
const script = template.slice(template.indexOf('<script>') + 8, template.indexOf('</script>'));

function fixture({
  editableFields = ['name', 'gender', 'grade', 'college', 'major', 'phone', 'qq'],
  missingLockedContactHints = false,
} = {}) {
  const editable = new Set(editableFields);
  const nodes = {};
  const notices = [];
  let focusedId = null;

  function node(id, value = '') {
    const attributes = new Map();
    const classes = new Set();
    const listeners = new Map();
    const element = {
      id, name: id, value, hidden: false, textContent: '',
      readOnly: false, disabled: false, required: false,
      parentElement: null, style: {}, validationMessage: '',
      classList: {
        add(...values) { values.forEach(value => classes.add(value)); },
        remove(...values) { values.forEach(value => classes.delete(value)); },
        contains(value) { return classes.has(value); },
        toggle(value, force) {
          const enabled = force === undefined ? !classes.has(value) : Boolean(force);
          if (enabled) classes.add(value); else classes.delete(value);
          return enabled;
        },
      },
      get className() { return Array.from(classes).join(' '); },
      set className(value) {
        classes.clear();
        String(value).split(/\s+/).filter(Boolean).forEach(value => classes.add(value));
      },
      setAttribute(name, value) { attributes.set(name, String(value)); },
      getAttribute(name) { return attributes.get(name) ?? null; },
      removeAttribute(name) { attributes.delete(name); },
      hasAttribute(name) { return attributes.has(name); },
      setCustomValidity(message) { this.validationMessage = message; },
      focus() { focusedId = id; },
      addEventListener(type, listener, options = false) {
        const capture = typeof options === 'boolean' ? options : Boolean(options.capture);
        const entries = listeners.get(type) || [];
        entries.push({listener, capture});
        listeners.set(type, entries);
      },
      dispatch(type) {
        const event = {
          type, target: this, currentTarget: this, defaultPrevented: false,
          preventDefault() { this.defaultPrevented = true; },
        };
        const ancestors = [];
        for (let parent = this.parentElement; parent; parent = parent.parentElement) {
          ancestors.push(parent);
        }
        const invoke = (target, captureOnly) => {
          event.currentTarget = target;
          for (const entry of target.listeners.get(type) || []) {
            if (captureOnly === undefined || entry.capture === captureOnly) {
              entry.listener.call(target, event);
            }
          }
        };
        ancestors.slice().reverse().forEach(parent => invoke(parent, true));
        invoke(this);
        if (type !== 'invalid' && type !== 'blur') {
          ancestors.forEach(parent => invoke(parent, false));
        }
        return event;
      },
      listeners,
    };
    nodes[id] = element;
    return element;
  }

  const form = node('editProfileForm');
  const initial = {
    name: '测试者', gender: '女', grade: '2026级',
    college: '测试学院', major: '测试专业',
    phone: '13800138000', qq: '12345',
  };
  Object.entries(initial).forEach(([id, value]) => {
    const field = node(id, value);
    field.classList.add('form-control');
    field.parentElement = form;
    field.readOnly = !editable.has(id);
    field.required = editable.has(id) && id !== 'qq';
  });
  for (const [field, prefix] of [['phone', 'Phone'], ['qq', 'Qq']]) {
    if (missingLockedContactHints && !editable.has(field)) continue;
    const hint = node(`profile${prefix}Hint`);
    hint.textContent = field === 'phone' ? '请输入11位手机号码。' : '选填，5–11位数字。';
    const error = node(`profile${prefix}Error`);
    error.hidden = true;
    nodes[field].setAttribute('aria-describedby', hint.id);
  }
  form.elements = Object.values(initial).map((_, index) => nodes[Object.keys(initial)[index]]);

  const context = {
    document: {
      getElementById(id) { return nodes[id] || null; },
      querySelector(selector) { return selector.startsWith('#') ? nodes[selector.slice(1)] || null : null; },
    },
    window: {appFeedback: (...args) => notices.push(args)},
  };
  const source = script.replace(/\{\{\s*editable_fields\s*\|\s*list\s*\|\s*tojson\s*\}\}/g, JSON.stringify(editableFields));
  vm.runInNewContext(source, context, {filename: 'edit_profile.html<script>'});
  return {nodes, notices, context, focusedId: () => focusedId};
}

function assertError(f, field, message) {
  const prefix = field === 'phone' ? 'Phone' : 'Qq';
  const input = f.nodes[field];
  const error = f.nodes[`profile${prefix}Error`];
  assert.equal(input.classList.contains('is-invalid'), true, `${field} needs an invalid style`);
  assert.equal(input.getAttribute('aria-invalid'), 'true', `${field} needs an accessible error state`);
  assert.equal(input.getAttribute('aria-describedby'), error.id, `${field} must describe its error`);
  assert.equal(error.hidden, false, `${field} error must be visible`);
  assert.equal(error.textContent, message);
}

function assertClear(f, field) {
  const prefix = field === 'phone' ? 'Phone' : 'Qq';
  const input = f.nodes[field];
  assert.equal(input.classList.contains('is-invalid'), false);
  assert.notEqual(input.getAttribute('aria-invalid'), 'true');
  assert.equal(input.getAttribute('aria-describedby'), `profile${prefix}Hint`);
  assert.equal(f.nodes[`profile${prefix}Error`].hidden, true);
}

test('first partial contact input stays neutral while the user is typing', () => {
  const f = fixture();
  for (const [field, value] of [['phone', '138'], ['qq', '12']]) {
    f.nodes[field].value = value;
    f.nodes[field].dispatch('input');
    assertClear(f, field);
  }
  assert.equal(f.notices.length, 0);
});

test('leaving a malformed phone shows the correction next to the field and links it accessibly', () => {
  const f = fixture();
  f.nodes.phone.value = '12800138000';
  f.nodes.phone.dispatch('blur');
  assertError(f, 'phone', '请输入有效的11位手机号码');
});

test('empty phone stays quiet on blur but native required validation explains what to fill', () => {
  const f = fixture();
  f.nodes.phone.value = '';
  f.nodes.phone.dispatch('blur');
  assertClear(f, 'phone');
  f.nodes.phone.dispatch('invalid');
  assertError(f, 'phone', '请输入手机号码');
});

test('submit stops on an invalid phone and moves focus to that field', () => {
  const f = fixture();
  f.nodes.phone.value = '1380013800';
  const event = f.nodes.editProfileForm.dispatch('submit');
  assert.equal(event.defaultPrevented, true);
  assert.equal(f.focusedId(), 'phone');
  assertError(f, 'phone', '请输入有效的11位手机号码');
});

test('editing an already invalid phone rechecks it and removes the error when corrected', () => {
  const f = fixture();
  f.nodes.phone.value = '138';
  f.nodes.phone.dispatch('blur');
  assertError(f, 'phone', '请输入有效的11位手机号码');
  f.nodes.phone.value = '138001';
  f.nodes.phone.dispatch('input');
  assertError(f, 'phone', '请输入有效的11位手机号码');
  f.nodes.phone.value = ' 13800138000 ';
  f.nodes.phone.dispatch('input');
  assertClear(f, 'phone');
});

test('QQ checks its numeric bounds on blur and optional clearing restores the hint', () => {
  const f = fixture();
  for (const value of ['1234', '01234', '123456789012', '123ab']) {
    f.nodes.qq.value = value;
    f.nodes.qq.dispatch('blur');
    assertError(f, 'qq', '请输入有效的QQ号码（5–11位数字）');
  }
  for (const value of ['12345', '12345678901']) {
    f.nodes.qq.value = value;
    f.nodes.qq.dispatch('input');
    f.nodes.qq.dispatch('blur');
    assertClear(f, 'qq');
  }
  f.nodes.qq.value = '123';
  f.nodes.qq.dispatch('blur');
  assertError(f, 'qq', '请输入有效的QQ号码（5–11位数字）');
  f.nodes.qq.value = '';
  f.nodes.qq.dispatch('input');
  assertClear(f, 'qq');
  assert.equal(f.nodes.editProfileForm.dispatch('submit').defaultPrevented, false);
});

test('submit rejects a filled malformed QQ and focuses QQ when the phone is valid', () => {
  const f = fixture();
  f.nodes.qq.value = '12a45';
  const event = f.nodes.editProfileForm.dispatch('submit');
  assert.equal(event.defaultPrevented, true);
  assert.equal(f.focusedId(), 'qq');
  assertError(f, 'qq', '请输入有效的QQ号码（5–11位数字）');
});

test('locked contact fields are not revalidated or used to block saving other profile changes', () => {
  const f = fixture({editableFields: ['name']});
  for (const field of ['phone', 'qq']) {
    f.nodes[field].value = 'legacy value';
    f.nodes[field].dispatch('input');
    f.nodes[field].dispatch('blur');
    f.nodes[field].dispatch('invalid');
    assertClear(f, field);
  }
  assert.equal(f.nodes.editProfileForm.dispatch('submit').defaultPrevented, false);
  assert.equal(f.focusedId(), null);
  assert.equal(f.notices.length, 0);
});

test('reset clears previous contact errors and safely skips locked fields without error nodes', () => {
  const f = fixture();
  f.nodes.phone.value = '138';
  f.nodes.qq.value = '12';
  f.nodes.phone.dispatch('blur');
  f.nodes.qq.dispatch('blur');
  assertError(f, 'phone', '请输入有效的11位手机号码');
  assertError(f, 'qq', '请输入有效的QQ号码（5–11位数字）');

  f.nodes.editProfileForm.dispatch('reset');
  assertClear(f, 'phone');
  assertClear(f, 'qq');

  // The browser restores default values; this fixture checks only feedback reset.
  const locked = fixture({editableFields: ['name'], missingLockedContactHints: true});
  assert.equal(locked.nodes.profilePhoneError, undefined);
  assert.equal(locked.nodes.profileQqError, undefined);
  locked.nodes.editProfileForm.dispatch('reset');
  assert.equal(locked.nodes.phone.classList.contains('is-invalid'), false);
  assert.equal(locked.nodes.qq.classList.contains('is-invalid'), false);
  assert.equal(locked.focusedId(), null);
  assert.equal(locked.notices.length, 0);
});
