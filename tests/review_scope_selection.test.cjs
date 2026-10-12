const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const template = fs.readFileSync('app/templates/admin/review_forms.html', 'utf8');
const source = template.slice(template.indexOf('function restoreReviewQueueUserSelection()'),
                              template.indexOf('function getFormGroupKey('));

function scope(permission = '审表_部门', search = '', groupsPerDepartment = 1) {
  const elements = [];
  const makeCheckbox = (id, classes, card, value = '') => {
    const classNames = new Set(classes.split(' '));
    const checkbox = {id, value, checked: false, indeterminate: false,
      classes: classNames, classList: {contains: name => classNames.has(name)}, closest: selector => card[selector] || null};
    elements.push(checkbox);
    return checkbox;
  };
  const matching = selector => {
    const local = selector.replace(/^#userStructure\s+/, '');
    const checked = local.endsWith(':checked');
    const classes = local.replace(/:checked$/, '').split('.').filter(Boolean);
    return elements.filter(element => classes.every(cls => element.classes.has(cls)) && (!checked || element.checked));
  };
  const queryAll = selectors => [...new Set(selectors.split(',').flatMap(selector => matching(selector.trim())))];
  const users = [], groups = [], departments = [];
  for (let departmentIndex = 0; departmentIndex < 2; departmentIndex++) {
    const departmentUsers = [];
    const departmentCard = {querySelectorAll: () => departmentUsers};
    if (permission === '审表_中心') departments.push(makeCheckbox(`dept_${departmentIndex}`, 'dept-checkbox', {'.scope-dept': departmentCard}));
    for (let groupIndex = 0; groupIndex < groupsPerDepartment; groupIndex++) {
      const groupId = permission === '审表_中心' ? `${departmentIndex}_${groupIndex}` : String(departmentIndex * groupsPerDepartment + groupIndex);
      const groupUsers = [];
      const groupCard = {querySelectorAll: () => groupUsers};
      if (permission !== '审表_小组') groups.push(makeCheckbox(`group_${groupId}`, `group-checkbox dept-${departmentIndex}`, {'.scope-group': groupCard, '.scope-dept': departmentCard}));
      for (let index = 0; index < 2; index++) {
        const row = {style: {display: ''}};
        const user = makeCheckbox(`user_${users.length + 1}`, `user-checkbox group-${groupId}`, {'.scope-user': row, '.scope-group': groupCard, '.scope-dept': departmentCard}, String(users.length + 1));
        users.push(user); groupUsers.push(user); departmentUsers.push(user);
      }
    }
  }
  const root = {querySelectorAll: queryAll};
  const context = vm.createContext({
    currentPermission: permission, selectedUserIds: [], URLSearchParams,
    document: {querySelectorAll: queryAll, getElementById: id => id === 'userStructure' ? root : elements.find(element => element.id === id)},
    window: {location: {search}},
  });
  vm.runInContext(source, context);
  return {users, groups, departments, run: code => vm.runInContext(code, context)};
}

test('select all visibly checks collapsed department group headers as well as their members', () => {
  const page = scope();
  page.run('selectAllDepartments()');
  assert.ok(page.users.every(user => user.checked));
  assert.ok(page.groups.every(group => group.checked), 'collapsed group headers must show their selected members');
});

test('default queue selection visibly reflects all selected members in group headers', () => {
  const page = scope();
  page.run('restoreReviewQueueUserSelection()');
  assert.ok(page.users.every(user => user.checked));
  assert.ok(page.groups.every(group => group.checked));
});

test('restored partial selection marks group and department headers indeterminate', () => {
  const page = scope('审表_中心', '?user_ids=1');
  page.run('restoreReviewQueueUserSelection()');
  assert.equal(page.users[0].checked, true);
  assert.equal(page.users[1].checked, false);
  assert.equal(page.groups[0].checked, false);
  assert.equal(page.groups[0].indeterminate, true);
  assert.equal(page.departments[0].indeterminate, true);
  assert.equal(page.departments[1].indeterminate, false);
});

test('center select all selects only search-matched members and shows partial group state', () => {
  const page = scope('审表_中心');
  page.users.slice(1).forEach(user => {user.closest('.scope-user').style.display = 'none';});
  page.run('selectAllDepartments()');
  assert.deepEqual(page.users.map(user => user.checked), [true, false, false, false]);
  assert.equal(page.groups[0].indeterminate, true);
  assert.equal(page.departments[0].indeterminate, true);
});

test('clear selection clears checked and partial header states', () => {
  const page = scope('审表_中心', '?user_ids=1');
  page.run('restoreReviewQueueUserSelection(); clearScopeSelection();');
  assert.ok([...page.users, ...page.groups, ...page.departments].every(control => !control.checked && !control.indeterminate));
});

test('group selection updates its department header without selecting a different department', () => {
  const page = scope('审表_中心');
  page.groups[0].checked = true;
  page.run("toggleGroup('0_0')");
  assert.equal(page.departments[0].checked, true);
  assert.equal(page.departments[1].checked, false);
  assert.deepEqual(page.users.map(user => user.checked), [true, true, false, false]);
});

test('selecting a search result preserves previously selected members outside the search', () => {
  const page = scope('审表_中心', '?user_ids=4');
  page.run('restoreReviewQueueUserSelection()');
  page.users.slice(1).forEach(user => {user.closest('.scope-user').style.display = 'none';});
  page.run('selectAllDepartments()');
  assert.deepEqual(page.users.map(user => user.checked), [true, false, false, true]);
});

test('manual member changes update header state and selected member IDs', () => {
  const page = scope('审表_中心', '?user_ids=1');
  page.run('restoreReviewQueueUserSelection()');
  page.users[1].checked = true;
  page.run('syncScopeSelectionControls()');
  assert.equal(page.groups[0].checked, true);
  assert.equal(page.groups[0].indeterminate, false);
  assert.equal(page.departments[0].checked, true);
  assert.deepEqual(JSON.parse(JSON.stringify(page.run('selectedUserIds'))), ['1', '2']);
});

test('department selection propagates to all its groups even while parent state is being synchronized', () => {
  const page = scope('审表_中心', '', 2);
  page.departments[0].checked = true;
  page.run("toggleDepartment('0')");
  assert.deepEqual(page.users.map(user => user.checked), [true, true, true, true, false, false, false, false]);
  assert.deepEqual(page.groups.map(group => group.checked), [true, true, false, false]);
  assert.equal(page.departments[0].checked, true);
  assert.equal(page.departments[0].indeterminate, false);
});
