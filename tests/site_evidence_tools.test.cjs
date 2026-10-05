const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
function crop(){const p='../app/static/js/site-door-crop.js';return fs.existsSync('app/static/js/site-door-crop.js')?require(p):{};}
test('dragging backwards clamps the selected door region inside the photo',()=>{
  assert.equal(typeof crop().selection,'function');
  const r=crop().selection({x:.8,y:.7},{x:-.1,y:.2});
  assert.equal(r.x,0);assert.equal(r.y,.2);assert.equal(r.width,.8);assert.ok(Math.abs(r.height-.5)<1e-12);
});

function map(){return fs.existsSync('app/static/js/site-building-map.js')?require('../app/static/js/site-building-map.js'):{};}
test('a located A building must not place the B model on that same footprint',()=>{
  const data={buildings:{features:[{properties:{code:'4',model_variants:['A']}}]}};
  assert.equal(map().selectionView(data,'4','A').mode,'map');
  assert.equal(map().selectionView(data,'4','B').mode,'model-preview');
});
test('building choices match timetable scope and omit explicitly ignored location codes',()=>{
  const data={buildings:{features:[{properties:{code:'8',name:'8教'}},{properties:{code:'29',name:'29教'}}]},nearby:{buildings:[]},schedule_buildings:['1','8','99','F','G','MC1','MC2']};
  assert.deepEqual(map().choices(data).map(b=>b.code),['1','8']);
});
test('unlocated models remain visible without inventing a geographic feature',()=>{
  const data={buildings:{features:[{properties:{code:'25'}}]}};
  const original=JSON.stringify(data);
  assert.equal(map().selectionView(data,'1').mode,'model-preview');
  assert.equal(map().selectionView(data,'25').mode,'map');
  assert.equal(map().selectionView(data,'99').mode,'unmapped');
  assert.equal(JSON.stringify(data),original);
  for(const code of ['4','19']){
    const a=map().buildingModel(code,'A'),b=map().buildingModel(code,'B');
    assert.notDeepEqual(a.faces,b.faces);assert.notEqual(a.source,b.source);
    assert.deepEqual(a.variants,['A','B']);
  }
});
test('every numeric building in the current timetable has a sourced model',()=>{
  const codes=['1','3','4','5','6','7','8','9','10','11','14','15','16','17','19','23','24','25','27','28','30','31','32','33','35','37','38','39','40','48'];
  const shapes=new Set();
  for(const code of codes){
    const model=map().buildingModel(code);assert.ok(model,code);
    assert.ok(model.faces.length>0&&model.features&&model.source,code);
    for(const f of model.faces){assert.match(f.color,/^#[0-9a-f]{6}$/i);for(const v of f.points)assert.ok(v.every(Number.isFinite));}
    shapes.add(JSON.stringify(model.faces));
  }
  assert.equal(shapes.size,codes.length);
  for(const code of ['99','F','G','MC1','MC2'])assert.equal(map().buildingModel(code),null);
});

test('renumbered buildings disclose the distinction between current photos and timetable codes',()=>{
  for(const code of ['16','19','48'])assert.match(map().buildingModel(code).identityNote,/课表/);
  assert.equal(map().buildingModel('25').identityNote,'');
});

test('legacy music timetable codes select different buildings and correct current-name meshes',()=>{
  assert.deepEqual(map().buildingModel('16').faces,map().buildingModel('19','A').faces);
  assert.deepEqual(map().buildingModel('19').faces,map().buildingModel('19','B').faces);
  assert.deepEqual(map().buildingModel('16').variants,[]);
  const data={buildings:{features:[{properties:{code:'16'}},{properties:{code:'19',model_variants:['B']}}]}};
  assert.equal(map().selectionView(data,'19').mode,'map');
  assert.equal(map().selectionView(data,'19','A').mode,'model-preview');
});
test('model placement aligns the ground plane to a rotated footprint and does not move on zoom',()=>{
  assert.equal(typeof map().modelPlacement,'function');
  const a=.37,c=Math.cos(a),s=Math.sin(a);
  const ring=[[-50,-20],[50,-20],[50,20],[-50,20],[-50,-20]].map(([x,y])=>[300+x*c-y*s,200+x*s+y*c]);
  const model=map().buildingModel('25');
  const first=map().modelPlacement(model,ring,1),zoomed=map().modelPlacement(model,ring,2);
  const ground=model.faces.flatMap(f=>f.points).map(([x,y])=>first.project([x,y,0]));
  for(const [x,y] of ground){
    const u=(x-300)*c+(y-200)*s,v=-(x-300)*s+(y-200)*c;
    assert.ok(Math.abs(u)<=50.001&&Math.abs(v)<=20.001);
  }
  for(const p of model.faces.flatMap(f=>f.points))assert.deepEqual(first.project([p[0],p[1],0]),zoomed.project([p[0],p[1],0]));
  const heights=ground.map(p=>p[1]);assert.ok(Math.max(...heights)>200&&Math.min(...heights)<200);
});
test('researched models have individual geometry and preserve uncertain buildings as footprints',()=>{
  const signatures=new Set();
  for(const code of ['7','8','14','25','26','27','28','29','32','33','37','38']){
    const model=map().buildingModel(code);
    assert.ok(model,code);
    assert.ok(model.source.startsWith('https://www.swu.edu.cn/info/1153/'));
    assert.ok(model.features.length>0);
    signatures.add(JSON.stringify(model.faces));
    for(const face of model.faces)for(const p of face.points)assert.ok(p.every(Number.isFinite));
  }
  assert.equal(signatures.size,12);
  assert.ok(map().buildingModel('10'));
  assert.equal(map().buildingModel('22'),null);
});
test('photo-informed models have distinct geometry, official provenance and no guessed model for other buildings',()=>{
  assert.equal(typeof map().buildingModel,'function');
  const eight=map().buildingModel('8'),twentyFive=map().buildingModel('25');
  assert.ok(eight.source.includes('/2368.htm'));
  assert.ok(twentyFive.source.includes('/2366.htm'));
  assert.ok(eight.faces.length>30&&twentyFive.faces.length>30);
  assert.notDeepEqual(eight.faces,twentyFive.faces);
  for(const m of [eight,twentyFive])for(const f of m.faces)for(const p of f.points)assert.ok(p.length===3&&p.every(Number.isFinite));
  assert.equal(map().buildingModel('46'),null);
});
test('building extrusion preserves the footprint and joins every wall to the lifted roof',()=>{
  assert.equal(typeof map().extrudeFootprint,'function');
  const ring=[[0,0],[40,0],[40,30],[0,30],[0,0]],original=JSON.stringify(ring);
  const shape=map().extrudeFootprint(ring,6,24);
  assert.equal(JSON.stringify(ring),original);
  assert.deepEqual(shape.roof,[[6,-24],[46,-24],[46,6],[6,6]]);
  assert.equal(shape.walls.length,4);
  for(let i=0;i<4;i++)assert.deepEqual(shape.walls[i],[ring[i],ring[(i+1)%4],shape.roof[(i+1)%4],shape.roof[i]]);
  assert.deepEqual(map().extrudeFootprint(ring.slice(0,-1),6,24),shape);
});
test('selected building view centres its bounds with room for a label on wide and tall maps',()=>{
  assert.equal(typeof map().fitView,'function');
  const points=[[100,200],[180,200],[180,260],[100,260]];
  for(const aspect of [1.7,1,.8]){
    const view=map().fitView(points,aspect);
    assert.ok(Math.abs(view.w/view.h-aspect)<1e-8);
    assert.equal(view.x+view.w/2,140);
    assert.equal(view.y+view.h/2,230);
    for(const [x,y] of points){
      assert.ok(x>view.x+view.w*.1&&x<view.x+view.w*.9);
      assert.ok(y>view.y+view.h*.1&&y<view.y+view.h*.9);
    }
  }
  assert.equal(map().fitView([],1),null);
});
test('map search includes unmapped schedule buildings and normalizes leading zeroes',()=>{
  assert.equal(typeof map().choices,'function');
  const data={buildings:{features:[{properties:{code:'8',name:'第八教学楼',aliases:['08','8教']}}]},
    nearby:{buildings:[{code:'8',distance_m:190}],unmapped_buildings:['46']},schedule_buildings:['8','46']};
  assert.equal(map().choices(data,'08')[0].code,'8');
  assert.equal(map().choices(data,'46')[0].mapped,false);
  assert.equal(map().choices(data,'unknown').length,0);
});
test('choosing a building requires explicit confirmation and does not synthesize GPS',()=>{
  assert.equal(typeof map().createSelection,'function');
  const used=[];const c=map().createSelection(code=>used.push(code));
  c.select('8');assert.deepEqual(used,[]);c.confirm();assert.deepEqual(used,['8']);
  c.reset();c.confirm();assert.deepEqual(used,['8']);
});
test('late region OCR cannot change a newly opened record',async()=>{
  assert.equal(typeof crop().createController,'function');
  let release;const shown=[];
  const c=crop().createController({recognize:()=>new Promise(r=>release=r),onResult:r=>shown.push(r)});
  c.load({id:1});const request=c.recognize({x:0,y:0,width:.5,height:.5});c.load({id:2});
  release({data:{id:1}});await request;assert.equal(shown.length,0);
});
test('record revalidation while crop recognition runs suppresses stale results',async()=>{
  assert.equal(typeof crop().createController,'function');
  let release;const shown=[];
  const c=crop().createController({recognize:()=>new Promise(r=>release=r),onResult:r=>shown.push(r)});
  c.load({id:1});const request=c.recognize({x:0,y:0,width:.5,height:.5});
  c.load({id:1,course_confirmed:true});release({data:{id:1}});await request;assert.equal(shown.length,0);
});
