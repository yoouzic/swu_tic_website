const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=()=>fs.readFileSync('app/static/js/site-context-guide.js','utf8');
function controller(adapter){
  const module={exports:{}};vm.runInNewContext(source(),{module,exports:module.exports});
  return module.exports.createController(adapter);
}

test('late suggestions from another record cannot replace the current course cards',async()=>{
  const pending={},shown=[];
  const c=controller({fetchSuggestions:id=>new Promise(resolve=>pending[id]=resolve),render:s=>shown.push(s)});
  const first=c.load({id:1});const second=c.load({id:2});
  pending[2]({candidates:[{candidate_id:'second'}]});await second;
  pending[1]({candidates:[{candidate_id:'first'}]});await first;
  assert.equal(shown.at(-1).data.candidates[0].candidate_id,'second');
});

test('correcting a fact reranks and does not treat auto OCR as a confirmed room',async()=>{
  const queries=[];
  const c=controller({fetchSuggestions:async(id,q)=>{queries.push(q);return {candidates:[]};},render(){}});
  await c.load({id:1,room_number:'6090',building:null});
  assert.deepEqual(JSON.parse(JSON.stringify(queries[0].facts)),{});
  await c.answer('room_number','0609');
  assert.equal(queries.at(-1).facts.room_number,'0609');
  await c.resetFacts();assert.deepEqual(JSON.parse(JSON.stringify(queries.at(-1).facts)),{});
});

test('switching records during context confirmation prevents final confirm',async()=>{
  let release,finalConfirm=false;
  const c=controller({fetchSuggestions:async()=>({candidates:[]}),render(){},
    confirmCourse:async(id,course,isCurrent)=>{await new Promise(r=>release=r);if(isCurrent())finalConfirm=true;return {id,course_confirmed:true};}});
  await c.load({id:1});const confirming=c.confirm({candidate_id:'one'});
  await c.load({id:2});release();await confirming;
  assert.equal(finalConfirm,false);
});

test('changing a human fact during pending suggestions suppresses earlier results',async()=>{
  const pending=[],shown=[];
  const c=controller({fetchSuggestions:(id,q)=>new Promise(resolve=>pending.push(resolve)),render:s=>shown.push(s)});
  const load=c.load({id:1});const answer=c.answer('building','8');
  pending[1]({candidates:[{candidate_id:'eight'}]});await answer;
  pending[0]({candidates:[{candidate_id:'old'}]});await load;
  assert.equal(shown.at(-1).data.candidates[0].candidate_id,'eight');
});

test('correcting building clears dependent teacher and period but preserves photographed room',async()=>{
  const queries=[];
  const c=controller({fetchSuggestions:async(id,q)=>{queries.push(q);return {candidates:[]};},render(){}});
  await c.load({id:1});await c.answer('building','25');await c.answer('room_number','0609');
  await c.answer('teacher_name','甲');await c.answer('period','1-2');await c.answer('building','8');
  assert.deepEqual(JSON.parse(JSON.stringify(queries.at(-1).facts)),{building:'8',room_number:'0609'});
  assert.ok(!queries.at(-1).asked.includes('teacher_name'));
});

test('a single wrong answer can be removed without resetting the other clues',async()=>{
  const queries=[];
  const c=controller({fetchSuggestions:async(id,q)=>{queries.push(q);return {candidates:[]};},render(){}});
  await c.load({id:1});await c.answer('building','8');await c.answer('room_number','6090');
  assert.equal(typeof c.clearFact,'function');await c.clearFact('room_number');
  assert.deepEqual(JSON.parse(JSON.stringify(queries.at(-1).facts)),{building:'8'});
});

test('default experience offers course cards before optional diagnostic questions',()=>{
  const module={exports:{}};vm.runInNewContext(source(),{module,exports:module.exports});
  assert.equal(typeof module.exports.defaultView,'function');
  assert.equal(module.exports.defaultView({candidates:[{candidate_id:'a'}],question:{kind:'room_number'}}),'courses');
  assert.equal(module.exports.defaultView({candidates:[],question:null}),'manual');
});

test('server uncertainty triggers question while distant candidates stay out of default cards',()=>{
  const module={exports:{}};vm.runInNewContext(source(),{module,exports:module.exports});const api=module.exports;
  const data={candidates:[{candidate_id:'near'},{candidate_id:'far'}],
    guidance:{mode:'clarify',candidate_ids:['near'],question:{kind:'building'}}};
  assert.equal(api.defaultView(data),'question');
  assert.equal(typeof api.visibleCourses,'function');
  assert.equal(api.visibleCourses(data,false).length,1);
  assert.equal(api.visibleCourses(data,true).length,2);
});

test('skipping and answering use two-question budget and correcting facts does not reset it',async()=>{
  const queries=[];
  const c=controller({fetchSuggestions:async(id,q)=>{queries.push(q);return {candidates:[]};},render(){}});
  await c.load({id:1});assert.equal(typeof c.skip,'function');
  await c.skip('building');await c.answer('room_number','0609');await c.clearFact('room_number');
  assert.equal(queries.at(-1).question_count,2);
  await c.answer('building','8',{question:false});assert.equal(queries.at(-1).question_count,2);
  await c.load({id:2});assert.equal(queries.at(-1).question_count,0);
});

test('back restores the preceding site question including an unanswered skipped field',async()=>{
  const queries=[];const c=controller({fetchSuggestions:async(id,q)=>{queries.push(q);return {candidates:[]};},render(){}});
  await c.load({id:1});await c.skip('building');await c.answer('teacher_name','甲');
  assert.equal(typeof c.back,'function');await c.back();
  assert.equal(queries.at(-1).question_count,1);
  assert.deepEqual(JSON.parse(JSON.stringify(queries.at(-1).facts)),{});
  assert.deepEqual(JSON.parse(JSON.stringify(queries.at(-1).asked)),['building']);
});

function confirmationAdapter(loadDraft,{saveDraft=async()=>true,requestHandler,formNode}={}){
  const text=source().replace(/\r\n/g,'\n');const start=text.indexOf('confirmCourse:async(id,course,isCurrent)=>{');
  const end=text.indexOf('\n        });\n        window.addEventListener',start);
  assert.ok(start>=0&&end>start,'real mounted confirmation adapter must be located');
  let notifications=0;const requests=[];const form=formNode||{inert:false};
  form.dispatchEvent=()=>{notifications++;};
  const adapter=vm.runInNewContext(`({${text.slice(start,end)}\n})`,{
    request:async(url,options)=>{requests.push({url,options});if(requestHandler)return requestHandler(url,options,form);return {success:true,data:{id:7,course_confirmed:true}};},
    window:{loadLectureFormDraft:loadDraft,saveLectureFormDraft:saveDraft},form,
    CustomEvent:class{constructor(type){this.type=type;}},
  });
  return {confirm:adapter.confirmCourse,requests,form,notifications:()=>notifications};
}

test('confirmed server course with draft-load failure never announces completed field filling',async()=>{
  const adapter=confirmationAdapter(async()=>false);
  await assert.rejects(adapter.confirm(7,{room:'8-609',candidate_id:'one'},()=>true),/草稿暂未加载/);
  assert.equal(adapter.notifications(),0);
});

test('switching record while the confirmation draft loads suppresses the old course completion event',async()=>{
  let release,current=true;
  const adapter=confirmationAdapter(()=>new Promise(resolve=>release=resolve));
  const pending=adapter.confirm(7,{room:'8-609',candidate_id:'one'},()=>current);
  await new Promise(resolve=>setImmediate(resolve));current=false;release(true);await pending;
  assert.equal(adapter.notifications(),0);
});

test('successful confirmed course draft load still notifies manual completion guide once',async()=>{
  const adapter=confirmationAdapter(async()=>true);
  const result=await adapter.confirm(7,{room:'8-609',candidate_id:'one'},()=>true);
  assert.equal(result.course_confirmed,true);
  assert.equal(adapter.notifications(),1);
  assert.equal(adapter.requests.length,2);
});

test('confirming a course saves current manual evaluation and witnesses before mutation and loading',async()=>{
  let visible={course_feedback:'刚写的评价',student_signature1:'新见证人'},persisted={course_feedback:'旧评价',student_signature1:'旧见证人'};
  const order=[];
  const adapter=confirmationAdapter(async()=>{order.push('load');visible={...persisted};return true;},{
    saveDraft:async()=>{order.push('save');persisted={...visible};return true;},
    requestHandler:async(url,options)=>{order.push(options.method);return {success:true,data:{id:7,course_confirmed:true}};},
  });
  await adapter.confirm(7,{room:'8-609',candidate_id:'one'},()=>true);
  assert.deepEqual(order,['save','PATCH','POST','load']);
  assert.deepEqual(visible,{course_feedback:'刚写的评价',student_signature1:'新见证人'});
});

test('a failed save before course confirmation keeps manual input and performs no server mutation',async()=>{
  let visible='刚写的评价';
  const adapter=confirmationAdapter(async()=>{visible='旧评价';return true;},{saveDraft:async()=>false});
  await assert.rejects(adapter.confirm(7,{room:'8-609',candidate_id:'one'},()=>true),/保存失败/);
  assert.equal(adapter.requests.length,0);
  assert.equal(adapter.notifications(),0);
  assert.equal(visible,'刚写的评价');
});

test('switching record while saving prevents both old course mutations',async()=>{
  let release,current=true;
  const adapter=confirmationAdapter(async()=>true,{saveDraft:()=>new Promise(resolve=>release=resolve)});
  const pending=adapter.confirm(7,{room:'8-609',candidate_id:'one'},()=>current);
  await new Promise(resolve=>setImmediate(resolve));
  current=false;if(release)release(true);
  await assert.rejects(pending,/记录已切换/);
  assert.equal(adapter.requests.length,0);
});

test('explicit course confirmation keeps the form inert throughout save mutation and load then restores it',async()=>{
  const form={inert:false},states=[];
  const adapter=confirmationAdapter(async()=>{states.push(form.inert);return true;},{formNode:form,
    saveDraft:async()=>{states.push(form.inert);return true;},
    requestHandler:async()=>{states.push(form.inert);return {success:true,data:{id:7,course_confirmed:true}};},
  });
  await adapter.confirm(7,{room:'8-609',candidate_id:'one'},()=>true);
  assert.deepEqual(states,[true,true,true,true]);
  assert.equal(form.inert,false);
});
