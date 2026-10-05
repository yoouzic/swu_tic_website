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
    querySelector(){return null;},focus(){},matches(){return false;}};
}
function fixture(){
  const ids=['course_changes','abnormal_situation','suggestions','quality_case','classroom_discipline','classroom_atmosphere','courseware_quality','overall_effect','teaching_method','tm_ppt','tm_lecture','tm_interactive','course_feedback','student_signature1','student_signature2','contact_phone1','contact_phone2','class_period','start_period','assistant_payload'];
  const fields=Object.fromEntries(ids.map(id=>[id,node(id)]));
  for(const id of ['tm_ppt','tm_lecture','tm_interactive'])fields[id].type='checkbox';
  const panel=node(),form=node(),window=node();
  const parts=Object.fromEntries(['issues','recommendation','summary','checklist','missing','count'].map(key=>[key,node()]));
  panel.querySelector=selector=>parts[selector.match(/data-completion-(.*?)\]/)?.[1]];
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
  return {fields,panel,parts,form,window,draft,answer};
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
