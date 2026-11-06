const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const feedbackText = require('../app/static/js/feedback-text.js');
const template = fs.readFileSync('app/templates/user/lecture_form.html', 'utf8');
const submitSource = template.slice(template.indexOf('// 表单验证'), template.indexOf('// 电话号码验证'));
const inputStart = template.indexOf("document.getElementById('course_feedback').addEventListener('input'");
const inputSource = template.slice(inputStart, template.indexOf('\n});', inputStart) + 4);

function fixture(value) {
  const fields = new Map();
  function field(id) {
    if (!fields.has(id)) fields.set(id, {
      id, value: 'present', checked: id === 'tm_lecture', style: {}, dataset: {}, textContent: '',
      classList: {add() {}, remove() {}},
      addEventListener(name, handler) {this[name] = handler;},
      querySelectorAll() {return [field('course_feedback')];},
      querySelector() {return null;},
    });
    return fields.get(id);
  }
  field('course_feedback').value = value;
  const context = vm.createContext({
    document: {getElementById: field},
    window: {SwuFeedbackText: feedbackText, appFeedback() {}},
    syncCoursewareQualityWithTeachingMethod() {},
    updateDateWithWeekday() {}, lectureFormDraftSubmitting: false,
  });
  vm.runInContext(submitSource + inputSource, context);
  return {field, submit() {
    let prevented = false;
    field('lectureForm').submit.call(field('lectureForm'), {preventDefault() {prevented = true;}});
    return prevented;
  }, input() {field('course_feedback').input.call(field('course_feedback'));}};
}

for (const [label, text, count] of [
  ['49 with punctuation', '课'.repeat(49) + '，。!?\n🙂', 49],
  ['50 mixed', '课'.repeat(20) + 'Abcde'.repeat(3) + '12345'.repeat(3) + '，\n🙂', 50],
  ['51 mixed', '课'.repeat(21) + 'Abcde'.repeat(3) + '12345'.repeat(3) + '。', 51],
  ['49 supplementary Han', '𠮷'.repeat(49) + '。', 49],
  ['50 supplementary Han', '𠮷'.repeat(50), 50],
  ['49 plus keycap emoji', '课'.repeat(49) + '1️⃣', 49],
]) {
  test(`form submit and live counter agree: ${label}`, () => {
    const form = fixture(text);
    form.input();
    assert.equal(form.field('feedback_counter').textContent, `已输入 ${count} 字`);
    assert.equal(form.field('feedback_limit_hint').style.display, count < 50 ? 'inline' : 'none');
    assert.equal(form.submit(), count < 50);
    assert.equal(form.field('feedback_counter').textContent, `已输入 ${count} 字`);
  });
}

test('shared feedback counter loads before inline consumers on both forms', () => {
  for (const path of ['app/templates/user/lecture_form.html', 'app/templates/admin/review_form.html']) {
    const html = fs.readFileSync(path, 'utf8');
    assert.ok(html.indexOf('js/feedback-text.js') >= 0);
    assert.ok(html.indexOf('js/feedback-text.js') < html.indexOf('<script>'));
  }
});
