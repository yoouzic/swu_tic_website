const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const template = fs.readFileSync('app/templates/admin/review_forms.html', 'utf8');
function pageFunction(name) {
  const start = template.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `The real page must define ${name}`);
  const nextFunction = template.indexOf('\nfunction ', start + 1);
  const scriptEnd = template.indexOf('</script>', start);
  return template.slice(start, nextFunction === -1 ? scriptEnd : Math.min(nextFunction, scriptEnd));
}
const source = [
  'renderFormsList', 'getAutomationCategoryLabel', 'getAutomationCategoryClass',
  'getAutomationCoverageLabel', 'getAutomationCoverageClass',
  'renderAutomationBatchPreview', 'automationEvidenceSource',
  'renderAutomationAssessment', 'updateAutomationRows',
].map(pageFunction).join('\n');

// Execute the production page functions; represent only their browser boundary.
function queue() {
  const rows = new Map();
  const evidence = new Map();
  const element = () => ({
    children: [], _text: '', className: '', dataset: {},
    set textContent(value) { this._text = String(value); this.children = []; },
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); },
    replaceChildren(...children) { this._text = ''; this.children = children; },
    append(...children) { this.children.push(...children); },
    appendChild(child) { this.children.push(child); },
  });
  const nodes = {
    riskFilter: {value: ''}, formsCount: element(), autoReviewDecisionSummary: element(),
    automationSuggestedComment: {value: ''},
  };
  let html = '';
  nodes.formsTableBody = {
    set innerHTML(value) {
      html = value;
      rows.clear();
      for (const match of html.matchAll(/<article\b[^>]*data-review-row-form-id="(\d+)"[^>]*>([\s\S]*?)<\/article>/g)) {
        const category = match[2].match(/<span class="([^"]*)" data-automation-category>([^<]*)<\/span>/);
        const coverage = match[2].match(/<span class="([^"]*)" data-automation-coverage="([^"]*)">([^<]*)<\/span>/);
        const badges = {
          '[data-automation-category]': category && Object.assign(element(), {className: category[1], textContent: category[2]}),
          '[data-automation-coverage]': coverage && Object.assign(element(), {
            className: coverage[1], dataset: {automationCoverage: coverage[2]}, textContent: coverage[3],
          }),
        };
        rows.set(Number(match[1]), {querySelector: selector => badges[selector] || null});
      }
    },
    get innerHTML() { return html; },
  };
  const context = vm.createContext({
    document: {
      getElementById: id => nodes[id] || null,
      createElement: element,
      querySelector(selector) {
        const formId = selector.match(/^\[data-review-row-form-id="(\d+)"\]$/)?.[1];
        if (formId) return rows.get(Number(formId)) || null;
        return null;
      },
    },
    window: {}, currentPermission: '审表_中心', isSuperAdmin: false,
    isFormLocked: () => false, escapeHtml: value => String(value), getStatusClass: () => '',
    bindSelectionEvents() {}, updateBatchButtons() {},
    setAutomationEvidenceValue: (kind, value) => evidence.set(kind, value),
  });
  vm.runInContext(source, context);
  return {
    nodes, rows, evidence,
    render(groups, checked = []) { context.groups = groups; context.checked = checked; vm.runInContext('renderFormsList(groups, checked)', context); },
    run(code) { return vm.runInContext(code, context); },
    badge(id, selector) { return rows.get(id)?.querySelector(selector); },
    html: () => html,
  };
}

function group(id, automation) {
  return {unique_id: id, forms: [{id, listener_name: '合成信息员', listener_number: `SYN-${id}`,
    course_title: '合成课程', teacher_name: '合成教师', status: '待审核', can_review: true, automation}]};
}
function assessment(coverage, category = '建议复核') {
  return {assessment_id: 'SYN-ASSESSMENT', category, coverage};
}

for (const [name, automation] of [['null', null], ['omitted', undefined]]) {
  test(`a ${name} assessment displays unchecked instead of a fabricated missing schedule`, () => {
    const page = queue();
    page.render([group(1, automation)]);
    assert.equal(page.badge(1, '[data-automation-category]').textContent, '未检查');
    assert.doesNotMatch(page.html(), /系统无法判断|课表缺失/);
    const coverage = page.badge(1, '[data-automation-coverage]');
    if (coverage) {
      assert.equal(coverage.textContent, '个人课表未检查');
      assert.notEqual(coverage.dataset.automationCoverage, 'none');
    }
  });
}

for (const [coverage, label] of [['none', '个人课表缺失'], ['basic', '个人课表基础'], ['complete', '个人课表完整']]) {
  test(`an existing ${coverage} assessment identifies personal schedule coverage`, () => {
    const page = queue();
    page.render([group(1, assessment(coverage))], [1]);
    assert.equal(page.badge(1, '[data-automation-category]').textContent, '建议复核');
    assert.equal(page.badge(1, '[data-automation-coverage]').textContent, label);
    assert.match(page.html(), /data-target-form-id="1"\s+checked/);
    assert.match(page.html(), /data-open-review="1"/);
  });
}

test('the unknown risk filter excludes forms that have never been checked', () => {
  const page = queue();
  page.nodes.riskFilter.value = '系统无法判断';
  page.render([group(1, null), group(2, assessment('none', '系统无法判断'))]);
  assert.deepEqual([...page.rows.keys()], [2]);
});

test('unchecked forms can be selected through the classification filter', () => {
  const page = queue();
  assert.ok(/<option value="未检查">未检查<\/option>/.test(template), 'The classification filter must offer unchecked forms');
  page.nodes.riskFilter.value = '未检查';
  page.render([group(1, null), group(2, assessment('complete'))]);
  assert.deepEqual([...page.rows.keys()], [1]);
});

test('the coverage legend explicitly describes personal schedules', () => {
  assert.ok(/aria-label="个人课表证据覆盖"/.test(template), 'The coverage legend must identify personal schedules');
  for (const label of ['个人课表缺失', '个人课表基础', '个人课表完整']) assert.ok(template.includes(`>${label}</span>`));
});

test('batch preview separates unchecked forms from missing personal schedules', () => {
  const page = queue();
  page.run('renderAutomationBatchPreview({processable: 5, cache_reusable: 0, unchecked: 2, basic: 1, complete: 1, missing: 1})');
  const values = page.nodes.autoReviewDecisionSummary.children.map(child => child.children.map(value => value.textContent));
  assert.ok(values.some(([label, value]) => label === '尚未检查' && value === '2'));
  assert.ok(values.some(([label, value]) => label === '个人课表缺失' && value === '1'));
  assert.ok(values.some(([label, value]) => label === '个人课表基础证据' && value === '1'));
  assert.ok(values.some(([label, value]) => label === '个人课表完整证据' && value === '1'));
});

test('the evidence drawer distinguishes personal coverage from school course evidence', () => {
  const page = queue();
  page.run('renderAutomationAssessment(1, {coverage: "none", findings: [{source: "schedule", title: "全校课程", message: "匹配成功"}]})');
  assert.match(page.evidence.get('schedule'), /个人课表覆盖：缺失/);
  assert.match(page.evidence.get('schedule'), /全校课程：匹配成功/);
});

test('completed batch results update both classification and personal coverage', () => {
  const page = queue();
  page.render([group(1, null)]);
  page.run('updateAutomationRows([{form_id: 1, assessment_id: "SYN-NEW", status: "completed", category: "无明显风险", coverage: "complete"}])');
  assert.equal(page.badge(1, '[data-automation-category]').textContent, '无明显风险');
  assert.equal(page.badge(1, '[data-automation-coverage]').textContent, '个人课表完整');
  assert.equal(page.badge(1, '[data-automation-coverage]').dataset.automationCoverage, 'complete');
});

test('queued batch items do not replace unchecked with unknown', () => {
  const page = queue();
  page.render([group(1, null)]);
  page.run('updateAutomationRows([{form_id: 1, assessment_id: null, status: "queued", category: "系统无法判断", coverage: null}])');
  assert.equal(page.badge(1, '[data-automation-category]').textContent, '未检查');
});

test('batch updates remain available when the user changes the risk filter', () => {
  const page = queue();
  page.render([group(1, null)]);
  page.run('updateAutomationRows([{form_id: 1, assessment_id: "SYN-NEW", status: "completed", category: "无明显风险", coverage: "complete"}])');
  page.nodes.riskFilter.value = '无明显风险';
  page.run('renderFormsList(currentFormGroups)');
  assert.deepEqual([...page.rows.keys()], [1]);
  assert.equal(page.badge(1, '[data-automation-coverage]').textContent, '个人课表完整');
});
