const {test}=require('node:test');
const assert=require('node:assert/strict');
const map=require('../app/static/js/site-building-map.js');
test('measured label boxes avoid neighbours and remain inside the viewport',()=>{
 const bounds={x:0,y:0,width:200,height:150},occupied=[];
 for(const x of [2,100,100,198]){
  const box=map.placeMapLabel(x,65,48,22,bounds,occupied,1);
  assert.ok(box);
  assert.ok(box.x>=0&&box.x+box.width<=200&&box.y>=0&&box.y+box.height<=150);
  for(const old of occupied)assert.ok(box.x>=old.x+old.width||old.x>=box.x+box.width||box.y>=old.y+old.height||old.y>=box.y+box.height);
  occupied.push(box);
 }
});
test('a crowded label never overwrites an existing selection marker',()=>{
 assert.equal(map.placeMapLabel(50,50,40,20,{x:0,y:0,width:100,height:100},[{x:0,y:0,width:100,height:100}],1),null);
});

test('an unobstructed building keeps its number centred on its own anchor',()=>{
 const box=map.placeMapLabel(100,80,40,20,{x:0,y:0,width:200,height:150},[],1);
 assert.equal(box.x+box.width/2,100);assert.equal(box.y+box.height/2,80);
});
test('concave building anchors stay inside the footprint rather than the courtyard',()=>{
 const ring=[[0,0],[10,0],[10,2],[2,2],[2,10],[0,10],[0,0]];
 const [x,y]=map.buildingAnchor([ring]);
 assert.ok(x>0&&y>0&&x<10&&y<10&&(x<2||y<2));
});
