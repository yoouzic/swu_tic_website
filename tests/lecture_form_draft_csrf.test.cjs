const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const template = fs.readFileSync('app/templates/user/lecture_form.html', 'utf8');
const draftSource = template.slice(template.indexOf('function parseAssistantPayloadObject(value)'),
                                   template.indexOf('async function loadLectureFormDraft()'));
const populateSource = template.slice(template.indexOf('function populateForm(data)'),
                                      template.indexOf('    </script>', template.indexOf('function populateForm(data)')))
                               .replace(/{% if form and form.status == '已驳回' %}[\s\S]*?{% endif %}/,'');

function fixture() {
  const fields = {
    lectureForm: {}, csrf_token: {name:'csrf_token',value:'fresh-session-token',type:'hidden'},
    course_feedback: {name:'course_feedback',value:'当前人工评价',type:'textarea'},
    teacher_name: {name:'teacher_name',value:'',type:'text'},
    feedback_counter:{textContent:''}, teaching_method:{name:'teaching_method',value:''},
    start_period:{name:'start_period',value:'3'}, end_period:{name:'end_period',value:'4'},
    unique_id:{name:'unique_id',value:''}, expected_form_id:{name:'expected_form_id',value:''},
    expected_form_updated_at:{name:'expected_form_updated_at',value:''},
    entry_mode:{name:'entry_mode',value:'photo'},
    manual_entry_allowed:{name:'manual_entry_allowed',value:'false'},
  };
  const context = {
    window:{lectureFormSiteCaptureId:null}, CSS:{escape:key=>key},
    lectureFormIsEditMode:false, lectureScheduleReady:true, lectureRegistrationReady:true,
    lectureCaptureReady:true, lectureCurrentSemester:'2026-2027-1', lectureCurrentScheduleBatchId:'1',
    document:{getElementById:id=>fields[id]||null, querySelectorAll:selector=>{
      const field=fields[selector.match(/^\[name="(.+)"\]$/)[1]];
      return field?[field]:[];
    }},
    FormData:class {
      constructor(form) {assert.equal(form,fields.lectureForm);}
      forEach(callback) {for(const field of Object.values(fields))if(field.name)callback(field.value,field.name);}
    },
    updateDateWithWeekday(){}, updateClassPeriod(){}, syncCoursewareQualityWithTeachingMethod(){},
    console:{log(){}},
  };
  vm.createContext(context);
  vm.runInContext(draftSource + populateSource,context);
  return {fields,context};
}

test('draft collection retains user text while excluding the current CSRF credential',()=>{
  const f=fixture();
  const data=f.context.collectLectureFormDraftData();
  assert.equal(data.course_feedback,'当前人工评价');
  assert.equal(Object.hasOwn(data,'csrf_token'),false);
  assert.equal(Object.hasOwn(data,'entry_mode'),false);
  assert.equal(Object.hasOwn(data,'manual_entry_allowed'),false);
});

test('old ordinary and per-photo draft cannot overwrite fresh hidden token or forward it to listeners',()=>{
  for(const site_capture_id of [undefined,7]) {
    const f=fixture();
    const old={csrf_token:'old-session-token',course_feedback:'记录独立人工评价',site_capture_id,
               entry_mode:'manual',manual_entry_allowed:true};
    f.context.applyLectureFormDraftData(old);
    assert.equal(f.fields.csrf_token.value,'fresh-session-token');
    assert.equal(f.fields.course_feedback.value,'记录独立人工评价');
    assert.equal(Object.hasOwn(old,'csrf_token'),false,'restoration event data must also be sanitized');
    assert.equal(f.fields.entry_mode.value,'photo');
    assert.equal(f.fields.manual_entry_allowed.value,'false');
    assert.equal(Object.hasOwn(old,'entry_mode'),false);
    assert.equal(Object.hasOwn(old,'manual_entry_allowed'),false);
  }
});

test('form error repopulation cannot restore a submitted session token by id',()=>{
  const f=fixture();
  f.context.populateForm({csrf_token:'old-session-token',teacher_name:'保留教师',
                         entry_mode:'manual',manual_entry_allowed:true});
  assert.equal(f.fields.csrf_token.value,'fresh-session-token');
  assert.equal(f.fields.teacher_name.value,'保留教师');
  assert.equal(f.fields.entry_mode.value,'photo');
  assert.equal(f.fields.manual_entry_allowed.value,'false');
});
