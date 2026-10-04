/* Region recognition is a machine clue; selecting a rectangle never confirms a room. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;
else {root.SiteDoorCrop=api;root.addEventListener('DOMContentLoaded',()=>api.mount());}
})(typeof window==='object'?window:globalThis,function(){
    'use strict';
    function selection(a,b){
        const clamp=v=>Math.max(0,Math.min(1,v));
        const x=Math.min(clamp(a.x),clamp(b.x)),y=Math.min(clamp(a.y),clamp(b.y));
        return {x,y,width:Math.abs(clamp(a.x)-clamp(b.x)),height:Math.abs(clamp(a.y)-clamp(b.y))};
    }
    function createController(adapter){
        let record=null,revision=0;
        return {load(next){record=next;revision++;},async recognize(region){
            if(!record||record.course_confirmed||record.submitted)return;
            const id=record.id,version=revision;
            const result=await adapter.recognize(id,region);
            if(version===revision&&record.id===id)adapter.onResult(result);
        }};
    }
    function mount(){
        const dialog=document.querySelector('[data-door-crop]');if(!dialog)return;
        const canvas=dialog.querySelector('canvas'),ctx=canvas.getContext('2d');
        const note=dialog.querySelector('[data-crop-status]'),run=dialog.querySelector('[data-crop-run]');
        const open=document.querySelector('[data-site-crop]');
        let record=null,image=null,region=null,anchor=null,busy=false,imageRevision=0;
        function close(){imageRevision++;dialog.close();}
        function draw(){
            if(!image)return;ctx.clearRect(0,0,canvas.width,canvas.height);ctx.drawImage(image,0,0,canvas.width,canvas.height);
            if(region){const {x,y,width:w,height:h}=region;
                ctx.fillStyle='rgba(20,35,50,.45)';ctx.fillRect(0,0,canvas.width,canvas.height);
                ctx.drawImage(image,x*image.width,y*image.height,w*image.width,h*image.height,x*canvas.width,y*canvas.height,w*canvas.width,h*canvas.height);
                ctx.strokeStyle='#70e1b2';ctx.lineWidth=3;ctx.strokeRect(x*canvas.width,y*canvas.height,w*canvas.width,h*canvas.height);
            }
        }
        const controller=createController({recognize:async(id,region)=>{
            const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),20000);
            try{const r=await fetch(`/user/api/site-capture/${id}/ocr-region`,{method:'POST',signal:abort.signal,
                headers:{'Content-Type':'application/json','X-CSRFToken':document.querySelector('meta[name="csrf-token"]').content},body:JSON.stringify({region})});
                const body=await r.json();if(!r.ok||!body.success)throw new Error(body.message||'重新识别失败');return body;
            }finally{clearTimeout(timer);}
        },onResult:result=>{
            close();window.dispatchEvent(new CustomEvent('lecture-site-ocr-updated',{detail:result}));
        }});
        window.addEventListener('lecture-site-capture-loaded',event=>{
            const changed=record?.id!==event.detail.id||event.detail.course_confirmed||event.detail.submitted;
            record=event.detail;
            if(changed){controller.load(record);close();}
            open.disabled=!!(record.course_confirmed||record.submitted);
        });
        document.getElementById('lectureForm').addEventListener('reset',()=>{record=null;controller.load(null);close();});
        open.addEventListener('click',()=>{
            if(!record||busy)return;
            controller.load(record);const version=++imageRevision;image=null;region=null;run.disabled=true;
            note.textContent='正在加载原照片…';dialog.showModal();
            const next=new Image();next.onload=()=>{
                if(version!==imageRevision||!dialog.open)return;
                image=next;canvas.width=next.width;canvas.height=next.height;
                region={x:.15,y:.3,width:.7,height:.3};draw();run.disabled=false;
                note.textContent='拖动框住一行门牌数字；也可用方向键移动，Shift＋方向键调整大小。';
            };next.onerror=()=>{if(version===imageRevision)note.textContent='照片暂未加载，可关闭后重试或手填门牌。';};next.src=record.photo_url;
        });
        const point=e=>{const r=canvas.getBoundingClientRect();return {x:(e.clientX-r.left)/r.width,y:(e.clientY-r.top)/r.height};};
        canvas.addEventListener('pointerdown',e=>{if(!image||busy)return;anchor=point(e);canvas.setPointerCapture(e.pointerId);});
        canvas.addEventListener('pointermove',e=>{if(anchor){region=selection(anchor,point(e));draw();}});
        canvas.addEventListener('pointerup',e=>{if(anchor){region=selection(anchor,point(e));anchor=null;draw();}});
        canvas.addEventListener('pointercancel',()=>{anchor=null;});
        canvas.addEventListener('keydown',e=>{
            if(!region||busy||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key))return;e.preventDefault();
            const dx=e.key==='ArrowLeft'?-.01:e.key==='ArrowRight'?.01:0,dy=e.key==='ArrowUp'?-.01:e.key==='ArrowDown'?.01:0;
            if(e.shiftKey){region.width=Math.max(.02,Math.min(1-region.x,region.width+dx));region.height=Math.max(.02,Math.min(1-region.y,region.height+dy));}
            else{region.x=Math.max(0,Math.min(1-region.width,region.x+dx));region.y=Math.max(0,Math.min(1-region.height,region.y+dy));}draw();
        });
        dialog.querySelector('[data-crop-close]').addEventListener('click',close);
        run.addEventListener('click',async()=>{
            if(busy||!region)return;busy=true;run.disabled=true;note.textContent='正在识别框选区域，原照片会保留…';
            try{await controller.recognize(region);}catch(e){note.textContent=e.name==='AbortError'?'识别较慢，可重试或手动输入门牌。':e.message;}
            finally{busy=false;run.disabled=false;}
        });
    }
    return {selection,createController,mount};
});
