const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('app/static/js/site-capture.js','utf8');
function showRecordContext(record){
  const nodes=Object.fromEntries(['record','open','photo','time','new','review','map-row','corrections','tools'].map(key=>[key,{}]));
  const inputs={siteRoom:{value:'601'},siteBuilding:{value:'10'}};
  const ctx={record,recordRevision:0,window:{},el:key=>nodes[key],status(){},document:{getElementById:key=>inputs[key]}};
  vm.runInNewContext(source.slice(source.indexOf('        function showRecord('),source.indexOf('        async function restore(')),ctx);
  return {ctx,inputs,nodes};
}
test('late photo metadata refresh preserves manually typed building',()=>{
  const {ctx,inputs}=showRecordContext({id:7});
  ctx.showRecord({id:7,photo_url:'/photo',received_at:'2026-10-02T09:00:00',room_number:'601',building:null});
  assert.equal(inputs.siteBuilding.value,'10');
  assert.equal(inputs.siteRoom.value,'601');
});
test('switching to another lesson replaces room context',()=>{
  const {ctx,inputs}=showRecordContext({id:7});
  ctx.showRecord({id:8,photo_url:'/photo',received_at:'2026-10-02T09:00:00',room_number:'302',building:'12'});
  assert.equal(inputs.siteBuilding.value,'12');
  assert.equal(inputs.siteRoom.value,'302');
});

test('Find locks manual context until response and keeps a concurrent confirmation locked',async()=>{
  const inputs=[{disabled:false},{disabled:false}],guide={dataset:{}};
  const ctx={document:{getElementById:()=>inputs.shift(),querySelector:()=>guide}};
  const kept=[...inputs];
  const start=source.indexOf('        async function withContextLock(');
  const end=source.indexOf('        function showRecord(',start);
  vm.runInNewContext(source.slice(start,end),ctx);
  let release;
  const pending=ctx.withContextLock(()=>new Promise(resolve=>release=resolve));
  assert.ok(kept.every(input=>input.disabled));
  assert.equal(guide.inert,true);
  guide.dataset.confirmBusy='true';release();await pending;
  assert.ok(kept.every(input=>input.disabled));
  assert.equal(guide.dataset.findBusy,'false');
  assert.equal(guide.inert,false);
});

test('unconfirmed record hides evaluation and exposes classroom tools; confirmed record reverses this',()=>{
  const {ctx,nodes}=showRecordContext(null);
  const record={id:8,photo_url:'/photo',received_at:'2026-10-02T09:00:00',room_number:'302',building:'12'};
  ctx.showRecord(record);
  assert.equal(nodes.review.hidden,true);
  assert.equal(nodes['map-row'].hidden,false);
  ctx.showRecord({...record,course_confirmed:true});
  assert.equal(nodes.review.hidden,false);
  assert.equal(nodes['map-row'].hidden,true);
  assert.equal(nodes.corrections.hidden,true);
});
