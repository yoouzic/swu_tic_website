const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const template = fs.readFileSync('app/templates/user/lecture_form.html', 'utf8');
const source = template.slice(template.indexOf('function applyLectureFormDraftData(data)'),
                              template.indexOf('async function loadLectureFormDraft()'));
function fixture({scheduleReady=true,captureReady=scheduleReady}={}) {
  const fields = {
    listener_name:{value:'拍照流程验收（测试学院）', type:'text'},
    listener_number:{value:'PHOTO-AUDIT', type:'text'},
    course_feedback:{value:'上一条评价', type:'textarea'},
    teacher_name:{value:'', type:'text'},
    assistant_payload:{value:''}, feedback_counter:{textContent:''},
    unique_id:{value:''}, expected_form_id:{value:''}, expected_form_updated_at:{value:''},
  };
  const context = {
    window:{lectureFormSiteCaptureId:7}, CSS:{escape:key=>key},
    lectureScheduleReady:scheduleReady, lectureRegistrationReady:scheduleReady,
    lectureCaptureReady:captureReady,
    lectureCurrentSemester:'2026-2027-1',
    lectureCurrentScheduleBatchId:'1',
    parseAssistantPayloadObject:value=>typeof value==='object'?value:null,
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

test('unavailable schedule removes assistant provenance from the draft event data and keeps manual text',()=>{
  const f=fixture({scheduleReady:false});
  const data={assistant:{semester:'2025-2026-2',stage:'confirmed'},teacher_name:'手填教师',course_feedback:'手填反馈'};
  f.context.applyLectureFormDraftData(data);
  assert.equal(data.assistant,undefined);
  assert.equal(f.fields.assistant_payload.value,'');
  assert.equal(f.fields.teacher_name.value,'手填教师');
  assert.equal(f.fields.course_feedback.value,'手填反馈');
});

test('a current schedule cannot revive a stale-semester confirmed assistant draft',()=>{
  const f=fixture();const data={assistant:{semester:'2025-2026-2',stage:'confirmed'},teacher_name:'旧草稿教师'};
  f.context.applyLectureFormDraftData(data);
  assert.equal(data.assistant,undefined);
  assert.equal(f.fields.assistant_payload.value,'');
  assert.equal(f.fields.teacher_name.value,'旧草稿教师');
});

test('legacy draft version identifiers never turn a new manual form into an edit',()=>{
  const f=fixture();
  f.context.applyLectureFormDraftData({unique_id:'17',expected_form_id:'18',expected_form_updated_at:'old'});
  assert.equal(f.fields.unique_id.value,'');
  assert.equal(f.fields.expected_form_id.value,'');
  assert.equal(f.fields.expected_form_updated_at.value,'');
});

test('replaced current-semester batch cannot revive a previously confirmed draft',()=>{
  const f=fixture();const data={assistant:{semester:'2026-2027-1',stage:'confirmed',source_kind:'primary',source_batch_id:'99'}};
  f.context.applyLectureFormDraftData(data);
  assert.equal(data.assistant,undefined);
  assert.equal(f.fields.assistant_payload.value,'');
});

test('confirmation from the current authoritative batch remains restorable',()=>{
  const f=fixture();const data={assistant:{semester:'2026-2027-1',stage:'confirmed',source_kind:'primary',source_batch_id:'1'}};
  f.context.applyLectureFormDraftData(data);
  assert.equal(data.assistant.source_batch_id,'1');
  assert.match(f.fields.assistant_payload.value,/"source_batch_id":"1"/);
});

test('manual mode restores text without reviving a photo ID in runtime or event data',()=>{
  const f=fixture({scheduleReady:false});
  const data={site_capture_id:8,teacher_name:'手填教师',course_feedback:'保留评价'};
  f.context.applyLectureFormDraftData(data);
  assert.equal(f.context.window.lectureFormSiteCaptureId,null);
  assert.equal(data.site_capture_id,undefined);
  assert.equal(f.fields.teacher_name.value,'手填教师');
  assert.equal(f.fields.course_feedback.value,'保留评价');
});
