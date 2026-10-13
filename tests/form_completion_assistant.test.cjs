const {test} = require('node:test');
const assert = require('node:assert/strict');
const assistant = require('../app/static/js/form-completion-assistant.js');

function formFixture(values = {}) {
  const fields = {};
  const ids = ['course_changes','abnormal_situation','suggestions','quality_case','classroom_discipline','classroom_atmosphere','courseware_quality','overall_effect','teaching_method','tm_ppt','tm_lecture','tm_interactive','course_feedback','student_signature1','student_signature2','contact_phone1','contact_phone2'];
  ids.forEach(id => { fields[id] = {id, value: values[id] || '', checked: false, type: id.startsWith('tm_') ? 'checkbox' : 'text', dispatchEvent() {}}; });
  return {fields, querySelector(selector) { return fields[selector.slice(1)] || null; }};
}

test('no issues explicitly fills changes, anomalies and suggestions only', () => {
  const form = formFixture();
  assistant.applyIssues(form, 'none', new Map());
  for (const id of ['course_changes','abnormal_situation','suggestions']) assert.equal(form.fields[id].value, '无');
  assert.equal(form.fields.course_feedback.value, '');
  assert.equal(form.fields.student_signature1.value, '');
});

test('skipping issue shortcut preserves manually entered no and does not invent other answers',()=>{
  const form=formFixture({suggestions:'无'});
  assistant.applyIssues(form,'skip',new Map());
  assert.equal(form.fields.suggestions.value,'无');
  assert.equal(form.fields.course_changes.value,'');
  assert.equal(form.fields.abnormal_situation.value,'');
});

test('recommendation preserves actual teaching methods and ratings', () => {
  const form = formFixture({classroom_discipline:'一般', classroom_atmosphere:'好', courseware_quality:'无', overall_effect:'一般', teaching_method:'讲授法'});
  form.fields.tm_lecture.checked = true;
  assistant.applyRecommendation(form, 'yes', new Map());
  assert.equal(form.fields.quality_case.value, '推荐');
  assert.equal(form.fields.tm_ppt.checked, false);
  assert.equal(form.fields.tm_lecture.checked, true);
  assert.equal(form.fields.tm_interactive.checked, false);
  assert.equal(form.fields.classroom_discipline.value, '一般');
  assert.equal(form.fields.classroom_atmosphere.value, '好');
  assert.equal(form.fields.courseware_quality.value, '无');
  assert.equal(form.fields.overall_effect.value, '一般');
  assert.equal(form.fields.teaching_method.value, '讲授法');
});

test('recommendation leaves unanswered ratings and teaching methods for the user to fill', () => {
  const form = formFixture();
  assistant.applyRecommendation(form, 'yes', new Map());
  assert.equal(form.fields.quality_case.value, '推荐');
  for (const id of ['classroom_discipline','classroom_atmosphere','courseware_quality','overall_effect','teaching_method']) assert.equal(form.fields[id].value, '');
  for (const id of ['tm_ppt','tm_lecture','tm_interactive']) assert.equal(form.fields[id].checked, false);
});

test('changing issue answer restores old text and preserves later manual edits', () => {
  const form = formFixture({suggestions: '原来的建议'});
  const owned = new Map();
  assistant.applyIssues(form, 'none', owned);
  form.fields.abnormal_situation.value = '学生迟到';
  assistant.applyIssues(form, 'some', owned);
  assert.equal(form.fields.course_changes.value, '');
  assert.equal(form.fields.suggestions.value, '原来的建议');
  assert.equal(form.fields.abnormal_situation.value, '学生迟到');
});

test('changing recommendation preserves ratings and teaching methods', () => {
  const form = formFixture({classroom_discipline: '好'});
  const owned = new Map();
  assistant.applyRecommendation(form, 'yes', owned);
  form.fields.classroom_atmosphere.value = '一般';
  assistant.applyRecommendation(form, 'no', owned);
  assert.equal(form.fields.quality_case.value, '不推荐');
  assert.equal(form.fields.classroom_discipline.value, '好');
  assert.equal(form.fields.classroom_atmosphere.value, '一般');
  assert.equal(form.fields.overall_effect.value, '');
  assert.equal(form.fields.tm_ppt.checked, false);
});

test('completion requires enough feedback and real phone format, and clears when valid', () => {
  const form = formFixture({course_feedback: '很好', contact_phone1: '123'});
  assert.equal(assistant.needsAttention(form.fields.course_feedback), true);
  assert.equal(assistant.needsAttention(form.fields.contact_phone1), true);
  form.fields.course_feedback.value = '具体课堂反馈'.repeat(12);
  form.fields.contact_phone1.value = '13800138000';
  assert.equal(assistant.needsAttention(form.fields.course_feedback), false);
  assert.equal(assistant.needsAttention(form.fields.contact_phone1), false);
});

test('confirming no issues again remembers the most recent manual value for undo', () => {
  const form = formFixture();
  const owned = new Map();
  assistant.applyIssues(form, 'none', owned);
  form.fields.course_changes.value = '新补充的变化';
  assistant.applyIssues(form, 'none', owned);
  assistant.applyIssues(form, 'some', owned);
  assert.equal(form.fields.course_changes.value, '新补充的变化');
});

test('changing a restored no-issues answer clears literal no values but preserves actual details', () => {
  const form = formFixture({course_changes:'无', abnormal_situation:'学生迟到', suggestions:'无'});
  assistant.applyIssues(form, 'some', new Map());
  assert.equal(form.fields.course_changes.value, '');
  assert.equal(form.fields.suggestions.value, '');
  assert.equal(form.fields.abnormal_situation.value, '学生迟到');
});

test('skipping a shortcut restores the earlier recommendation only', () => {
  const form = formFixture({quality_case:'不推荐', overall_effect:'一般'});
  const owned = new Map();
  assistant.applyRecommendation(form, 'yes', owned);
  assistant.applyRecommendation(form, 'skip', owned);
  assert.equal(form.fields.quality_case.value, '不推荐');
  assert.equal(form.fields.overall_effect.value, '一般');
});
