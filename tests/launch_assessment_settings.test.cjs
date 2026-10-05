const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function monthInfoContext() {
    const source = fs.readFileSync('app/templates/admin/department_monthly_assessment_stats.html', 'utf8');
    const start = source.indexOf('function renderMonthInfo(');
    const end = source.indexOf('function renderAssessmentTierText(', start);
    const nodes = {monthScopeWarning: {}};
    const context = {document: {getElementById: id => nodes[id] ||= {}}, escapeHtml: value => String(value)};
    vm.runInNewContext(source.slice(start, end), context);
    return {nodes, context};
}

test('monthly assessment shows scope warning as plain text and clears it for historical payloads', () => {
    const {nodes, context} = monthInfoContext();
    const warning = '历史规则未归属学期 <img src=x onerror=bad()>，暂不生效';
    context.renderMonthInfo({has_full_months: true, month_labels: ['第1教学月'], scope_warning: warning});
    assert.equal(nodes.monthScopeWarning.textContent, warning);
    assert.equal(nodes.monthScopeWarning.innerHTML, undefined);
    assert.equal(nodes.monthScopeWarning.hidden, false);
    context.renderMonthInfo({has_full_months: true, month_labels: ['历史快照']});
    assert.equal(nodes.monthScopeWarning.hidden, true);
    assert.equal(nodes.monthScopeWarning.textContent, '');
});

test('unassigned rules show a warning and one explicit semester action in settings', () => {
    const source = fs.readFileSync('app/templates/admin/_settings_assessment.html', 'utf8');
    const start = source.indexOf('    function renderMembers(');
    const end = source.indexOf('    function loadMembers(', start);
    const nodes = {};
    const context = {
        document: {getElementById: id => nodes[id] ||= {}, querySelectorAll: () => []},
        escapeHtml: value => String(value || ''), renderOverrideTypeBadge: type => type,
        updateSelectionCount() {}, isSuperAdmin: true,
    };
    vm.runInNewContext(source.slice(start, end), context);
    context.renderMembers({total_weeks: 16, scope_warning: '未归属学期暂不生效', departments: [{
        department: '合成部门', groups: [{group_name: '合成组', members: [{user_id: 1, name: '合成人员',
            overrides: [{id: 7, override_type: 'leave', start_week: 1, end_week: 1, reason: '合成理由',
                scope_status: 'unassigned', semester: null, applies_to_current_semester: false}]}]}]}]});
    assert.equal(nodes.overrideScopeWarning.textContent, '未归属学期暂不生效');
    assert.equal(nodes.overrideScopeWarning.hidden, false);
    assert.match(nodes.memberBody.innerHTML, /未归属学期.*暂不生效/);
    assert.match(nodes.memberBody.innerHTML, /openAssignRuleSemester\(7\)/);
    assert.equal(nodes.ruleEndWeek.max, 16);
});
