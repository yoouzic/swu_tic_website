const {test}=require('node:test');
const assert=require('node:assert/strict');
const {start}=require('../app/static/js/site-location.js');
function setup(){
  let success,error,cleared=0,timeout,clock=100000;
  const geo={watchPosition(s,e,options){success=s;error=e;assert.equal(options.maximumAge,0);assert.equal(options.enableHighAccuracy,true);return 0;},clearWatch(id){assert.equal(id,0);cleared++;}};
  const sampler=start({geo,now:()=>clock,setTimer:fn=>{timeout=fn;return 1;},clearTimer(){}});
  const point=(latitude,accuracy,timestamp)=>{clock++;success({coords:{latitude,longitude:106.43,accuracy},timestamp:timestamp??clock});};
  return {sampler,point,error:()=>error({code:1}),timeout:()=>timeout(),cleared:()=>cleared};
}
test('selects observed best accuracy without inventing averaged coordinates',()=>{
  const s=setup();s.point(29.82,180);s.point(29.82001,25);s.point(29.82002,70);
  assert.equal(s.sampler.best().accuracy,25);assert.equal(s.sampler.best().latitude,29.82001);
  s.sampler.stop();assert.equal(s.cleared(),1);
});
test('rejects stale fixes and isolated large jumps',()=>{
  const s=setup();s.point(29.82,40);s.point(29.9,5);s.point(29.82001,20,80000);
  assert.equal(s.sampler.best().latitude,29.82);
  s.point(29.90001,6);assert.equal(s.sampler.best().latitude,29.90001);
});
test('permission denied terminates sampling without fabricating a location',()=>{
  const s=setup();s.error();assert.equal(s.sampler.best(),null);assert.equal(s.cleared(),1);
  s.point(29.82,10);assert.equal(s.sampler.best(),null);
});
test('time budget preserves best fix and clears watcher even with id zero',()=>{
  const s=setup();s.point(29.82,80);s.timeout();assert.equal(s.cleared(),1);assert.equal(s.sampler.best().accuracy,80);
});
test('a previously good observation expires instead of following the user to another room',()=>{
  let clock=10000;
  const sampler=start({now:()=>clock,geo:{watchPosition(fn){fn({coords:{latitude:29.82,longitude:106.43,accuracy:8},timestamp:clock});return 0;},clearWatch(){}},setTimer:()=>1,clearTimer(){}});
  assert.equal(sampler.best().accuracy,8);clock=16000;assert.equal(sampler.best(),null);sampler.stop();
});

test('returns bounded raw observations including jumps for robust evidence, never stale samples',()=>{
  const s=setup();s.point(29.82,40);s.point(29.9,5);s.point(29.82,40,80000);
  assert.equal(s.sampler.samples().length,2);
  assert.equal(s.sampler.best().latitude,29.82);
  const copy=s.sampler.samples();copy[0].latitude=0;
  assert.equal(s.sampler.samples()[0].latitude,29.82);
  for(let i=0;i<30;i++)s.point(29.82+i/1e6,40);
  assert.equal(s.sampler.samples().length,12);
});

test('fresh equal or less precise accepted fixes remain available after an earlier fix expires',()=>{
  let clock=100000,success;
  const sampler=start({now:()=>clock,geo:{watchPosition(fn){success=fn;return 1;},clearWatch(){}},setTimer:()=>1,clearTimer(){}});
  const point=(at,accuracy)=>{clock=at;success({coords:{latitude:29.82,longitude:106.43,accuracy},timestamp:at});};
  point(100000,25);point(103000,25);point(106000,80);
  assert.equal(sampler.best()?.timestamp,103000);
  clock=109000;assert.equal(sampler.best()?.timestamp,106000);
  clock=112000;assert.equal(sampler.best(),null);sampler.stop();
});

test('a rejected jump never becomes the live best when the earlier accepted fix expires',()=>{
  let clock=100000,success;
  const sampler=start({now:()=>clock,geo:{watchPosition(fn){success=fn;return 1;},clearWatch(){}},setTimer:()=>1,clearTimer(){}});
  success({coords:{latitude:29.82,longitude:106.43,accuracy:40},timestamp:clock});
  clock=104000;success({coords:{latitude:29.9,longitude:106.43,accuracy:5},timestamp:clock});
  clock=106000;assert.equal(sampler.best(),null);assert.equal(sampler.samples().length,2);sampler.stop();
});

test('sampler exposes whether it can still receive readings after an early stable stop',()=>{
  const s=setup();assert.equal(typeof s.sampler.active,'function');assert.equal(s.sampler.active(),true);
  s.point(29.82,8);s.point(29.82001,9);assert.equal(s.sampler.active(),false);
});
