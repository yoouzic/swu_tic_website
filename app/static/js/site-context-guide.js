/* Human-confirmed evidence flow; numerical model scores never become UI probabilities. */
(function(root,factory){
    const api=factory();
    if(typeof module==='object'&&module.exports)module.exports=api;
    else {root.SiteContextGuide=api;root.addEventListener('DOMContentLoaded',()=>api.mount());}
})(typeof window==='object'?window:globalThis,function(){
    'use strict';
    function defaultView(data){
        if(!data?.candidates?.length)return 'manual';
        return data.guidance?.mode==='clarify'?'question':data.guidance?.mode==='expand'?'expand':'courses';
    }
    function visibleCourses(data,expanded=false){
        if(expanded||!data.guidance)return data.candidates||[];
        const ids=new Set(data.guidance.candidate_ids||[]);
        return data.candidates.filter(c=>ids.has(c.candidate_id));
    }
    function createController(adapter){
        let record=null,facts={},asked=[],questionCount=0,revision=0,busy=false,manual=false,history=[];
        const render=state=>adapter.render({...state,record,facts:{...facts},asked:[...asked],canGoBack:history.length>0});
        const remember=()=>history.push({facts:{...facts},asked:[...asked],questionCount});
        async function refresh(){
            if(!record)return;
            const id=record.id,version=++revision;
            render({status:'loading'});
            try{
                const data=await adapter.fetchSuggestions(id,{facts:{...facts},asked:[...asked],question_count:questionCount});
                if(version!==revision||id!==record?.id)return;
                render({status:manual?'manual':'ready',data});
            }catch(error){if(version===revision)render({status:'error',message:error.message});}
        }
        async function load(next){
            const switching=record?.id!==next.id;
            const contextChanged=record?.building!==next.building||record?.room_number!==next.room_number;
            record=next;
            if(busy&&!switching){if(next.course_confirmed)render({status:'confirmed'});return;}
            if(switching||contextChanged){
                revision++;facts={};asked=[];manual=false;history=[];
                if(switching)questionCount=0;
                if(next.building){facts.building=String(next.building);if(next.room_number)facts.room_number=String(next.room_number);}
            }
            if(next.course_confirmed||next.submitted){revision++;render({status:'confirmed'});return;}
            return refresh();
        }
        async function answer(kind,value,{question=true}={}){
            remember();
            if(question)questionCount=Math.min(2,questionCount+1);
            if((kind==='building'||kind==='room_number')&&facts[kind]!==value){
                for(const dependent of ['teacher_name','period'])delete facts[dependent];
                asked=asked.filter(k=>!['teacher_name','period'].includes(k));
            }
            facts[kind]=value;manual=false;
            if(!asked.includes(kind)&&asked.length<4)asked.push(kind);
            return refresh();
        }
        async function skip(kind){
            remember();
            questionCount=Math.min(2,questionCount+1);
            if(!asked.includes(kind)&&asked.length<4)asked.push(kind);
            return refresh();
        }
        async function clearFact(kind){
            remember();
            delete facts[kind];asked=asked.filter(k=>k!==kind);manual=false;
            if(kind==='building'||kind==='room_number'){
                for(const dependent of ['teacher_name','period'])delete facts[dependent];
                asked=asked.filter(k=>!['teacher_name','period'].includes(k));
            }
            return refresh();
        }
        async function back(){
            if(busy||!history.length)return;
            const previous=history.pop();facts=previous.facts;asked=previous.asked;questionCount=previous.questionCount;manual=false;
            return refresh();
        }
        async function resetFacts(){facts={};asked=[];questionCount=0;history=[];manual=false;return refresh();}
        function reject(){revision++;manual=true;render({status:'manual'});}
        function invalidate(){revision++;manual=true;render({status:'manual'});}
        async function confirm(course){
            if(busy||!record)return;
            const id=record.id,version=++revision;busy=true;render({status:'confirming'});
            const isCurrent=()=>version===revision&&id===record?.id;
            try{
                const next=await adapter.confirmCourse(id,course,isCurrent);
                if(!isCurrent())return;
                record=next;render({status:'confirmed'});
            }catch(error){if(isCurrent())render({status:'error',message:error.message});}
            finally{busy=false;}
        }
        return {load,answer,skip,back,clearFact,resetFacts,reject,invalidate,confirm};
    }

    function mount(){
        const root=document.querySelector('[data-site-context-guide]');if(!root)return;
        const status=root.querySelector('[data-context-status]');
        const content=root.querySelector('[data-context-content]');
        const form=document.getElementById('lectureForm');
        let latest=null,showCourses=null,expanded=false;
        function button(label,fn){const b=document.createElement('button');b.type='button';b.className='btn btn-outline-secondary btn-sm';b.textContent=label;b.addEventListener('click',fn);return b;}
        function container(){const d=document.createElement('div');d.className='site-context__actions';return d;}
        async function request(url,options={}){
            const headers=new Headers(options.headers||{});headers.set('X-CSRFToken',document.querySelector('meta[name="csrf-token"]').content);
            const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
            try{
                const response=await fetch(url,{...options,headers,signal:controller.signal});
                const result=await response.json();if(!response.ok||!result.success)throw new Error(result.message||'暂未完成，请重试。');return result;
            }finally{clearTimeout(timer);}
        }
        function render(state){
            root.hidden=false;content.replaceChildren();latest=state;
            root.dataset.confirmBusy=String(state.status==='confirming');
            const locked=state.status==='confirming'||root.dataset.findBusy==='true';
            for(const id of ['siteBuilding','siteRoom'])document.getElementById(id).disabled=locked;
            const manualFind=document.querySelector('[data-site-find]');if(manualFind)manualFind.disabled=locked;
            if(state.status==='confirmed'){root.hidden=true;return;}
            if(state.canGoBack&&state.status!=='confirming')content.append(button('返回上一题',async()=>{
                expanded=false;showCourses=null;await controller.back();
                document.getElementById('siteBuilding').value=latest?.facts?.building||'';
                document.getElementById('siteRoom').value=latest?.facts?.room_number||latest?.record?.room_number||'';
            }));
            if(!['manual','error'].includes(state.status)&&state.data?.candidates?.length&&Object.keys(state.facts).length){
                const facts=container(),names={building:'楼栋',room_number:'门牌',period:'节次',teacher_name:'教师'};
                for(const [kind,value] of Object.entries(state.facts))facts.append(button(`${names[kind]}：${kind==='room_number'?value.padStart(4,'0'):value} · 修改`,()=>{
                    const field=kind==='building'?'siteBuilding':kind==='room_number'?'siteRoom':null;
                    if(field)document.getElementById(field).value='';
                    showCourses=null;expanded=false;controller.clearFact(kind);
                }));
                content.append(facts);
            }
            if(state.status==='loading'){status.textContent='正在结合门牌、时间和位置查找…';return;}
            if(state.status==='confirming'){status.textContent='正在核对并确认这门课…';return;}
            if(state.status==='manual'){
                status.textContent='可修正教学楼和门牌号，或让助手帮你缩小范围。';
                const correction=document.querySelector('[data-site-corrections]');if(correction)correction.open=true;
                content.append(button('重新参考现场线索',()=>{showCourses=null;expanded=false;controller.resetFacts();}));return;
            }
            if(state.status==='error'){
                status.textContent=state.message||'暂未找到，请手动填写教室。';document.querySelector('[data-site-corrections]').open=true;
                content.append(button('重试',()=>controller.resetFacts()));return;
            }
            const data=state.data;
            if(data.source_unavailable){status.textContent='课表暂不可用，可手动填写课程或稍后补充。';document.querySelector('[data-site-corrections]').open=true;return;}
            if(!data.candidates.length){status.textContent='未找到课程，请核对教学楼和门牌。';document.querySelector('[data-site-corrections]').open=true;return;}
            const guidance=data.guidance,question=guidance?guidance.question:data.question;
            const expand=()=>{expanded=true;showCourses=true;render(state);};
            if(defaultView(data)==='expand'&&!expanded){
                status.textContent=guidance.reason;
                content.append(button('扩大范围查找',expand),button('修改信息',()=>controller.reject()),
                    button('重新开始核对',()=>{showCourses=null;expanded=false;controller.resetFacts();}));return;
            }
            const coursesFirst=expanded||(showCourses??(defaultView(data)==='courses'));
            status.textContent=expanded?'范围已扩大，包含远处或位置未核验的课程，请核对。':guidance?.reason||'请核对以下课程。';
            if(question&&!coursesFirst){
                const heading=document.createElement('h3');heading.className='h6';heading.textContent=question.prompt;content.append(heading);
                const progress=document.createElement('p');progress.className='text-muted small';
                progress.textContent=`补充问题 ${Math.min(2,(guidance?.questions_used||0)+1)} / 2`;content.append(progress);
                let offset=0;const options=container(),actions=container();content.append(options,actions);
                const addBatch=()=>{
                    for(const option of question.options.slice(offset,offset+6))options.append(button(option.label,()=>{
                        const field=question.kind==='building'?'siteBuilding':question.kind==='room_number'?'siteRoom':null;
                        if(field)document.getElementById(field).value=option.label.replace(/教$/,'');
                        showCourses=null;expanded=false;controller.answer(question.kind,option.value);
                    }));
                    offset+=6;more.hidden=offset>=question.options.length;
                };
                const more=button('更多选项',addBatch);actions.append(more,
                    button('我不知道，跳过此题',()=>{showCourses=null;expanded=false;controller.skip(question.kind);}),
                    button('先看候选课程',()=>{showCourses=true;render(state);}),
                    button('都不是，扩大范围',expand));addBatch();
            }else{
                const courses=visibleCourses(data,expanded);
                const cards=document.createElement('div');cards.className='site-context__cards';content.append(cards);
                let offset=0;const actions=container();
                const addBatch=()=>{
                    for(const course of courses.slice(offset,offset+3)){
                        const b=button('',()=>controller.confirm(course));b.className='site-capture__course';
                        if(course.requires_manual_time)b.disabled=true;
                        const title=document.createElement('strong');title.textContent=course.course_title;
                        const room=/^(\d+)-(\d{3,4})$/.exec(course.room);
                        const classroom=room?`${Number(room[1])}教 · 门牌${room[2].padStart(4,'0')}`:course.room;
                        const detail=document.createElement('span');detail.textContent=`${classroom} · 第${course.period[0]}-${course.period[1]}节 · ${course.teacher_name} · ${course.student_grade_class||''}`;
                        const reason=document.createElement('span');reason.className='site-context__reason';reason.textContent=[...(course.match_reasons||[]),...(course.match_uncertainties||[]),
                            ...(expanded&&course.site_distance_m!=null?[`距定位点约${course.site_distance_m}米`]:[])].join(' · ');
                        const confirm=document.createElement('span');confirm.className='site-context__confirm';confirm.textContent=course.requires_manual_time?'请手动核对节次':'确认这门课';b.append(title,detail,reason,confirm);cards.append(b);
                    }
                    offset+=3;more.hidden=offset>=courses.length;
                };
                const more=button('更多同范围课程',addBatch);actions.append(more,
                    button(expanded?'返回优先推荐':'扩大范围查找',()=>{if(expanded){expanded=false;showCourses=null;render(state);}else expand();}),
                    button('都不是，修改信息',()=>controller.reject()));
                if(question&&!expanded)actions.append(button('帮我缩小范围',()=>{showCourses=false;render(state);}));
                content.append(actions);addBatch();
            }
        }
        const controller=createController({render,
            fetchSuggestions:async(id,body)=>(await request(`/user/api/site-capture/${id}/suggestions`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).data,
            confirmCourse:async(id,course,isCurrent)=>{
                const match=/^([\w\u4e00-\u9fff]+)-([A-Za-z]?\d{3,4})$/.exec(course.room);
                if(!match)throw new Error('请手动核对课表中的完整教室。');
                const lock=form._lectureMutationLock||(form._lectureMutationLock={count:0,previousInert:form.inert});
                lock.count++;form.inert=true;
                try{
                    if(await window.saveLectureFormDraft()===false)throw new Error('草稿保存失败，请检查网络后重试；当前填写内容已保留。');
                    if(!isCurrent())throw new Error('记录已切换，请重新确认。');
                    await request(`/user/api/site-capture/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({building:match[1],room_number:match[2]})});
                    if(!isCurrent())throw new Error('记录已切换，请重新确认。');
                    const result=await request(`/user/api/site-capture/${id}/confirm`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:course.candidate_id})});
                    if(isCurrent()){
                        if(await window.loadLectureFormDraft()===false)throw new Error('课程已确认，但草稿暂未加载，请刷新后重试。');
                        if(isCurrent())form.dispatchEvent(new CustomEvent('lecture-assistant-course-confirmed'));
                    }
                    return result.data;
                }finally{
                    if(--lock.count===0){form.inert=lock.previousInert;delete form._lectureMutationLock;}
                }
            }
        });
        window.addEventListener('lecture-site-capture-loaded',event=>{
            if(latest?.record?.id!==event.detail.id){showCourses=null;expanded=false;}
            controller.load(event.detail);
        });
        for(const id of ['siteBuilding','siteRoom'])document.getElementById(id)?.addEventListener('input',()=>controller.invalidate());
        form.addEventListener('reset',()=>{root.hidden=true;content.replaceChildren();controller.reject();root.hidden=true;});
        window.addEventListener('lecture-site-building-picked',event=>{
            if(event.detail.captureId!==latest?.record?.id||root.dataset.confirmBusy==='true'||root.dataset.findBusy==='true')return;
            document.getElementById('siteBuilding').value=event.detail.building;
            showCourses=null;expanded=false;controller.answer('building',event.detail.building,{question:false});
        });
        return controller;
    }
    return {createController,defaultView,visibleCourses,mount};
});
