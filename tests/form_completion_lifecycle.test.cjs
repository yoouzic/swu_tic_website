const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('app/static/js/form-completion-assistant.js','utf8');

function node(id=''){
  const listeners={};
  return {id,value:'',checked:false,type:'text',hidden:false,dataset:{},classList:{toggle(){},remove(){}},
    listeners,addEventListener(type,callback){(listeners[type]||=[]).push(callback);},
    dispatchEvent(event){for(const callback of listeners[event.type]||[])callback(event);},
    fire(type,event={}){this.dispatchEvent({type,...event});},replaceChildren(){},appendChild(){},
    querySelector(){return null;},closest(){return null;},focus(){},matches(){return false;}};
}
function fixture(){
  const ids=['course_changes','abnormal_situation','suggestions','quality_case','classroom_discipline','classroom_atmosphere','courseware_quality','overall_effect','teaching_method','tm_ppt','tm_lecture','tm_interactive','course_feedback','student_signature1','student_signature2','contact_phone1','contact_phone2','class_period','start_period','assistant_payload'];
  const fields=Object.fromEntries(ids.map(id=>[id,node(id)]));
  for(const id of ['tm_ppt','tm_lecture','tm_interactive'])fields[id].type='checkbox';
  const panel=node(),form=node(),window=node();
  const parts=Object.fromEntries(['issues','methods','ratings','rating-detail','rating-title','rating-progress','courseware-choice','method-confirm','rating-confirm','recommendation','summary','checklist','missing','count'].map(key=>[key,node()]));
  const methodChoices=['tm_ppt','tm_lecture','tm_interactive'].map(id=>Object.assign(node(),{dataset:{completionMethod:id},type:'checkbox'}));
  const ratingChoices=['classroom_discipline','classroom_atmosphere','courseware_quality','overall_effect'].map(id=>Object.assign(node(),{dataset:{completionRating:id},type:'checkbox'}));
  panel.querySelector=selector=>parts[selector.match(/data-completion-(.*?)\]/)?.[1]];
  panel.querySelectorAll=selector=>selector==='[data-completion-method]'?methodChoices:selector==='[data-completion-rating]'?ratingChoices:[];
  panel.contains=()=>false;
  form.querySelector=selector=>fields[selector.slice(1)]||null;
  form.querySelectorAll=selector=>selector==='[required]'?[]:selector.startsWith('[data-completion')?[]:Object.values(fields);
  let ready;
  const document={getElementById:()=>form,querySelector:()=>panel,createElement:()=>node(),addEventListener(type,callback){if(type==='DOMContentLoaded')ready=callback;}};
  vm.runInNewContext(source,{document,window,Event:class{constructor(type){this.type=type;}},Map,Set});ready();
  const draft=(captureId,confirmed=true)=>{
    fields.assistant_payload.value=confirmed?'confirmed':'';
    window.fire('lecture-form-draft-loaded',{detail:{data:{site_capture_id:captureId,...(confirmed?{assistant:{stage:'confirmed'}}:{})}}});
  };
  const answer=(key,value)=>panel.fire('click',{target:{closest:()=>({dataset:{[key]:value},hasAttribute:name=>name===`data-${key.replace(/[A-Z]/g,c=>'-'+c.toLowerCase())}`})}});
  return {fields,panel,parts,form,window,draft,answer,methodChoices,ratingChoices};
}

test('skipping on a second record preserves its manual values instead of restoring the first record text',()=>{
  const f=fixture();f.fields.course_changes.value='第一条记录的课程变化';f.draft(7);
  f.answer('completionIssueAnswer','none');
  for(const id of ['course_changes','abnormal_situation','suggestions'])f.fields[id].value='无';
  f.draft(8);f.answer('completionIssueAnswer','skip');
  assert.equal(f.fields.course_changes.value,'无');
  assert.equal(f.fields.abnormal_situation.value,'无');
});

test('loading an unconfirmed photo draft hides the previous record evaluation guide',()=>{
  const f=fixture();f.draft(7);assert.equal(f.panel.hidden,false);
  f.draft(8,false);
  assert.equal(f.panel.hidden,true);
});

test('changing answers within the same photo record still restores its own prior manual values',()=>{
  const f=fixture();f.fields.course_changes.value='本条人工变化';f.draft(7);
  f.answer('completionIssueAnswer','none');f.draft(7);
  f.answer('completionIssueAnswer','skip');
  assert.equal(f.fields.course_changes.value,'本条人工变化');
});

test('manual recommendation is accepted with general ratings',()=>{
  const f=fixture();
  for(const id of ['classroom_discipline','classroom_atmosphere','courseware_quality','overall_effect'])f.fields[id].value='一般';
  f.fields.tm_ppt.checked=true;
  f.fields.quality_case.value='推荐';
  f.form.fire('change',{target:f.fields.quality_case});
  assert.equal(f.fields.quality_case.value,'推荐');
});

test('editing any rating keeps a recommendation',()=>{
  const f=fixture();
  for(const id of ['tm_ppt','tm_lecture','tm_interactive'])f.fields[id].checked=true;
  for(const id of ['classroom_discipline','classroom_atmosphere','courseware_quality','overall_effect'])f.fields[id].value='非常好';
  f.fields.quality_case.value='推荐';
  for(const id of ['classroom_discipline','classroom_atmosphere','courseware_quality','overall_effect']){
    for(const value of ['好','一般']){
      f.fields[id].value=value;
      f.form.fire('change',{target:f.fields[id]});
      assert.equal(f.fields.quality_case.value,'推荐');
      assert.equal(f.fields[id].value,value);
    }
  }
});

test('changing teaching methods and feedback keeps a recommendation',()=>{
  const f=fixture();
  f.fields.quality_case.value='推荐';
  f.fields.tm_lecture.checked=true;
  f.fields.courseware_quality.value='无';
  f.form.fire('change',{target:f.fields.tm_lecture});
  f.fields.course_feedback.value='真实的课堂反馈';
  f.form.fire('input',{target:f.fields.course_feedback});
  assert.equal(f.fields.quality_case.value,'推荐');
  assert.equal(f.fields.courseware_quality.value,'无');
});

test('restoring a recommended draft with general ratings completes the recommendation question',()=>{
  const f=fixture();
  for(const id of ['course_changes','abnormal_situation','suggestions'])f.fields[id].value='无';
  for(const id of ['classroom_discipline','classroom_atmosphere','courseware_quality','overall_effect'])f.fields[id].value='一般';
  f.fields.quality_case.value='推荐';
  f.fields.tm_ppt.checked=true;
  f.draft(9);
  assert.equal(f.parts.recommendation.hidden,true);
  assert.equal(f.parts.summary.hidden,false);
  assert.equal(f.fields.overall_effect.value,'一般');
});

test('unanswered methods and complement are not preset when the guide opens',()=>{
  const f=fixture();f.form.fire('lecture-assistant-course-confirmed');
  f.answer('completionIssueAnswer','none');
  assert.equal(f.parts.methods.hidden,false);
  assert.equal(f.parts.ratings.hidden,true);
  for(const id of ['classroom_discipline','classroom_atmosphere','overall_effect'])assert.equal(f.fields[id].value,'');
  assert.equal(f.fields.tm_ppt.checked,false);
});

test('multi selected ratings are asked in order and do not affect recommendation',()=>{
  const f=fixture();f.form.fire('lecture-assistant-course-confirmed');
  f.answer('completionIssueAnswer','none');
  f.methodChoices[0].checked=true;f.methodChoices[1].checked=true;
  f.answer('completionMethodConfirm','');
  assert.equal(f.parts.ratings.hidden,false);
  f.ratingChoices[0].checked=true;f.ratingChoices[3].checked=true;
  f.answer('completionRatingConfirm','');
  assert.equal(f.parts['rating-detail'].hidden,false);
  assert.equal(f.parts['rating-title'].textContent,'管理课堂纪律');
  assert.equal(f.fields.classroom_atmosphere.value,'非常好');
  assert.equal(f.fields.quality_case.value,'');
  f.answer('completionRatingAnswer','一般');
  assert.equal(f.parts['rating-title'].textContent,'整体教学效果');
  f.answer('completionRatingAnswer','好');
  assert.equal(f.parts.recommendation.hidden,false);
  f.answer('completionRecommendAnswer','yes');
  assert.equal(f.fields.quality_case.value,'推荐');
  assert.equal(f.fields.classroom_discipline.value,'一般');
  assert.equal(f.fields.overall_effect.value,'好');
  assert.equal(f.parts.summary.hidden,false);
});

test('confirming no exceptions without PPT fills three ratings and keeps courseware absent',()=>{
  const f=fixture();f.form.fire('lecture-assistant-course-confirmed');f.answer('completionIssueAnswer','none');
  f.methodChoices[1].checked=true;f.answer('completionMethodConfirm','');
  assert.equal(f.parts['courseware-choice'].hidden,true);
  f.answer('completionRatingConfirm','');
  assert.equal(f.fields.courseware_quality.value,'无');
  for(const id of ['classroom_discipline','classroom_atmosphere','overall_effect'])assert.equal(f.fields[id].value,'非常好');
  assert.equal(f.parts.recommendation.hidden,false);
  assert.equal(f.fields.quality_case.value,'');
});

test('switching to a new record clears selected choices and does not inherit ratings',()=>{
  const f=fixture();f.draft(21);f.answer('completionIssueAnswer','none');
  f.methodChoices[0].checked=true;f.answer('completionMethodConfirm','');f.answer('completionRatingConfirm','');
  for(const field of Object.values(f.fields)){field.value='';field.checked=false;}
  f.draft(22);f.answer('completionIssueAnswer','none');
  assert.equal(f.parts.methods.hidden,false);
  assert.equal(f.methodChoices[0].checked,false);
  assert.equal(f.fields.overall_effect.value,'');
});

test('going back does not rewrite ratings and skipping restores this record manual ratings',()=>{
  const f=fixture();f.form.fire('lecture-assistant-course-confirmed');f.answer('completionIssueAnswer','none');
  f.fields.classroom_discipline.value='好';f.fields.classroom_atmosphere.value='一般';
  f.methodChoices[0].checked=true;f.answer('completionMethodConfirm','');
  f.ratingChoices[0].checked=true;f.ratingChoices[1].checked=false;
  f.answer('completionRatingConfirm','');f.answer('completionRatingAnswer','一般');
  f.answer('completionBack','');
  assert.equal(f.fields.classroom_discipline.value,'一般');
  assert.equal(f.fields.classroom_atmosphere.value,'非常好');
  f.answer('completionRatingSkip','');
  assert.equal(f.fields.classroom_discipline.value,'好');
  assert.equal(f.fields.classroom_atmosphere.value,'一般');
  assert.equal(f.parts.recommendation.hidden,false);
});

test('skipping teaching methods leaves ratings for manual entry without defaults',()=>{
  const f=fixture();f.form.fire('lecture-assistant-course-confirmed');f.answer('completionIssueAnswer','none');
  f.answer('completionMethodSkip','');
  assert.equal(f.parts.recommendation.hidden,false);
  assert.equal(f.fields.classroom_discipline.value,'');
  assert.equal(f.fields.tm_ppt.checked,false);
  assert.equal(f.fields.quality_case.value,'');
});

test('a partial restored draft retains completed ratings while asking for missing ratings',()=>{
  const f=fixture();
  for(const id of ['course_changes','abnormal_situation','suggestions'])f.fields[id].value='无';
  f.fields.tm_ppt.checked=true;f.fields.classroom_discipline.value='一般';f.fields.classroom_atmosphere.value='非常好';
  f.fields.quality_case.value='推荐';f.draft(31);
  assert.equal(f.parts.ratings.hidden,false);
  assert.deepEqual(f.ratingChoices.map(choice=>choice.checked),[true,false,true,true]);
  assert.equal(f.fields.classroom_discipline.value,'一般');
  assert.equal(f.fields.quality_case.value,'推荐');
});

test('adding PPT manually reopens missing courseware evaluation without removing recommendation',()=>{
  const f=fixture();
  for(const id of ['course_changes','abnormal_situation','suggestions'])f.fields[id].value='无';
  f.fields.tm_lecture.checked=true;
  for(const id of ['classroom_discipline','classroom_atmosphere','overall_effect'])f.fields[id].value='非常好';
  f.fields.courseware_quality.value='无';f.fields.quality_case.value='推荐';f.draft(32);
  assert.equal(f.parts.summary.hidden,false);
  f.fields.tm_ppt.checked=true;f.fields.courseware_quality.value='';
  f.form.fire('change',{target:f.fields.tm_ppt});
  assert.equal(f.parts.ratings.hidden,false);
  assert.equal(f.parts['courseware-choice'].hidden,false);
  assert.equal(f.fields.quality_case.value,'推荐');
});
