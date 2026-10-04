/* Photo-first drafts: persisted evidence precedes recognition and course lookup. */
(() => {
    'use strict';
    document.addEventListener('DOMContentLoaded', () => {
        const root=document.querySelector('[data-site-capture]');
        if(!root)return;
        const form=document.getElementById('lectureForm');
        const el=key=>root.querySelector(`[data-site-${key}]`);
        let record=null,stream=null,location={},busy=false,reviewMode=false,newRecord=false,recordRevision=0,formRevision=0;
        let locationSession=null;
        function endLocation(){if(locationSession){locationSession.ended=true;locationSession.sampler.stop();}locationSession=null;}
        function beginLocation(){
            endLocation();location={};
            const session={sampler:null,captureId:null,savedAccuracy:Infinity,pending:false,ended:false,
                observationRevision:0,savedRevision:0,lastAttemptRevision:-1};
            async function persist(sample){
                if(!session.captureId||session.ended||session.pending||session.observationRevision<=session.savedRevision||session.observationRevision<=session.lastAttemptRevision)return;
                session.pending=true;
                const revision=session.observationRevision;session.lastAttemptRevision=revision;
                try{
                    const result=await request(`/user/api/site-capture/${session.captureId}/location`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:{...sample,samples:session.sampler.samples()}})});
                    session.savedAccuracy=result.data.location_accuracy;
                    session.savedRevision=revision;
                    if(record?.id===session.captureId)showRecord(result.data);
                }catch(error){/* Photo and manual classroom remain usable. */}
                finally{
                    session.pending=false;
                    const latest=session.sampler?.best();
                    if(latest&&session.observationRevision>session.savedRevision&&!session.ended)setTimeout(()=>persist(latest),800);
                }
            }
            session.persist=persist;
            session.sampler=window.SiteLocation.start({onUpdate:sample=>{
                if(locationSession===session)location=sample;
                persist(sample);
            },onSample:()=>{
                session.observationRevision++;
                if(session.captureId)setTimeout(()=>{const best=session.sampler?.best();if(best)persist(best);},800);
            }});
            locationSession=session;
            location=session.sampler.best()||{};
            return session;
        }
        const status=(text)=>{el('status').textContent=text;};
        const laterSections=['basic-information','course-information','classroom-review','feedback-information','signature-information'];
        const submit=form.querySelector('button[type="submit"]');
        const assistant=document.querySelector('[data-listening-assistant]');
        const reviewGuide=document.querySelector('[data-site-review-guide]');
        const setMode=(review)=>{
            reviewMode=review;
            for(const id of laterSections){const section=document.getElementById(id);if(section)section.hidden=!review;}
            if(assistant)assistant.hidden=!review||!!record?.course_confirmed;
            if(reviewGuide)reviewGuide.hidden=!review;
            if(submit)submit.hidden=!review;
            document.querySelector('.form-section-nav').hidden=!review;
        };
        setMode(false);
        const stop=()=>{stream?.getTracks().forEach(track=>track.stop());stream=null;el('camera').hidden=true;};
        async function request(url,options={}){
            const headers=new Headers(options.headers||{});
            headers.set('X-CSRFToken',document.querySelector('meta[name="csrf-token"]').content);
            const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),20000);
            try{
                const response=await fetch(url,{...options,headers,signal:controller.signal});
                let data;
                try{data=await response.json();}
                catch(cause){
                    const error=new Error('服务响应暂时无法读取，请刷新后重试。');
                    error.definiteFailure=response.status>=400&&response.status<500;
                    throw error;
                }
                if(!response.ok||!data.success){
                    const error=new Error(data.message||'操作未完成，请重试。');
                    error.definiteFailure=response.ok||(response.status>=400&&response.status<500);
                    throw error;
                }
                return data;
            }finally{clearTimeout(timer);}
        }
        async function action(button,fn){
            if(busy)return;busy=true;const text=button.textContent;
            const lock=form._lectureMutationLock||(form._lectureMutationLock={count:0,previousInert:form.inert});
            lock.count++;form.inert=true;
            button.disabled=true;button.textContent='处理中…';
            try{await fn();}catch(error){status(error.name==='AbortError'?'处理较慢，照片可能已保存，请刷新查看。':(error.message||'操作未完成，请重试。'));}
            finally{
                if(--lock.count===0){form.inert=lock.previousInert;delete form._lectureMutationLock;}
                busy=false;button.disabled=button===el('find')&&document.querySelector('[data-site-context-guide]')?.dataset.confirmBusy==='true';button.textContent=text;
            }
        }
        async function ensureDraftSaved(){
            if(await window.saveLectureFormDraft()===false)throw new Error('草稿保存失败，请检查网络后重试；当前填写内容已保留。');
        }
        async function ensureDraftLoaded(){
            if(await window.loadLectureFormDraft()===false)throw new Error('现场记录已保存，但草稿暂未加载，请刷新后重试。');
        }
        async function withContextLock(fn){
            const inputs=['siteBuilding','siteRoom'].map(id=>document.getElementById(id));
            const guide=document.querySelector('[data-site-context-guide]');
            if(guide){guide.dataset.findBusy='true';guide.inert=true;}
            for(const input of inputs)input.disabled=true;
            try{return await fn();}
            finally{
                if(guide){guide.dataset.findBusy='false';guide.inert=false;}
                const locked=guide?.dataset.confirmBusy==='true';
                for(const input of inputs)input.disabled=locked;
            }
        }
        function showRecord(data){
            recordRevision++;
            const switching=!record || record.id!==data.id;
            window.lectureFormSiteCaptureId=data.id;
            const captureInput=document.getElementById('site_capture_id');if(captureInput)captureInput.value=String(data.id);
            record=data;el('record').hidden=false;el('open').disabled=false;el('open').textContent='重拍门牌';el('photo').src=data.photo_url;
            el('new').hidden=false;el('open').hidden=true;
            el('review').hidden=!data.course_confirmed;
            el('map-row').hidden=!!data.course_confirmed;el('corrections').hidden=!!data.course_confirmed;
            if(switching){el('corrections').open=false;el('tools').open=false;}
            el('time').textContent=data.received_at.slice(0,16).replace('T',' ');
            const roomInput=document.getElementById('siteRoom');
            const buildingInput=document.getElementById('siteBuilding');
            if(switching||!roomInput.value)roomInput.value=data.room_number||'';
            if(switching||!buildingInput.value)buildingInput.value=data.building||'';
            status(data.course_confirmed?'课程已确认，可以填写评价。':'照片已保存，待确认课程。');
            window.dispatchEvent?.(new CustomEvent('lecture-site-capture-loaded',{detail:data}));
        }
        function clearRecord(){
            formRevision++;recordRevision++;newRecord=false;stop();endLocation();record=null;
            window.lectureFormSiteCaptureId=null;
            const captureInput=document.getElementById('site_capture_id');if(captureInput)captureInput.value='';
            el('record').hidden=true;el('new').hidden=true;el('open').disabled=false;
            el('open').textContent='拍门牌';el('open').hidden=false;el('review').hidden=true;el('time').textContent='';
            status('拍摄门牌，查找本次课程。');el('courses').replaceChildren();setMode(false);
        }
        async function restore(id){
            const revision=++recordRevision;
            const result=await request(`/user/api/site-capture/${id}`);
            if(revision!==recordRevision)return;
            showRecord(result.data);
            await listPending();
        }
        async function listPending(){
            const result=await request('/user/api/site-capture/records');
            el('pending').replaceChildren();el('history').hidden=!result.data.some(item=>item.id!==record?.id);
            for(const item of result.data.filter(item=>item.id!==record?.id)){
                const button=document.createElement('button');button.type='button';button.className='site-capture__course';
                button.textContent=`${item.received_at.slice(0,16).replace('T',' ')} · ${item.building?item.building+'-':''}${item.room_number||'门牌待核对'} · ${item.course_title}`;
                button.addEventListener('click',()=>action(button,async()=>{
                    await ensureDraftSaved();
                    await request(`/user/api/site-capture/${item.id}/resume`,{method:'POST'});
                    await ensureDraftLoaded();setMode(false);
                }));el('pending').append(button);
            }
        }
        async function openCamera(){
            const version=formRevision;
            stop();endLocation();location={};
            if(!navigator.mediaDevices?.getUserMedia)throw new Error('当前页面无法打开相机，请使用手机浏览器的安全连接打开。');
            let next;
            try{next=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:1280}},audio:false});}
            catch(error){throw new Error(error.name==='NotAllowedError'?'请允许相机权限后重新拍摄。':'相机未能打开，请检查后重试。');}
            if(version!==formRevision){next.getTracks().forEach(track=>track.stop());return;}
            stream=next;
            el('video').srcObject=stream;el('camera').hidden=false;status('拍清楚门牌号即可，系统会自动识别并查找课程。');
            beginLocation();
        }
        el('open').addEventListener('click',()=>action(el('open'),async()=>{
            if(record&&!newRecord)await ensureDraftSaved();
            await openCamera();
        }));
        el('retake').addEventListener('click',()=>{if(busy)return;newRecord=false;el('open').click();});
        el('manual').addEventListener('click',()=>{setMode(true);if(assistant)assistant.hidden=true;document.getElementById('course-information').scrollIntoView({behavior:'smooth',block:'start'});});
        el('cancel').addEventListener('click',()=>{stop();endLocation();newRecord=false;if(record)showRecord(record);else status('');});
        el('new').addEventListener('click',()=>action(el('new'),async()=>{
            await ensureDraftSaved();newRecord=true;
            try{await openCamera();}catch(error){newRecord=false;throw error;}
        }));
        el('shoot').addEventListener('click',()=>action(el('shoot'),async()=>{
            const version=formRevision,previousId=record?.id;
            if(record){
                await ensureDraftSaved();
                if(version!==formRevision||record?.id!==previousId)throw new Error('记录已切换，请重新拍摄。');
            }
            const video=el('video');if(!video.videoWidth)throw new Error('相机还未就绪，请稍后拍摄。');
            const canvas=document.createElement('canvas');const scale=Math.min(1,1280/Math.max(video.videoWidth,video.videoHeight));
            canvas.width=Math.round(video.videoWidth*scale);canvas.height=Math.round(video.videoHeight*scale);
            canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);
            const capturedAt=new Date().toISOString();
            const session=locationSession?.sampler.best()?locationSession:beginLocation();
            const initialLocation=session.sampler.best()?{...session.sampler.best(),samples:session.sampler.samples()}:{};
            setTimeout(()=>session.sampler.stop(),8000);
            const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',.88));
            if(version!==formRevision)return;
            if(!blob)throw new Error('拍照失败，请重新拍摄。');
            const body=new FormData();body.append('photo',blob,'site.jpg');body.append('captured_at',capturedAt);body.append('location',JSON.stringify(initialLocation));
            if(newRecord)body.append('new_record','1');
            else if(record)body.append('replace_capture_id',String(record.id));
            let saved=false;
            status('正在上传照片并保存现场记录…');
            try{const result=await request('/user/api/site-capture',{method:'POST',body});saved=true;if(version!==formRevision)return;session.captureId=result.data.id;session.savedAccuracy=result.data.location_accuracy||Infinity;const latest=session.sampler.best();if(latest)session.persist(latest);newRecord=false;showRecord(result.data);stop();await ensureDraftLoaded();if(version===formRevision)setMode(false);}
            catch(error){
                if(version!==formRevision)return;
                if(saved||error.definiteFailure)throw error;
                const draft=await request('/user/api/lecture_form_draft');
                if(version!==formRevision)return;
                if(draft.data?.site_capture_id&&String(draft.data.site_capture_id)!==String(previousId)){
                    await restore(draft.data.site_capture_id);if(version!==formRevision)return;newRecord=false;stop();await ensureDraftLoaded();if(version===formRevision)setMode(false);
                }
                else throw error;
            }
        }));
        el('find').addEventListener('click',()=>action(el('find'),()=>withContextLock(async()=>{
            if(!record)throw new Error('请先拍摄门牌。');
            const id=record.id,version=formRevision;
            await ensureDraftSaved();
            if(version!==formRevision||record?.id!==id)throw new Error('记录已切换，请重新查找。');
            status('正在查找课程…');el('courses').replaceChildren();
            const result=await request(`/user/api/site-capture/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({building:document.getElementById('siteBuilding').value,room_number:document.getElementById('siteRoom').value})});
            if(version!==formRevision||record?.id!==id)throw new Error('记录已切换，请重新查找。');
            showRecord(result.data);await ensureDraftLoaded();
            if(window.SiteContextGuide){status('教室已确认，请继续核对助手选项。');return;}
            if(!result.candidates.length){status(result.message);return;}
            status('请选择本次听课的课程。');
            for(const candidate of result.candidates){
                const button=document.createElement('button');button.type='button';button.className='site-capture__course';
                const title=document.createElement('strong');title.textContent=candidate.course_title;
                const details=document.createElement('span');details.textContent=`${candidate.teacher_name} · 第${candidate.period[0]}-${candidate.period[1]}节 · ${candidate.student_grade_class}`;
                button.append(title,details);el('courses').append(button);
                button.addEventListener('click',()=>action(button,async()=>{
                    const currentVersion=formRevision;
                    await ensureDraftSaved();
                    if(currentVersion!==formRevision||record?.id!==id)throw new Error('记录已切换，请重新确认。');
                    const confirmed=await request(`/user/api/site-capture/${id}/confirm`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:candidate.candidate_id})});
                    if(currentVersion!==formRevision||record?.id!==id)throw new Error('记录已切换，请重新确认。');
                    showRecord(confirmed.data);el('courses').replaceChildren();
                    await ensureDraftLoaded();form.dispatchEvent(new CustomEvent('lecture-assistant-course-confirmed'));
                    status('课程已确认。');
                }));
            }
        })));
        el('later').addEventListener('click',()=>action(el('later'),async()=>{
            if(record){await ensureDraftSaved();status(record.course_confirmed?'课程已确认。':'已保存，待确认课程。');setMode(false);}
        }));
        el('review').addEventListener('click',()=>{
            setMode(true);
            const completion=document.querySelector('[data-form-completion]');
            const target=completion&&!completion.hidden?completion:document.getElementById('classroom-review');
            target.scrollIntoView({behavior:'smooth',block:target===completion?'center':'start'});
        });
        form.addEventListener('invalid',()=>setMode(true),true);
        form.addEventListener('submit',event=>{
            if(!record || !record.building){
                event.preventDefault();event.stopImmediatePropagation();
                status(record?'请先确认教学楼和门牌号。':'请先拍摄门牌并保存现场记录。');
                root.scrollIntoView({behavior:'smooth',block:'start'});
            }
        },true);
        window.addEventListener('lecture-form-draft-loaded',event=>{const id=event.detail?.data?.site_capture_id;if(id)restore(id).catch(()=>status('照片记录暂未加载，请刷新重试。'));else clearRecord();});
        window.addEventListener('lecture-site-ocr-updated',event=>{
            if(event.detail.data.id!==record?.id)return;
            showRecord(event.detail.data);status(event.detail.message);
        });
        form.addEventListener('reset',clearRecord);
        window.addEventListener('pagehide',()=>{stop();endLocation();});
        request('/user/api/site-capture/settings').then(result=>{
            const list=document.getElementById('siteBuildings');for(const building of result.data.buildings){const option=document.createElement('option');option.value=building;list.append(option);}
        }).catch(()=>{});
        listPending().catch(()=>{});
    });
})();
