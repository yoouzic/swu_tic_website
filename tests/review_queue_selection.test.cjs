const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const template = fs.readFileSync('app/templates/admin/review_forms.html', 'utf8');
const source = template.slice(template.indexOf('function bindSelectionEvents()'), template.indexOf('function deleteFormGroup('))
  + template.slice(template.indexOf('function toggleSelectAll()'), template.indexOf('function resetSelectedExportCheckState()'));

function queue() {
  const controls = [];
  const selectAll = {checked: false, indeterminate: false, disabled: false};
  const selectedHint = {style: {}, textContent: ''};
  function addForm(id, locked = false) {
    const group = {kind: 'group-select-checkbox', dataset: {targetFormId: String(id)}, checked: locked, disabled: locked};
    const form = {kind: 'form-checkbox', value: String(id), dataset: {groupId: `group-${id}`}, checked: locked, disabled: locked};
    controls.push(group, form);
    return {group, form};
  }
  const first = addForm(101), second = addForm(102);
  const queryAll = selectors => controls.filter(control => selectors.split(',').some(part => {
    const selector = part.trim();
    return selector.startsWith(`.${control.kind}`) && (!selector.endsWith(':checked') || control.checked);
  }));
  const context = vm.createContext({
    currentPermission: '审表_中心', isSuperAdmin: false, selectionLocked: false, lockedFormIds: new Set(),
    document: {querySelectorAll: queryAll,
      getElementById: id => id === 'selectAll' ? selectAll : id === 'selectedFormsHint' ? selectedHint : null,
      querySelector: selector => {
        const id = selector.match(/(?:value|data-target-form-id)="([^"]+)"/)?.[1];
        return controls.find(control => selector.startsWith(`.${control.kind}`) && (control.value || control.dataset.targetFormId) === id);
      }},
  });
  vm.runInContext(source, context);
  return {controls, selectAll, first, second, addForm, run: code => vm.runInContext(code, context),
    selected: () => JSON.parse(JSON.stringify(vm.runInContext('getSelectedReviewableFormIds()', context)))};
}

test('select-all header reflects all manually selected current forms', () => {
  const page = queue();
  page.first.group.checked = true;
  page.second.group.checked = true;
  page.run('updateBatchButtons()');
  assert.equal(page.selectAll.checked, true);
  assert.equal(page.selectAll.indeterminate, false);
});

test('changing one form shows a partial select-all header', () => {
  const page = queue();
  page.first.group.checked = true;
  page.run('onGroupSelectChange(document.querySelector(\'.group-select-checkbox[data-target-form-id="101"]\'))');
  assert.equal(page.selectAll.checked, false);
  assert.equal(page.selectAll.indeterminate, true);
  assert.deepEqual(page.selected(), ['101']);
});

test('new unchecked list rows clear a previously checked select-all header', () => {
  const page = queue();
  page.selectAll.checked = true;
  page.run('updateBatchButtons()');
  assert.equal(page.selectAll.checked, false, 'rerendered unchecked rows must reset the stale header before its next click');
  assert.equal(page.selectAll.indeterminate, false);
  page.selectAll.checked = true;
  page.run('toggleSelectAll()');
  assert.deepEqual(page.selected(), ['101', '102']);
});

test('select all preserves locked forms and keeps the existing unique ID payload', () => {
  const page = queue();
  const locked = page.addForm(103, true);
  page.run('lockedFormIds.add("103")');
  page.selectAll.checked = true;
  page.run('toggleSelectAll()');
  assert.deepEqual(page.selected().sort(), ['101', '102', '103']);
  page.selectAll.checked = false;
  page.run('toggleSelectAll()');
  assert.equal(locked.group.checked, true);
  assert.equal(locked.form.checked, true);
  assert.deepEqual(page.selected(), ['103']);
});

test('select-all is disabled when permissions or locks leave no selectable forms', () => {
  const page = queue();
  page.controls.forEach(control => {control.disabled = true;});
  page.selectAll.checked = true;
  page.run('updateBatchButtons()');
  assert.equal(page.selectAll.disabled, true);
  assert.equal(page.selectAll.checked, false);
  page.addForm(104);
  page.run('updateBatchButtons()');
  assert.equal(page.selectAll.disabled, false);
});
