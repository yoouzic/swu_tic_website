const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const template = fs.readFileSync('app/templates/user/lecture_form.html', 'utf8');
const source = template.slice(template.indexOf('async function loadLectureFormDraft()'),
                              template.indexOf('function scheduleLectureFormDraftSave()'));
const deleteSource = template.slice(template.indexOf('async function deleteLectureFormDraft()'),
                                    template.indexOf('// 表单验证'));
const scheduleSource = template.slice(template.indexOf('function scheduleLectureFormDraftSave()'),
                                      template.indexOf('async function deleteLectureFormDraft()'));
const resetSource = template.match(/form\.addEventListener\('reset', function\(\) \{[\s\S]*?\n    \}\);/)[0];
function fixture(fetch) {
  const applied = [], statuses = [], events = [];
  const context = {
    lectureFormIsEditMode:false, lectureFormDraftSubmitting:false,
    lectureFormDraftLoadSequence:0, lectureFormDraftDirty:true,
    lectureFormDraftEditRevision:0, lectureFormDraftSavePromise:null,
    lectureFormDraftUrl:'/draft', lectureFormDraftSaveController:null, lectureFormDraftTimer:null,
    fetch, AbortController,
    collectLectureFormDraftData:() => ({course_feedback:'保留当前人工评价'}),
    applyLectureFormDraftData:data => applied.push(data),
    setLectureDraftStatus:(text,kind) => statuses.push({text,kind}),
    formatLectureDraftTime:time => time,
    window:{lectureFormSiteCaptureId:7, dispatchEvent:event => events.push(event),clearTimeout(){},setTimeout(){}},
    CustomEvent:class {constructor(type,options){this.type=type;this.detail=options.detail;}},
    console:{error(){}},
  };
  let reset;
  context.form={addEventListener:(_type,callback)=>{reset=callback;}};
  vm.createContext(context); vm.runInContext(source + scheduleSource + deleteSource + resetSource, context);
  return {context,applied,statuses,events,reset:()=>reset()};
}
const response = (body,ok=true) => ({ok,json:async()=>body});

test('successful draft save returns a success signal to photo navigation', async()=>{
  const f=fixture(async()=>response({success:true,updated_at:'now'}));
  assert.equal(await f.context.saveLectureFormDraft(),true);
  assert.equal(f.context.lectureFormDraftDirty,false);
  assert.equal(f.context.lectureFormDraftSaveController,null);
});
test('failed draft save returns failure and retains dirty input for retry', async()=>{
  const f=fixture(async()=>response({success:false,message:'暂不可保存'},false));
  assert.equal(await f.context.saveLectureFormDraft(),false);
  assert.equal(f.context.lectureFormDraftDirty,true);
  assert.equal(f.statuses.at(-1).kind,'error');
  assert.equal(f.applied.length,0);
});
test('aborted draft save cannot signal successful navigation', async()=>{
  const f=fixture(async()=>{const error=new Error('aborted');error.name='AbortError';throw error;});
  assert.equal(await f.context.saveLectureFormDraft(),false);
});
test('successful draft load returns true only after applying data and notifying record restoration', async()=>{
  const f=fixture(async()=>response({success:true,exists:true,data:{site_capture_id:8},updated_at:'now'}));
  assert.equal(await f.context.loadLectureFormDraft(),true);
  assert.equal(f.applied[0].site_capture_id,8);
  assert.equal(f.events[0].type,'lecture-form-draft-loaded');
});
test('failed draft load returns false without replacing current form', async()=>{
  const f=fixture(async()=>response({success:false,message:'读取失败'},false));
  assert.equal(await f.context.loadLectureFormDraft(),false);
  assert.equal(f.applied.length,0);
  assert.equal(f.statuses.at(-1).kind,'error');
});
test('HTTP failure must not apply a success-shaped stale draft', async()=>{
  const f=fixture(async()=>response({success:true,exists:true,data:{site_capture_id:99}},false));
  assert.equal(await f.context.loadLectureFormDraft(),false);
  assert.equal(f.applied.length,0);
});
test('missing draft returns false without changing current data', async()=>{
  const f=fixture(async()=>response({success:true,exists:false}));
  assert.equal(await f.context.loadLectureFormDraft(),false);
  assert.equal(f.applied.length,0);
});
test('a superseded draft response returns false and cannot undo the newer load', async()=>{
  let resolve;let calls=0;
  const f=fixture(async()=>++calls===1?new Promise(done=>{resolve=done;}):response({success:true,exists:true,data:{site_capture_id:8}}));
  const older=f.context.loadLectureFormDraft();
  assert.equal(await f.context.loadLectureFormDraft(),true);
  resolve(response({success:true,exists:true,data:{site_capture_id:7}}));
  assert.equal(await older,false);
  assert.deepEqual(f.applied.map(data=>data.site_capture_id),[8]);
});
test('clearing a draft invalidates an already pending load before it can restore the old photo', async()=>{
  let resolve;
  const f=fixture((_url,options={})=>options.method==='DELETE'
    ?response({success:true}):new Promise(done=>{resolve=done;}));
  const older=f.context.loadLectureFormDraft();
  await f.context.deleteLectureFormDraft();
  resolve(response({success:true,exists:true,data:{site_capture_id:7}}));
  assert.equal(await older,false);
  assert.equal(f.applied.length,0);
  assert.equal(f.events.length,0);
});
test('reset invalidates pending loads immediately rather than waiting for its deferred DELETE', async()=>{
  let resolve;
  const f=fixture(()=>new Promise(done=>{resolve=done;}));
  const older=f.context.loadLectureFormDraft();
  f.reset();
  resolve(response({success:true,exists:true,data:{site_capture_id:7}}));
  assert.equal(await older,false);
  assert.equal(f.applied.length,0);
});
test('reentrant saves share one write and flush newer input before allowing navigation', async()=>{
  let resolve;const writes=[];let current='旧评价';
  const f=fixture((_url,options)=>{
    writes.push(JSON.parse(options.body).data.course_feedback);
    return writes.length===1?new Promise(done=>{resolve=done;}):response({success:true});
  });
  f.context.collectLectureFormDraftData=()=>({course_feedback:current});
  const first=f.context.saveLectureFormDraft();
  current='刚填写的新评价';f.context.scheduleLectureFormDraftSave();
  const second=f.context.saveLectureFormDraft();
  assert.equal(writes.length,1,'there must not be overlapping PUT requests for one form');
  resolve(response({success:true}));
  assert.equal(await first,true);assert.equal(await second,true);
  assert.deepEqual(writes,['旧评价','刚填写的新评价']);
  assert.equal(f.context.lectureFormDraftDirty,false);
});
test('input edited during a save is persisted before that save can report safe-to-leave success', async()=>{
  let resolve;let current='旧评价';const writes=[];
  const f=fixture((_url,options)=>{
    writes.push(JSON.parse(options.body).data.course_feedback);
    return writes.length===1?new Promise(done=>{resolve=done;}):response({success:true});
  });
  f.context.collectLectureFormDraftData=()=>({course_feedback:current});
  const saving=f.context.saveLectureFormDraft();
  current='保存期间继续输入';f.context.scheduleLectureFormDraftSave();
  resolve(response({success:true}));
  assert.equal(await saving,true);
  assert.deepEqual(writes,['旧评价','保存期间继续输入']);
  assert.equal(f.context.lectureFormDraftDirty,false);
});
test('late draft reads cannot replace fields edited while the request was pending', async()=>{
  let resolve;
  const f=fixture(()=>new Promise(done=>{resolve=done;}));
  const loading=f.context.loadLectureFormDraft();
  f.context.scheduleLectureFormDraftSave();
  resolve(response({success:true,exists:true,data:{course_feedback:'服务器旧评价',site_capture_id:7}}));
  assert.equal(await loading,false);
  assert.equal(f.applied.length,0);
  assert.equal(f.context.lectureFormDraftDirty,true);
});
