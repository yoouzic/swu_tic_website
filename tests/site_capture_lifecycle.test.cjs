const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/site-capture.js', 'utf8');

class Node {
  constructor() { this.listeners = {}; this.value = ''; this.textContent = ''; this.hidden = false; this.disabled = false; this.dataset = {}; this.children = []; this.scrollCalls=0; }
  addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
  async fire(type, detail) { for (const callback of this.listeners[type] || []) await callback({type, detail, target:this}); }
  click() { return this.fire('click'); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  scrollIntoView() {this.scrollCalls++;}
  dispatchEvent(event) { for (const callback of this.listeners[event.type] || []) callback(event); }
}
async function captureFixture(route, {scheduleReady=true}={}) {
  const nodes = Object.fromEntries(['status','camera','open','retake','manual','cancel','new','shoot','find','classroom','later','review','record','photo','time','pending','history','corrections','tools','courses','video','map-row'].map(id => [id, new Node()]));
  if(!scheduleReady){delete nodes.find;delete nodes.courses;}
  const inputs = Object.fromEntries(['siteRoom','siteBuilding','site_capture_id','siteBuildings','basic-information','course-information','classroom-review','feedback-information','signature-information'].map(id => [id, new Node()]));
  const form = new Node(), assistant = new Node(), reviewGuide = new Node(), completion = new Node(), nav = new Node(), window = new Node();form.inert=false;
  assistant.parentElement=form;reviewGuide.parentElement=form;completion.parentElement=reviewGuide;
  reviewGuide.hidden=true;completion.hidden=true;
  const timers = [];
  const root = new Node(); root.querySelector = selector => nodes[selector.match(/data-site-(.*?)\]/)[1]];
  root.dataset.scheduleReady=String(scheduleReady);
  form.querySelector = () => new Node();
  let ready;
  const document = {
    addEventListener(type, callback) { if (type === 'DOMContentLoaded') ready = callback; },
    getElementById(id) { return id === 'lectureForm' ? form : inputs[id]; },
    querySelector(selector) {
      if (selector === '[data-site-capture]') return root;
      if (selector === '[data-listening-assistant]') return assistant;
      if (selector === '[data-site-review-guide]') return reviewGuide;
      if (selector === '[data-form-completion]') return completion;
      if (selector === '.form-section-nav') return nav;
      if (selector === 'meta[name="csrf-token"]') return {content:'local'};
      return null;
    },
    createElement(tag) {
      if (tag === 'canvas') return {getContext:() => ({drawImage(){}}), toBlob:callback => callback(new Blob(['photo'], {type:'image/jpeg'}))};
      return new Node();
    },
  };
  const sampler = {stop(){},best:() => null,samples:() => []};
  window.SiteLocation = {start:() => sampler};
  window.saveLectureFormDraft = async () => true;
  window.loadLectureFormDraft = async () => true;
  const calls = [];
  const fetch = async (url, options={}) => {
    calls.push({url,options});
    if (url === '/user/api/site-capture/settings') return {ok:true,json:async () => ({success:true,data:{buildings:[]}})};
    if (url === '/user/api/site-capture/records') return {ok:true,json:async () => ({success:true,data:[]})};
    return route(url, options);
  };
  const navigator = {};
  const context = {document,window,navigator,fetch,Headers,AbortController,Blob,FormData,CustomEvent:class {constructor(type,options={}){this.type=type;this.detail=options.detail;}},setTimeout:callback=>{timers.push(callback);return timers.length;},clearTimeout(){},console};
  vm.runInNewContext(source,context);ready();await Promise.resolve();await Promise.resolve();
  nodes.video.videoWidth = 1280;nodes.video.videoHeight = 720;
  return {nodes,inputs,form,assistant,reviewGuide,completion,window,calls,timers,navigator};
}
const photo = id => ({id,photo_url:`/photo/${id}`,received_at:'2026-10-03T09:00:00',room_number:'601',building:'8',course_confirmed:false});
const response = (data, ok=true) => ({ok,json:async () => data});
const settle = () => new Promise(resolve => setImmediate(resolve));

test('without a timetable classroom confirmation saves and reveals manual fields without course controls', async()=>{
  const fixture=await captureFixture(async(url,options)=>{
    if(url==='/user/api/site-capture/7'&&options.method==='PATCH')return response({success:true,data:photo(7),candidates:[]});
    if(url==='/user/api/site-capture/7')return response({success:true,data:photo(7)});
    throw new Error(url);
  },{scheduleReady:false});
  await fixture.window.fire('lecture-form-draft-loaded',{data:{site_capture_id:7}});await settle();
  await fixture.nodes.classroom.click();
  assert.equal(fixture.inputs['course-information'].hidden,false);
  assert.match(fixture.nodes.status.textContent,/教室已确认/);
  assert.equal(fixture.assistant.hidden,true);
  assert.equal(fixture.nodes.corrections.open,true);
  assert.equal(fixture.nodes.find,undefined);
  assert.equal(fixture.nodes.courses,undefined);
  assert.ok(!fixture.calls.some(call=>/suggestions|\/confirm/.test(call.url)));
});

test('failed new-photo upload retains the camera and reports the error when the active draft is still the old record', async () => {
  const fixture = await captureFixture(async url => {
    if (url === '/user/api/site-capture') return response({success:false,message:'照片上传失败，请重试'},false);
    if (url === '/user/api/lecture_form_draft') return response({success:true,data:{site_capture_id:7}});
    if (url === '/user/api/site-capture/7') return response({success:true,data:photo(7)});
    throw new Error(url);
  });
  await fixture.window.fire('lecture-form-draft-loaded',{data:{site_capture_id:7}});
  await settle();
  fixture.nodes.camera.hidden = false;
  await fixture.nodes.shoot.click();
  assert.match(fixture.nodes.status.textContent,/照片上传失败/);
  assert.equal(fixture.nodes.camera.hidden,false);
  assert.equal(fixture.inputs.site_capture_id.value,'7');
});

test('a restore started before clearing the form cannot resurrect the old photo afterward', async () => {
  let resolve;
  const fixture = await captureFixture(url => {
    if (url === '/user/api/site-capture/7') return new Promise(done => {resolve=done;});
    throw new Error(url);
  });
  await fixture.window.fire('lecture-form-draft-loaded',{data:{site_capture_id:7}});
  await fixture.form.fire('reset');
  resolve(response({success:true,data:photo(7)}));
  await settle();
  assert.equal(fixture.nodes.record.hidden,true);
  assert.equal(fixture.nodes.open.hidden,false);
  assert.notEqual(fixture.inputs.site_capture_id.value,'7');
});

test('a saved new capture recovered after response failure also reloads its own form draft', async () => {
  let uploaded=false;
  const fixture = await captureFixture(async url => {
    if (url === '/user/api/site-capture') {uploaded=true;throw new Error('response lost');}
    if (url === '/user/api/lecture_form_draft') return response({success:true,data:{site_capture_id:uploaded?8:7}});
    if (url === '/user/api/site-capture/7') return response({success:true,data:photo(7)});
    if (url === '/user/api/site-capture/8') return response({success:true,data:photo(8)});
    throw new Error(url);
  });
  await fixture.window.fire('lecture-form-draft-loaded',{data:{site_capture_id:7}});
  await settle();
  let loads=0;fixture.window.loadLectureFormDraft=async () => {loads++;};
  await fixture.nodes.shoot.click();
  assert.equal(fixture.inputs.site_capture_id.value,'8');
  assert.equal(loads,1);
});

test('retaking a photo first saves current manual evaluation before opening the camera', async () => {
  const fixture=await captureFixture(async url=> {
    if(url==='/user/api/site-capture/7')return response({success:true,data:photo(7)});
    throw new Error(url);
  });
  await fixture.window.fire('lecture-form-draft-loaded',{data:{site_capture_id:7}});await settle();
  const order=[];
  fixture.window.saveLectureFormDraft=async()=>{order.push('save');return true;};
  fixture.navigator.mediaDevices={getUserMedia:async()=>{order.push('camera');return {getTracks:()=>[]};}};
  await fixture.nodes.retake.click();await settle();
  assert.deepEqual(order,['save','camera']);
});

test('a failed draft save prevents switching to a new record and starting its camera', async () => {
  const fixture=await captureFixture(async url=> {
    if(url==='/user/api/site-capture/7')return response({success:true,data:photo(7)});
    throw new Error(url);
  });
  await fixture.window.fire('lecture-form-draft-loaded',{data:{site_capture_id:7}});await settle();
  let cameras=0;
  fixture.window.saveLectureFormDraft=async()=>false;
  fixture.navigator.mediaDevices={getUserMedia:async()=>{cameras++;return {getTracks:()=>[]};}};
  await fixture.nodes.new.click();await settle();
  assert.equal(cameras,0);
  assert.match(fixture.nodes.status.textContent,/保存失败/);
});

test('a failed draft save keeps evaluation visible when postponing the current record', async () => {
  const fixture=await captureFixture(async url=> {
    if(url==='/user/api/site-capture/7')return response({success:true,data:{...photo(7),course_confirmed:true}});
    throw new Error(url);
  });
  await fixture.window.fire('lecture-form-draft-loaded',{data:{site_capture_id:7}});await settle();
  await fixture.nodes.review.click();fixture.window.saveLectureFormDraft=async()=>false;
  await fixture.nodes.later.click();await settle();
  assert.equal(fixture.inputs['classroom-review'].hidden,false);
  assert.match(fixture.nodes.status.textContent,/保存失败/);
});

test('a failed new-record camera attempt does not turn a later retake into a new record', async()=>{
  const fixture=await captureFixture(async url=>{
    if(url==='/user/api/site-capture/7')return response({success:true,data:photo(7)});
    if(url==='/user/api/site-capture')return response({success:true,data:photo(8)});
    throw new Error(url);
  });
  await fixture.window.fire('lecture-form-draft-loaded',{data:{site_capture_id:7}});await settle();
  fixture.navigator.mediaDevices={getUserMedia:async()=>{throw Object.assign(new Error('denied'),{name:'NotAllowedError'});}};
  await fixture.nodes.new.click();await settle();
  fixture.navigator.mediaDevices.getUserMedia=async()=>({getTracks:()=>[]});
  await fixture.nodes.retake.click();await settle();await fixture.nodes.shoot.click();
  const body=fixture.calls.find(call=>call.url==='/user/api/site-capture').options.body;
  assert.equal(body.get('new_record'),null);
  assert.equal(body.get('replace_capture_id'),'7');
});

test('known successful photo upload with draft-load failure reports the saved record without retrying the upload recovery protocol',async()=>{
  const fixture=await captureFixture(async url=>{
    if(url==='/user/api/site-capture')return response({success:true,data:photo(8)});
    if(url==='/user/api/lecture_form_draft')return response({success:true,data:{site_capture_id:8}});
    if(url==='/user/api/site-capture/8')return response({success:true,data:photo(8)});
    throw new Error(url);
  });
  let loads=0;fixture.window.loadLectureFormDraft=async()=>{loads++;return false;};
  await fixture.nodes.shoot.click();
  assert.equal(loads,1);
  assert.match(fixture.nodes.status.textContent,/已保存.*草稿暂未加载/);
  assert.equal(fixture.inputs.site_capture_id.value,'8');
});

test('explicit photo rejection never adopts an unrelated new draft as a successful upload',async()=>{
  const fixture=await captureFixture(async url=>{
    if(url==='/user/api/site-capture')return {...response({success:false,message:'照片无法读取'},false),status:400};
    if(url==='/user/api/lecture_form_draft')return response({success:true,data:{site_capture_id:8}});
    if(url==='/user/api/site-capture/8')return response({success:true,data:photo(8)});
    throw new Error(url);
  });
  await fixture.nodes.shoot.click();
  assert.match(fixture.nodes.status.textContent,/照片无法读取/);
  assert.equal(fixture.calls.filter(call=>call.url==='/user/api/lecture_form_draft').length,0);
});

test('a non-JSON 4xx photo rejection is not mistaken for an uncertain successful commit',async()=>{
  const fixture=await captureFixture(async url=>{
    if(url==='/user/api/site-capture')return {ok:false,status:400,json:async()=>{throw new SyntaxError('HTML response');}};
    if(url==='/user/api/lecture_form_draft')return response({success:true,data:{site_capture_id:8}});
    if(url==='/user/api/site-capture/8')return response({success:true,data:photo(8)});
    throw new Error(url);
  });
  await fixture.nodes.shoot.click();
  assert.equal(fixture.calls.filter(call=>call.url==='/user/api/lecture_form_draft').length,0);
  assert.equal(fixture.inputs.site_capture_id.value,'');
});

test('a late successful photo-upload response does not restore a record after the user cleared the form',async()=>{
  let resolve;
  const fixture=await captureFixture(url=>{
    if(url==='/user/api/site-capture')return new Promise(done=>{resolve=done;});
    throw new Error(url);
  });
  const upload=fixture.nodes.shoot.click();await settle();
  await fixture.form.fire('reset');
  resolve(response({success:true,data:photo(8)}));await upload;await settle();
  assert.equal(fixture.nodes.record.hidden,true);
  assert.equal(fixture.window.lectureFormSiteCaptureId,null);
});

test('a late camera permission result is stopped after clearing the form',async()=>{
  let resolve,stopped=0;
  const fixture=await captureFixture(url=>{throw new Error(url);});
  fixture.navigator.mediaDevices={getUserMedia:()=>new Promise(done=>{resolve=done;})};
  const open=fixture.nodes.open.click();await settle();await fixture.form.fire('reset');
  resolve({getTracks:()=>[{stop(){stopped++;}}]});await open;
  assert.equal(fixture.nodes.camera.hidden,true);
  assert.equal(stopped,1);
});

test('loading a draft without a photo binding clears the previously displayed record association',async()=>{
  const fixture=await captureFixture(async url=>{
    if(url==='/user/api/site-capture/7')return response({success:true,data:photo(7)});
    throw new Error(url);
  });
  await fixture.window.fire('lecture-form-draft-loaded',{data:{site_capture_id:7}});await settle();
  await fixture.window.fire('lecture-form-draft-loaded',{data:{course_feedback:'新草稿人工反馈'}});await settle();
  assert.equal(fixture.nodes.record.hidden,true);
  assert.equal(fixture.window.lectureFormSiteCaptureId,null);
  assert.equal(fixture.inputs.site_capture_id.value,'');
});

test('finding courses saves current manually entered evaluation before PATCH and reloading',async()=>{
  let visible='刚刚手写的评价',persisted='上次的评价';const order=[];
  const fixture=await captureFixture(async(url,options)=>{
    if(url==='/user/api/site-capture/7'){
      if(options.method==='PATCH')order.push('PATCH');
      return response({success:true,data:photo(7),candidates:[],message:'已保存'});
    }
    throw new Error(url);
  });
  await fixture.window.fire('lecture-form-draft-loaded',{data:{site_capture_id:7}});await settle();
  await fixture.nodes.manual.click();
  fixture.window.saveLectureFormDraft=async()=>{order.push('save');persisted=visible;return true;};
  fixture.window.loadLectureFormDraft=async()=>{order.push('load');visible=persisted;return true;};
  await fixture.nodes.find.click();
  assert.deepEqual(order,['save','PATCH','load']);assert.equal(visible,'刚刚手写的评价');
});

test('failed save blocks manual course lookup without PATCH or overwriting evaluation',async()=>{
  let patches=0,visible='刚刚手写的评价';
  const fixture=await captureFixture(async(url,options)=>{
    if(url==='/user/api/site-capture/7'){
      if(options.method==='PATCH')patches++;
      return response({success:true,data:photo(7),candidates:[]});
    }
    throw new Error(url);
  });
  await fixture.window.fire('lecture-form-draft-loaded',{data:{site_capture_id:7}});await settle();
  fixture.window.saveLectureFormDraft=async()=>false;
  fixture.window.loadLectureFormDraft=async()=>{visible='旧评价';return true;};
  await fixture.nodes.find.click();
  assert.equal(patches,0);assert.equal(visible,'刚刚手写的评价');
  assert.match(fixture.nodes.status.textContent,/保存失败/);
});

test('manual course lookup holds form inert for save PATCH and load and then restores prior state',async()=>{
  let fixture;const states=[];
  fixture=await captureFixture(async(url,options)=>{
    if(url==='/user/api/site-capture/7'){
      if(options.method==='PATCH')states.push(fixture.form.inert);
      return response({success:true,data:photo(7),candidates:[]});
    }
    throw new Error(url);
  });
  await fixture.window.fire('lecture-form-draft-loaded',{data:{site_capture_id:7}});await settle();
  fixture.window.saveLectureFormDraft=async()=>{states.push(fixture.form.inert);return true;};
  fixture.window.loadLectureFormDraft=async()=>{states.push(fixture.form.inert);return true;};
  await fixture.nodes.find.click();
  assert.deepEqual(states,[true,true,true]);assert.equal(fixture.form.inert,false);
});

test('fallback course card saves evaluation typed after lookup before confirming and loading',async()=>{
  let visible='查询前评价',persisted='旧评价',confirms=0;
  const candidate={candidate_id:'one',course_title:'课程',teacher_name:'教师',period:[1,2],student_grade_class:'一班'};
  const fixture=await captureFixture(async(url,options)=>{
    if(url==='/user/api/site-capture/7/confirm'){confirms++;return response({success:true,data:{...photo(7),course_confirmed:true}});}
    if(url==='/user/api/site-capture/7')return response({success:true,data:photo(7),candidates:[candidate]});
    throw new Error(url);
  });
  await fixture.window.fire('lecture-form-draft-loaded',{data:{site_capture_id:7}});await settle();
  fixture.window.saveLectureFormDraft=async()=>{persisted=visible;return true;};
  fixture.window.loadLectureFormDraft=async()=>{visible=persisted;return true;};
  await fixture.nodes.find.click();visible='查询后刚写的评价';
  await fixture.nodes.courses.children[0].click();
  assert.equal(confirms,1);assert.equal(visible,'查询后刚写的评价');
});

test('failed save before a fallback course card prevents confirmation and preserves current evaluation',async()=>{
  let visible='查询前评价',persisted='旧评价',confirms=0;
  const candidate={candidate_id:'one',course_title:'课程',teacher_name:'教师',period:[1,2]};
  const fixture=await captureFixture(async(url,options)=>{
    if(url==='/user/api/site-capture/7/confirm'){confirms++;return response({success:true,data:{...photo(7),course_confirmed:true}});}
    if(url==='/user/api/site-capture/7')return response({success:true,data:photo(7),candidates:[candidate]});
    throw new Error(url);
  });
  await fixture.window.fire('lecture-form-draft-loaded',{data:{site_capture_id:7}});await settle();
  fixture.window.saveLectureFormDraft=async()=>{persisted=visible;return true;};
  fixture.window.loadLectureFormDraft=async()=>{visible=persisted;return true;};
  await fixture.nodes.find.click();visible='查询后刚写的评价';fixture.window.saveLectureFormDraft=async()=>false;
  await fixture.nodes.courses.children[0].click();
  assert.equal(confirms,0);assert.equal(visible,'查询后刚写的评价');
  assert.match(fixture.nodes.status.textContent,/保存失败/);
});

test('retaking after editing evaluation while the camera is open saves the new value again before uploading',async()=>{
  let visible='打开相机前评价',persisted='旧评价';const order=[];
  const fixture=await captureFixture(async(url,options)=>{
    if(url==='/user/api/site-capture/7')return response({success:true,data:photo(7)});
    if(url==='/user/api/site-capture'){order.push(`upload:${persisted}`);return response({success:true,data:photo(8)});}
    throw new Error(url);
  });
  await fixture.window.fire('lecture-form-draft-loaded',{data:{site_capture_id:7}});await settle();
  fixture.window.saveLectureFormDraft=async()=>{persisted=visible;order.push(`save:${visible}`);return true;};
  fixture.window.loadLectureFormDraft=async()=>{visible=persisted;return true;};
  fixture.navigator.mediaDevices={getUserMedia:async()=>({getTracks:()=>[]})};
  await fixture.nodes.retake.click();await settle();assert.equal(fixture.form.inert,false);
  visible='相机打开后刚修改的评价';await fixture.nodes.shoot.click();
  assert.deepEqual(order,['save:打开相机前评价','save:相机打开后刚修改的评价','upload:相机打开后刚修改的评价']);
  assert.equal(visible,'相机打开后刚修改的评价');
});

test('failed save while shooting an existing record prevents uploading and preserves the evaluation and camera',async()=>{
  let uploads=0;
  const fixture=await captureFixture(async url=>{
    if(url==='/user/api/site-capture/7')return response({success:true,data:photo(7)});
    if(url==='/user/api/site-capture'){uploads++;return response({success:true,data:photo(8)});}
    throw new Error(url);
  });
  await fixture.window.fire('lecture-form-draft-loaded',{data:{site_capture_id:7}});await settle();
  fixture.nodes.camera.hidden=false;fixture.window.saveLectureFormDraft=async()=>false;
  await fixture.nodes.shoot.click();
  assert.equal(uploads,0);assert.equal(fixture.nodes.camera.hidden,false);
  assert.match(fixture.nodes.status.textContent,/保存失败/);assert.equal(fixture.form.inert,false);
});

function contextConfirmation(fixture){
  const text=fs.readFileSync('app/static/js/site-context-guide.js','utf8').replace(/\r\n/g,'\n');
  const start=text.indexOf('confirmCourse:async(id,course,isCurrent)=>{');
  const end=text.indexOf('\n        });\n        window.addEventListener',start);
  assert.ok(start>=0&&end>start);
  return vm.runInNewContext(`({${text.slice(start,end)}\n})`,{
    request:async()=>({success:true,data:{id:7,course_confirmed:true}}),window:fixture.window,form:fixture.form,
    CustomEvent:class{constructor(type){this.type=type;}},
  }).confirmCourse;
}

test('overlapping context and photo actions keep form locked until both finish and restore its original state',async()=>{
  const fixture=await captureFixture(async url=>{
    if(url==='/user/api/site-capture/7')return response({success:true,data:photo(7)});
    throw new Error(url);
  });
  await fixture.window.fire('lecture-form-draft-loaded',{data:{site_capture_id:7}});await settle();
  const saves=[];fixture.window.saveLectureFormDraft=()=>new Promise(resolve=>saves.push(resolve));
  const confirming=contextConfirmation(fixture)(7,{room:'8-609',candidate_id:'one'},()=>true);await settle();
  const later=fixture.nodes.later.click();await settle();assert.equal(saves.length,2);
  saves[0](true);await confirming;assert.equal(fixture.form.inert,true);
  saves[1](true);await later;assert.equal(fixture.form.inert,false);
});

function rendered(node){
  for(let current=node;current;current=current.parentElement)if(current.hidden)return false;
  return true;
}

test('confirmed photo review reveals the independent evaluation guide and scrolls to its questions',async()=>{
  const fixture=await captureFixture(async url=>{
    if(url==='/user/api/site-capture/7')return response({success:true,data:{...photo(7),course_confirmed:true}});
    throw new Error(url);
  });
  await fixture.window.fire('lecture-form-draft-loaded',{data:{site_capture_id:7}});await settle();
  fixture.completion.hidden=false;
  assert.equal(rendered(fixture.completion),false);
  await fixture.nodes.review.click();
  assert.equal(fixture.assistant.hidden,true);
  assert.equal(fixture.reviewGuide.hidden,false);
  assert.equal(rendered(fixture.completion),true);
  assert.equal(fixture.completion.scrollCalls,1);
  assert.equal(fixture.inputs['classroom-review'].scrollCalls,0);
});

test('postponing or clearing a photo review hides its evaluation guide wrapper again',async()=>{
  const fixture=await captureFixture(async url=>{
    if(url==='/user/api/site-capture/7')return response({success:true,data:{...photo(7),course_confirmed:true}});
    throw new Error(url);
  });
  await fixture.window.fire('lecture-form-draft-loaded',{data:{site_capture_id:7}});await settle();
  fixture.completion.hidden=false;await fixture.nodes.review.click();
  assert.equal(fixture.reviewGuide.hidden,false);
  await fixture.nodes.later.click();assert.equal(fixture.reviewGuide.hidden,true);
  await fixture.nodes.review.click();await fixture.form.fire('reset');
  assert.equal(fixture.reviewGuide.hidden,true);
});

test('photo review still scrolls to classroom fields if the evaluation guide has not been activated',async()=>{
  const fixture=await captureFixture(async url=>{
    if(url==='/user/api/site-capture/7')return response({success:true,data:{...photo(7),course_confirmed:true}});
    throw new Error(url);
  });
  await fixture.window.fire('lecture-form-draft-loaded',{data:{site_capture_id:7}});await settle();
  await fixture.nodes.review.click();
  assert.equal(fixture.reviewGuide.hidden,false);
  assert.equal(fixture.completion.scrollCalls,0);
  assert.equal(fixture.inputs['classroom-review'].scrollCalls,1);
});
