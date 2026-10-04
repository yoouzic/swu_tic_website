const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const template = fs.readFileSync('app/templates/user/lecture_form.html', 'utf8');
const source = template.slice(template.indexOf('function applyLectureFormDraftData(data)'),
                              template.indexOf('async function loadLectureFormDraft()'));
function fixture() {
  const fields = {
    listener_name:{value:'拍照流程验收（测试学院）', type:'text'},
    listener_number:{value:'PHOTO-AUDIT', type:'text'},
    course_feedback:{value:'上一条评价', type:'textarea'},
    teacher_name:{value:'', type:'text'},
    assistant_payload:{value:''}, feedback_counter:{textContent:''},
  };
  const context = {
    window:{lectureFormSiteCaptureId:7}, CSS:{escape:key=>key},
    parseAssistantPayloadObject:()=>null,
    document:{
      getElementById:id=>fields[id] || null,
      querySelectorAll:selector=>{
        const field=fields[selector.match(/^\[name="(.+)"\]$/)[1]];
        return field?[field]:[];
      },
    },
  };
  vm.createContext(context);vm.runInContext(source,context);
  return {fields,context};
}
test('new photo draft cannot clear the required readonly listener identity',()=>{
  const f=fixture();
  f.context.applyLectureFormDraftData({site_capture_id:8,listener_name:'',listener_number:''});
  assert.equal(f.fields.listener_name.value,'拍照流程验收（测试学院）');
  assert.equal(f.fields.listener_number.value,'PHOTO-AUDIT');
  assert.equal(f.context.window.lectureFormSiteCaptureId,8);
});
test('older per-photo identity cannot replace the current rendered profile',()=>{
  const f=fixture();
  f.context.applyLectureFormDraftData({listener_name:'旧姓名（旧学院）',listener_number:'OLD'});
  assert.equal(f.fields.listener_name.value,'拍照流程验收（测试学院）');
  assert.equal(f.fields.listener_number.value,'PHOTO-AUDIT');
});
test('preserving identity still restores the selected record course and manual feedback',()=>{
  const f=fixture();
  f.context.applyLectureFormDraftData({site_capture_id:8,teacher_name:'文红梅',course_feedback:'记录乙独立人工评价'});
  assert.equal(f.fields.teacher_name.value,'文红梅');
  assert.equal(f.fields.course_feedback.value,'记录乙独立人工评价');
  assert.equal(f.fields.feedback_counter.textContent,'已输入 9 字');
});
