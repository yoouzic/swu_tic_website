/* Local campus context + explicit human building choice. No map click writes GPS. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;
else {root.SiteBuildingMap=api;root.addEventListener('DOMContentLoaded',()=>api.mount());}
})(typeof window==='object'?window:globalThis,function(){
    'use strict';
    const buildingLabel=code=>/^\d+$/.test(code)?code+'教':code;
    const ignoredModelCodes=new Set(['99','F','G','MC1','MC2']);
    const defaultVariant=code=>code==='19'?'B':'A';
    function buildingAnchor(rings){
        const ring=rings[0];
        const inside=([x,y],points)=>{
            let hit=false;
            for(let i=0,j=points.length-1;i<points.length;j=i++){
                const [ax,ay]=points[i],[bx,by]=points[j];
                if((ay>y)!==(by>y)&&x<(bx-ax)*(y-ay)/(by-ay)+ax)hit=!hit;
            }
            return hit;
        };
        const valid=p=>inside(p,ring)&&!rings.slice(1).some(h=>inside(p,h));
        const xs=ring.map(p=>p[0]),ys=ring.map(p=>p[1]),left=Math.min(...xs),top=Math.min(...ys),w=Math.max(...xs)-left,h=Math.max(...ys)-top;
        const centre=[left+w/2,top+h/2];if(valid(centre))return centre;
        let best=null,clearance=-1;
        for(let i=1;i<20;i++)for(let j=1;j<20;j++){
            const p=[left+w*i/20,top+h*j/20];if(!valid(p))continue;
            let distance=Infinity;
            for(const r of rings)for(let a=0,b=r.length-1;a<r.length;b=a++){
                const dx=r[a][0]-r[b][0],dy=r[a][1]-r[b][1];
                const t=Math.max(0,Math.min(1,((p[0]-r[b][0])*dx+(p[1]-r[b][1])*dy)/(dx*dx+dy*dy||1)));
                distance=Math.min(distance,Math.hypot(p[0]-r[b][0]-t*dx,p[1]-r[b][1]-t*dy));
            }
            if(distance>clearance){best=p;clearance=distance;}
        }
        return best||ring[0];
    }
    function placeMapLabel(x,y,width,height,bounds,occupied,scale=1){
        for(const [dx,dy] of [[0,0],[0,-22],[30,0],[-30,0],[0,26],[36,-26],[-36,-26],[36,26],[-36,26],[0,-48],[0,50]]){
            const box={x:Math.max(bounds.x,Math.min(bounds.x+bounds.width-width,x+dx*scale-width/2)),
                y:Math.max(bounds.y,Math.min(bounds.y+bounds.height-height,y+dy*scale-height/2)),width,height};
            if(width>bounds.width||height>bounds.height)continue;
            if(occupied.some(b=>box.x<b.x+b.width&&b.x<box.x+width&&box.y<b.y+b.height&&b.y<box.y+height))continue;
            return box;
        }
        return null;
    }
    function choices(data,query=''){
        const near=new Map((data.nearby.buildings||[]).map(b=>[b.code,b.distance_m]));
        const rows=new Map(data.buildings.features.map(f=>[f.properties.code,{...f.properties,mapped:true,distance:near.get(f.properties.code)}]));
        for(const code of data.schedule_buildings||[])if(!rows.has(code))rows.set(code,{code,name:code+'教',mapped:false,aliases:[]});
        const raw=query.trim().toLowerCase(),q=/^\d+$/.test(raw)?String(Number(raw)):raw;
        const scheduled=new Set(data.schedule_buildings||[]);
        return [...rows.values()].filter(b=>!ignoredModelCodes.has(b.code)&&(!scheduled.size||scheduled.has(b.code)))
            .filter(b=>!q||[b.code,b.name,...(b.aliases||[])].some(v=>String(v).toLowerCase().includes(q)))
            .sort((a,b)=>(a.distance??Infinity)-(b.distance??Infinity)||a.code.localeCompare(b.code,'zh-CN',{numeric:true}));
    }
    // Photo-informed illustrative meshes. Dimensions are design units, not survey data.
    function buildingModel(code,variant=defaultVariant(code)){
        // This timetable retains the old music-building numbers: 16=A, 19=B.
        if(code==='16')return {...buildingModel('19','A'),name:'厚乐楼 A栋（原16教）',variants:[],
            identityNote:'课表沿用原16教楼号，对应现厚乐楼A栋。'};
        const profiles={
            '1':['雨僧楼','2395','对称浅色立面、分段灰色坡屋顶、中央入口'],
            '3':['绩镛楼','2393','长条前楼、灰色坡屋顶、竖向窗间墙'],
            '4':['咏修楼 A栋','2392','低层翼楼、中央突出门厅、灰色坡屋顶'],
            '5':['弘礼楼','2376','浅色楼体、出挑入口、横向外廊'],
            '6':['师元楼','2333','中央三角山花、竖向玻璃带、门廊'],
            '9':['荟文楼 A栋','2349','错层矩形楼体、竖向塔块、架空入口'],
            '10':['荟文楼 B栋','16887','内庭参考：弧形回廊、顶部采光格栅；外墙简化'],
            '11':['田家炳教育书院','2334','浅色双翼、青绿色入口竖条、分段檐口'],
            '15':['至美楼','16901','红色框架、玻璃入口、跨空连梁'],
            '17':['漱溟楼','2389','浅色低层主体、竖向窗格、平屋顶'],
            '19':['厚乐楼 A栋','16902','圆弧端部、深色竖向玻璃带、框形门厅'],
            '23':['润心楼','2360','弧形白色外廊、中央玻璃斜面、入口雨棚'],
            '24':['启智楼','2361','远景参考：平屋顶格架；树木遮挡部分简化'],
            '30':['阳初楼','2388','古典浅色立面、突出端部、深色基座'],
            '31':['兆畦楼','2387','折线围合楼体、阶梯屋顶、灰白立面'],
            '35':['逸夫楼','2331','双翼楼体、弧形柱廊、宽台阶入口'],
            '39':['弘忠楼','2370','黄色宽门廊、水平挑檐、矩形主体'],
            '40':['弘实楼','2369','浅色长条主体、实墙端部、平屋顶'],
            '48':['弘新楼','2357','米黄色网格立面、蓝色窗格、中央入口'],
            '7':['修业楼','2345','低层长条楼体、外廊和横向檐口'],
            '8':['敬德楼','2368','浅色分段楼体、圆弧窗带、入口立柱'],
            '14':['白南楼','2390','横向窗带、竖向框架、宽入口雨棚'],
            '25':['明德楼','2366','高层塔楼、纵向玻璃带、底部柱廊'],
            '26':['弘诚楼','2374','弧形楼面、端部蓝色玻璃带、浅色山墙'],
            '27':['弘信楼','2373','弧形连廊、红色竖向构件、蓝色玻璃端部'],
            '28':['弘义楼','2375','弧形体量、蓝色玻璃端墙、横向窗格'],
            '29':['伴月楼','2332','环抱庭院的弧形楼体、连续窗带'],
            '32':['弘勤楼','2372','圆柱玻璃端部、环形顶盖、矩形主楼'],
            '33':['同庆楼','2386','对称立面、中央弧形山花、入口柱廊'],
            '37':['弘朴楼','2371','狭长楼体、黄色竖向窗间墙、出挑檐口'],
            '38':['明辨楼','2341','浅色古典立面、上下两层柱廊、三角山花']
        };
        if(code==='4'&&variant==='B')profiles['4']=['咏修楼 B栋','16911','现代弧形外廊、阶梯露台、竖向端墙'];
        if(code==='19'&&variant==='B')profiles['19']=['厚乐楼 B栋','16903','高玻璃门厅、浅色框架、外侧窗带'];
        const profile=profiles[code];if(!profile)return null;
        const faces=[];
        const face=(points,color,depth)=>faces.push({points,color,depth});
        function box(x,y,z,w,d,h,color,roof=color){
            face([[x,y,z+h],[x+w,y,z+h],[x+w,y+d,z+h],[x,y+d,z+h]],roof);
            face([[x+w,y,z],[x+w,y+d,z],[x+w,y+d,z+h],[x+w,y,z+h]],color);
            face([[x,y+d,z],[x+w,y+d,z],[x+w,y+d,z+h],[x,y+d,z+h]],color);
        }
        function windows(x,y,z,w,d,h,rows,columns,color){
            for(let r=0;r<rows;r++)for(let c=0;c<columns;c++){
                const xx=x+(c+.18)*w/columns,zz=z+(r+.2)*h/rows;
                face([[xx,y+d+.06,zz],[xx+w/columns*.6,y+d+.06,zz],[xx+w/columns*.6,y+d+.06,zz+h/rows*.65],[xx,y+d+.06,zz+h/rows*.65]],color,x+w/2+y+d+(z+h/2)*.015+.2);
            }
            for(let r=0;r<rows;r++)for(let c=0;c<Math.max(2,Math.round(columns*d/w));c++){
                const n=Math.max(2,Math.round(columns*d/w)),yy=y+(c+.18)*d/n,zz=z+(r+.2)*h/rows;
                face([[x+w+.06,yy,zz],[x+w+.06,yy+d/n*.6,zz],[x+w+.06,yy+d/n*.6,zz+h/rows*.65],[x+w+.06,yy,zz+h/rows*.65]],color,x+w+y+d/2+(z+h/2)*.015+.2);
            }
        }
        function cylinder(x,y,z,r,h,color){
            const ring=Array.from({length:16},(_,i)=>[x+Math.cos(i*Math.PI/8)*r,y+Math.sin(i*Math.PI/8)*r]);
            face(ring.map(([xx,yy])=>[xx,yy,z+h]),color);
            for(let i=0;i<16;i++){const a=ring[i],b=ring[(i+1)%16];face([[...a,z],[...b,z],[...b,z+h],[...a,z+h]],color);}
        }
        function block(x,y,w,d,h,wall,roof,rows,cols,glass='#789293'){
            box(x,y,0,w,d,h,wall,roof);windows(x,y,2,w,d,h-4,rows,cols,glass);
        }
        function arc(radius,thickness,start,end,height,wall,glass,rows=4){
            const n=18;
            for(let i=0;i<n;i++){
                const a=start+(end-start)*i/n,b=start+(end-start)*(i+1)/n;
                const point=(r,t,z)=>[Math.cos(t)*r,Math.sin(t)*r,z];
                const outer=[point(radius,a,0),point(radius,b,0),point(radius,b,height),point(radius,a,height)];
                const inner=[point(radius-thickness,a,0),point(radius-thickness,b,0),point(radius-thickness,b,height),point(radius-thickness,a,height)];
                face(outer,wall);face(inner,wall);
                face([point(radius,a,height),point(radius,b,height),point(radius-thickness,b,height),point(radius-thickness,a,height)],'#ddd9cb');
                const depth=r=>Math.cos((a+b)/2)*r+Math.sin((a+b)/2)*r+height*.0075+.2;
                for(let j=0;j<rows;j++){
                    const z=3+j*(height-5)/rows,h=(height-5)/rows*.56;
                    for(const r of [radius+.05,radius-thickness-.05])face([point(r,a+.015,z),point(r,b-.015,z),point(r,b-.015,z+h),point(r,a+.015,z+h)],glass,depth(r));
                }
                if(i===0||i===n-1){const t=i===0?a:b;face([point(radius,t,0),point(radius-thickness,t,0),point(radius-thickness,t,height),point(radius,t,height)],wall);}
            }
        }
        function columns(xs,y,height,color){for(const x of xs)cylinder(x,y,0,1.3,height,color);}
        function pediment(x,y,z,w,d,h,color){
            face([[x,y+d,z],[x+w,y+d,z],[x+w/2,y+d,z+h]],color);
            face([[x,y,z],[x+w/2,y,z+h],[x+w/2,y+d,z+h],[x,y+d,z]],'#e2d6bd');
            face([[x+w/2,y,z+h],[x+w,y,z],[x+w,y+d,z],[x+w/2,y+d,z+h]],'#d1bea0');
        }
        function pitchedRoof(x,y,z,w,d,h){
            face([[x,y,z],[x+w,y,z],[x+w,y+d/2,z+h],[x,y+d/2,z+h]],'#737976');
            face([[x,y+d/2,z+h],[x+w,y+d/2,z+h],[x+w,y+d,z],[x,y+d,z]],'#666b67');
            face([[x+w,y,z],[x+w,y+d,z],[x+w,y+d/2,z+h]],'#d2c9b6');
        }
        if(code==='8'){
            box(-36,-15,0,72,24,43,'#ddcdb0','#eae0ca');windows(-36,-15,3,72,24,38,4,10,'#768c8b');
            box(-36,9,0,19,22,37,'#d0b999','#e8dcc5');windows(-36,9,3,19,22,31,3,3,'#647f81');
            box(17,9,0,19,22,37,'#ddcdb0','#e8dcc5');windows(17,9,3,19,22,31,3,3,'#647f81');
            cylinder(0,14,16,12,32,'#d6c3a4');
            for(let i=0;i<4;i++){cylinder(0,14,18+i*7,12.15,4.5,'#849a99');cylinder(0,14,23+i*7,12.6,1.2,'#e9ddc5');}
            cylinder(-8,23,0,1.8,16,'#c2a47d');cylinder(8,23,0,1.8,16,'#c2a47d');
        }else if(code==='25'){
            box(-34,-17,0,68,34,15,'#c6c8bd','#e7e5da');windows(-34,-17,1,68,34,12,1,10,'#46646b');
            box(-26,-12,15,52,23,75,'#b3a188','#e5e3d8');windows(-26,-12,17,52,23,69,10,7,'#d9e0dc');
            face([[-20,11.12,17],[-10,11.12,17],[-10,11.12,87],[-20,11.12,87]],'#3e7891',12.1);
            for(let z=22;z<88;z+=7)face([[-20,11.2,z],[-10,11.2,z],[-10,11.2,z+.6],[-20,11.2,z+.6]],'#a8c2c8',12.2);
            for(let z=22;z<89;z+=7)box(-26,-12,z,52,23,.7,'#eae8dc');
            box(-35,12,15,70,13,2,'#d7d8cb','#f0edde');
            for(const x of [-28,-10,10,28])cylinder(x,22,0,1.1,15,'#c8cabe');
        }
        if(code==='7'){
            block(-42,-10,84,20,23,'#c8c4a9','#dad6c0',2,12);
            box(-43,10,12,86,5,1.2,'#e7dfc5');box(-44,-11,23,88,24,1.4,'#b4b5a2');
            columns([-35,-21,-7,7,21,35],14,12,'#c5c3ae');
        }else if(code==='14'){
            block(-39,-13,78,26,45,'#b9b8ac','#d8d6c9',6,12,'#a6bba8');
            for(const x of [-39,-14,12,37])box(x,13,0,2,1.8,47,'#888f89');
            box(-27,13,9,54,13,3,'#c4b9a3','#ded4bd');columns([-22,-8,8,22],24,9,'#8f8b7d');
        }else if(['26','27','28'].includes(code)){
            const n=Number(code),height=n===26?48:n===27?40:43;
            arc(n===26?48:43,16,Math.PI*.08,Math.PI*.92,height,n===27?'#b98169':'#d4d1c3','#7397a2',n===26?6:5);
            box(-42,-4,0,12,20,height,'#e0ddd0');windows(-42,-4,1,12,20,height-2,6,2,'#527f98');
            if(n===27)for(const x of [-21,0,21])box(x,33,0,2.2,3,43,'#aa624b');
            if(n===28)box(24,7,0,10,18,43,'#e2dfd2');
        }else if(code==='29'){
            arc(48,15,-Math.PI*.15,Math.PI*1.15,38,'#bb9f77','#a0b4ac',5);
            box(-11,-12,0,22,8,10,'#b6b39d','#e4ded1');columns([-8,8],-5,10,'#bcbaa8');
        }else if(code==='32'){
            block(-32,-14,43,29,49,'#e3e0d3','#eae5d7',5,5);
            cylinder(17,13,0,17,48,'#e0ddcf');
            for(let z=2;z<44;z+=8){cylinder(17,13,z,17.1,6,'#466e70');cylinder(17,13,z+6,17.5,1.5,'#ebe7d8');}
            cylinder(17,13,49,19,2,'#c8c8ba');
        }else if(code==='33'){
            block(-45,-13,90,26,47,'#c8b590','#ded3b8',6,13,'#688083');
            box(-46,-14,47,92,28,2,'#ddc9a4');box(-14,13,0,28,6,49,'#e4d6b8');windows(-14,13,18,28,6,29,3,4,'#789293');
            box(-18,16,14,36,12,3,'#b99b75','#e5d7ba');columns([-14,-5,5,14],26,14,'#caa384');
            const arch=Array.from({length:13},(_,i)=>[-17+34*i/12,19,49+Math.sin(i*Math.PI/12)*8]);
            face([[-17,19,49],...arch,[17,19,49]],'#e9dbc0');
        }else if(code==='37'){
            block(-46,-11,92,22,43,'#d2bc87','#e0c894',5,14,'#738580');
            for(let x=-46;x<46;x+=6.6)box(x,11,0,1,1.8,43,'#e4cfa0');
            box(-48,-13,43,96,26,2,'#b8a36e');box(-40,12,8,20,11,2,'#ab8355');columns([-37,-24],22,8,'#d0ae77');
        }else if(code==='38'){
            block(-41,-13,82,26,49,'#dbcfb6','#e9dfc9',6,11,'#739397');
            box(-18,13,0,36,7,49,'#e1d3b7');windows(-18,13,2,36,7,45,5,4,'#789293');
            columns([-16,-6,6,16],28,22,'#c7a18d');box(-20,17,22,40,15,3,'#d9c3a3');
            for(const x of [-16,-6,6,16])cylinder(x,27,25,1.3,22,'#eee3cc');
            box(-20,17,47,40,15,2,'#dbcbae');pediment(-21,17,49,42,15,8,'#e6d8bb');
        }
        if(['1','3','4'].includes(code)&&!(code==='4'&&variant==='B')){
            const w=code==='3'?100:86,h=code==='1'?34:code==='3'?28:22;
            block(-w/2,-13,w,26,h,'#d3cbb7','#d8d1bd',code==='4'?2:3,code==='3'?15:12,'#657977');
            pitchedRoof(-w/2-2,-15,h,w+4,30,9);
            if(code!=='3'){block(-14,9,28,12,h+4,'#e0d5be','#ddd3bb',3,4);pitchedRoof(-16,7,h+4,32,17,10);box(-7,21,0,14,3,8,'#5b6662');}
        }else if(code==='4'){
            arc(42,15,.08*Math.PI,.92*Math.PI,41,'#c7b999','#81999a',4);
            box(23,9,0,12,25,48,'#cdbf9d');box(-23,21,7,32,11,2,'#e1d5b9');
        }else if(code==='5'){
            block(-35,-14,70,28,44,'#ddd9cb','#e6e1d2',5,10);
            box(15,14,15,20,18,30,'#e3e1d7');columns([18,31],28,15,'#cecab9');
            for(let z=8;z<42;z+=8)box(-35,14,z,47,5,1.2,'#eeeadb');
        }else if(code==='6'){
            block(-38,-12,76,24,45,'#d8c398','#e4d4ac',5,10,'#697f8c');
            block(-12,10,24,8,50,'#e0cda6','#e4d4ac',5,3,'#577894');pediment(-16,9,50,32,12,9,'#cab88a');
            columns([-13,13],25,12,'#d7ceb4');box(-17,18,12,34,12,2,'#e2d8bf');
        }else if(code==='9'){
            block(-6,-16,43,30,44,'#d6d8ce','#e4e2d6',5,7);box(-15,-18,0,13,34,51,'#dcded3');
            box(-42,-12,10,37,26,16,'#cecec1');windows(-42,-12,12,37,26,12,1,7,'#71888a');columns([-37,-23,-8],12,10,'#adb3a7');
        }else if(code==='10'){
            block(-34,-20,68,14,43,'#dcded4','#e7e6da',5,9);block(-34,-6,14,42,43,'#d5d6cb','#e7e6da',5,2);
            block(20,-6,14,42,43,'#e3e2d7','#e7e6da',5,2);arc(23,6,0,Math.PI,42,'#d9dace','#80959a',5);
            for(let y=-3;y<31;y+=6)box(-21,y,46,42,1.5,1,'#c4d7da');
        }else if(code==='11'){
            block(-41,-14,27,28,55,'#e4e1d2','#9eaaa0',7,4);block(14,-14,27,28,61,'#e4e1d2','#9eaaa0',8,4);
            block(-14,-12,28,25,48,'#e7e3d5','#c3c7b8',5,4);
            for(const x of [-11,-5,1,7])box(x,14,13,2.2,5,22,'#507f7c');columns([-12,12],22,13,'#b7b5a8');
        }else if(code==='15'){
            block(-31,-12,62,24,31,'#b95f46','#cd8a68',3,8,'#627f85');
            cylinder(-9,15,0,13,26,'#658d92');box(-35,18,0,5,5,48,'#b7543b');box(30,18,0,5,5,43,'#b7543b');
            box(-35,18,43,70,5,5,'#be6147');box(15,-12,28,4,40,3,'#c7b7a1');
        }else if(code==='17'){
            block(-31,-12,62,24,27,'#dbd0aa','#dfd6ba',3,8,'#678b94');
            box(-7,12,0,14,3,28,'#e8dfc5');windows(-7,12,2,14,3,24,3,3,'#648991');
        }else if(code==='19'&&variant==='A'){
            block(-37,-13,44,27,39,'#d5d6ce','#ddddcf',4,6);cylinder(19,7,0,20,43,'#dfe0d7');
            for(let z=3;z<40;z+=9)cylinder(19,7,z,20.1,4,'#688987');
            box(-25,14,0,3,12,18,'#b89a86');box(-3,14,0,3,12,18,'#b89a86');box(-25,14,18,25,12,3,'#b89a86');
        }else if(code==='19'){
            block(-35,-13,70,26,41,'#d4d4c8','#e4e0d0',4,9);box(-15,13,0,30,8,35,'#679193');
            for(const x of [-17,15])box(x,14,0,2,10,38,'#e5e2d5');box(-17,14,36,34,10,2,'#e5e2d5');
        }else if(code==='23'){
            arc(43,15,.04*Math.PI,.96*Math.PI,37,'#d8d8cd','#8b9a95',4);
            box(-9,24,0,18,7,33,'#627f81');box(-16,29,10,32,9,1.4,'#aaa994');
        }else if(code==='24'){
            block(-43,-13,86,26,32,'#d8dace','#e0e2d5',4,13,'#91a8a5');
            for(let x=-43;x<=43;x+=10)box(x,-13,32,1.5,26,5,'#b4beb8');box(-44,11,37,88,2,1,'#b4beb8');
        }else if(code==='30'){
            block(-44,-14,88,28,51,'#c3b492','#d9cdb1',6,13,'#77898a');
            for(const x of [-44,26]){block(x,10,18,15,56,'#cbbb99','#e0d0ae',6,2);pediment(x-1,10,56,20,16,6,'#d9c8a7');}
            box(-44,-14,0,88,28,6,'#8c887b');
        }else if(code==='31'){
            block(-39,-24,78,15,32,'#d5d8cf','#bec5bc',4,12);block(-39,-9,15,44,29,'#e4e4d8','#bec5bc',3,2);
            block(24,-9,15,44,35,'#d1d5ce','#c1c7bd',4,2);box(-20,-8,22,35,12,8,'#dde0d4');
        }else if(code==='35'){
            block(-39,-20,78,20,48,'#dacaae','#e4d7bc',6,12);block(-39,0,18,30,48,'#cdbda0','#e4d7bc',6,3);block(21,0,18,30,48,'#dacaae','#e4d7bc',6,3);
            arc(30,6,.1*Math.PI,.9*Math.PI,20,'#dbceb1','#819c9c',2);
            for(let i=0;i<7;i++){const a=.1*Math.PI+i*.8*Math.PI/6;cylinder(Math.cos(a)*31,Math.sin(a)*31,0,1.5,20,'#e0d6c0');}
        }else if(code==='39'){
            block(-32,-14,64,28,29,'#d4c694','#dad1b4',3,9);box(-29,14,10,58,12,3,'#cbb984');box(-32,13,23,64,15,2,'#d5c89d');columns([-24,-8,8,24],25,10,'#c2af82');
        }
        if(code==='40'){
            block(-45,-12,90,24,38,'#d6d2bb','#ddd8c3',5,14,'#8b9a92');
            box(-46,-13,0,9,27,41,'#dfdbc8');box(36,-13,0,10,27,41,'#dfdbc8');
        }else if(code==='48'){
            block(-39,-13,78,26,33,'#e1d5ac','#e9ddbd',4,11,'#5c8198');
            for(let x=-39;x<39;x+=13)box(x,13,0,2.4,1.2,34,'#e9dcba');
            box(-9,13,0,18,4,8,'#668b99');box(-11,13,8,22,5,1.2,'#dbcaa8');
        }
        const identityNote={
            '19':'课表沿用原19教楼号，对应现厚乐楼B栋；A栋在课表中为16教。',
            '48':'此模型参考现行48号弘新楼；课表48教的纺织实验教室对应关系待核对。'
        }[code]||'';
        return {name:profile[0],source:'https://www.swu.edu.cn/info/1153/'+profile[1]+'.htm',features:profile[2],identityNote,variants:['4','19'].includes(code)?['A','B']:[],variant,faces};
    }
    // Register the model's ground plane to the footprint's minimum oriented bounds.
    // Only height is illustrative; XY never uses screen-space icon sizing.
    function modelPlacement(model,ring,ratio=1){
        let best=null;
        for(let i=0;i<ring.length-1;i++){
            const dx=ring[i+1][0]-ring[i][0],dy=ring[i+1][1]-ring[i][1],length=Math.hypot(dx,dy);
            if(length<1e-6)continue;
            let u=[dx/length,dy/length],v=[-u[1],u[0]];
            const bounds=axis=>{const p=ring.map(([x,y])=>x*axis[0]+y*axis[1]);return [Math.min(...p),Math.max(...p)];};
            let ub=bounds(u),vb=bounds(v);
            if(ub[1]-ub[0]<vb[1]-vb[0]){[u,v]=[v,u];[ub,vb]=[vb,ub];}
            const area=(ub[1]-ub[0])*(vb[1]-vb[0]);
            if(!best||area<best.area-1e-6)best={u,v,area};
        }
        const u=best?.u||[1,0],v=best?.v||[0,1];
        // Face the two visible walls towards the screen's lower edge.
        if(u[1]<0){u[0]*=-1;u[1]*=-1;}
        if(v[1]<0){v[0]*=-1;v[1]*=-1;}
        const bounds=axis=>{const p=ring.map(([x,y])=>x*axis[0]+y*axis[1]);return [Math.min(...p),Math.max(...p)];};
        const ub=bounds(u),vb=bounds(v),vertices=model.faces.flatMap(f=>f.points);
        const xs=vertices.map(p=>p[0]),ys=vertices.map(p=>p[1]),zs=vertices.map(p=>p[2]);
        const xmin=Math.min(...xs),ymin=Math.min(...ys),xspan=Math.max(...xs)-xmin,yspan=Math.max(...ys)-ymin;
        const sx=(ub[1]-ub[0])/Math.max(1,xspan),sy=(vb[1]-vb[0])/Math.max(1,yspan);
        const sz=Math.min(Math.sqrt(sx*sy)*.72,105*ratio/Math.max(1,...zs));
        return {project:([x,y,z])=>{
            const a=ub[0]+(x-xmin)*sx,b=vb[0]+(y-ymin)*sy;
            return [a*u[0]+b*v[0],a*u[1]+b*v[1]-z*sz];
        },groundCenter:[(ub[0]+ub[1])/2*u[0]+(vb[0]+vb[1])/2*v[0],(ub[0]+ub[1])/2*u[1]+(vb[0]+vb[1])/2*v[1]]};
    }
    // Screen-space extrusion is illustrative, never a measured building height.
    function extrudeFootprint(points,dx,lift){
        const base=points.map(p=>[...p]);
        if(base.length>1&&base[0][0]===base.at(-1)[0]&&base[0][1]===base.at(-1)[1])base.pop();
        const roof=base.map(([x,y])=>[x+dx,y-lift]);
        return {roof,walls:base.map((p,i)=>[p,base[(i+1)%base.length],roof[(i+1)%base.length],roof[i]])};
    }
    function fitView(points,aspect=760/460){
        if(!points.length)return null;
        const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
        const left=Math.min(...xs),right=Math.max(...xs),top=Math.min(...ys),bottom=Math.max(...ys);
        const w=Math.max(420,right-left+180,(bottom-top+180)*aspect),h=w/aspect;
        return {x:(left+right-w)/2,y:(top+bottom-h)/2,w,h};
    }
    function mappedFeature(data,code,variant=defaultVariant(code)){
        return data.buildings.features.find(f=>f.properties.code===code&&(!f.properties.model_variants||f.properties.model_variants.includes(variant)));
    }
    function selectionView(data,code,variant=defaultVariant(code)){
        const mapped=!!mappedFeature(data,code,variant);
        const model=buildingModel(code,variant);
        return {mapped,model,mode:mapped?'map':model?'model-preview':'unmapped'};
    }
    function createSelection(use){let selected=null;return {
        select(code){selected=code;},reset(){selected=null;},confirm(){if(selected!==null)use(selected);}
    };}
    function mount(){
        const dialog=document.querySelector('[data-building-map]');if(!dialog)return;
        const svg=dialog.querySelector('svg'),list=dialog.querySelector('[data-map-list]'),search=dialog.querySelector('[data-map-search]');
        const note=dialog.querySelector('[data-map-status]'),picked=dialog.querySelector('[data-map-picked]'),use=dialog.querySelector('[data-map-use]');
        const open=document.querySelector('[data-site-map]'),ns='http://www.w3.org/2000/svg';
        // Progressive enhancement also supports pages served from an older template cache.
        let locate=dialog.querySelector('[data-map-selected]');
        if(!locate){
            locate=document.createElement('button');locate.type='button';locate.dataset.mapSelected='';
            locate.textContent='定位所选';locate.disabled=true;
            const overview=dialog.querySelector('[data-map-overview]');overview.before(locate);
        }
        const variantSelect=document.createElement('select');variantSelect.setAttribute('aria-label','选择楼栋分部');variantSelect.hidden=true;
        variantSelect.className='site-map__variant';
        for(const value of ['A','B']){const option=document.createElement('option');option.value=value;option.textContent=value+'栋外观';variantSelect.append(option);}
        dialog.querySelector('[data-map-overview]').before(variantSelect);
        dialog.querySelector('.site-map__legend').textContent='蓝点：采集位置　浅绿：附近楼栋　金色立体楼栋：当前选择（高度示意）';
        let animateSelection=false,selectedVariant='A',overview=false,previewZoom=1;
        let record=null,data=null,context=null,revision=0,selected=null,view={x:0,y:0,w:1000,h:650},drag=null;
        const project=([lon,lat])=>[(lon-106.423)*96450,-(lat-29.827)*111195];
        const select=createSelection(code=>{
            if(!record||record.course_confirmed||record.submitted)return;
            window.dispatchEvent(new CustomEvent('lecture-site-building-picked',{detail:{captureId:record.id,building:code}}));dialog.close();
        });
        function element(tag,attrs,parent=svg){const e=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,v);parent.append(e);return e;}
        function coordinates(f){return (f.geometry.type==='Polygon'?f.geometry.coordinates[0]:f.geometry.coordinates).map(project);}
        function footprintPath(f,dx=0,dy=0){return f.geometry.coordinates.map(ring=>'M'+ring.map(project).map(([x,y])=>`${x+dx},${y+dy}`).join('L')+'Z').join(' ');}
        function centre(f){const points=coordinates(f).slice(0,-1);return points.reduce((a,p)=>[a[0]+p[0]/points.length,a[1]+p[1]/points.length],[0,0]);}
        const aspect=()=>Math.max(.5,svg.clientWidth/Math.max(1,svg.clientHeight));
        function fit(features){
            const next=fitView(features.flatMap(coordinates),aspect());if(next){view=next;draw();}
        }
        function zoom(factor){if(svg.dataset.viewMode==='model-preview'){previewZoom=Math.min(1.6,Math.max(.65,previewZoom/factor));draw();return;}const w=Math.max(250,Math.min(5500,view.w*factor)),h=w/aspect();view={x:view.x+(view.w-w)/2,y:view.y+(view.h-h)/2,w,h};draw();}
        function pick(code,variant=defaultVariant(code)){
            selectedVariant=variant;overview=false;previewZoom=1;
            animateSelection=selected!==code;selected=code;select.select(code);const row=choices(data).find(b=>b.code===code),feature=mappedFeature(data,code,selectedVariant);
            picked.textContent=row?buildingLabel(row.code):'';
            const model=buildingModel(code,selectedVariant);
            variantSelect.hidden=!model?.variants.length;variantSelect.value=selectedVariant;
            use.disabled=!row;use.textContent=row?`使用${buildingLabel(row.code)}`:'使用此教学楼';
            locate.disabled=!feature;renderList();
            if(feature){
                fit([feature]);
                if(window.matchMedia('(max-width: 680px)').matches)dialog.querySelector('.site-map__surface').scrollIntoView({block:'start',behavior:'instant'});
            }else{draw();if(model&&window.matchMedia('(max-width: 680px)').matches)dialog.querySelector('.site-map__surface').scrollIntoView({block:'start',behavior:'instant'});}
        }
        function renderList(){
            list.replaceChildren();const rows=choices(data,search.value);
            if(!rows.length){const p=document.createElement('p');p.textContent='没有匹配的楼号，可关闭地图后手动输入。';list.append(p);}
            for(const b of rows){
                const button=document.createElement('button');button.type='button';button.className='site-map__item';
                button.setAttribute('aria-pressed',String(b.code===selected));
                const title=document.createElement('strong');title.textContent=buildingLabel(b.code);
                const detail=document.createElement('span');detail.textContent=!b.mapped?(buildingModel(b.code)?'有外观模型 · 地图位置待核对':'课表地点 · 暂无地图位置'):b.distance!==undefined?`距定位点约${Math.round(b.distance)}米 · 待核对`:'已收录地图 · 待核对';
                button.append(title,detail);button.addEventListener('click',()=>{
                    pick(b.code);
                });list.append(button);
            }
        }
        function drawPreview(model){
            svg.replaceChildren();svg.dataset.viewMode='model-preview';
            dialog.querySelector('.site-map__legend').textContent='外观预览（非地图位置） · 楼高、比例和遮挡部分为简化示意';
            const width=Math.max(280,svg.clientWidth),height=Math.max(260,svg.clientHeight);
            svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
            const project=([x,y,z])=>[(x-y)*.85,(x+y)*.38-z];
            const all=model.faces.flatMap(f=>f.points).map(project);
            const xs=all.map(p=>p[0]),ys=all.map(p=>p[1]),left=Math.min(...xs),right=Math.max(...xs),top=Math.min(...ys),bottom=Math.max(...ys);
            const size=Math.min((width-64)/(right-left),(height-94)/(bottom-top))*.85*previewZoom;
            const position=p=>{const [x,y]=project(p);return [width/2+(x-(left+right)/2)*size,height/2+8+(y-(top+bottom)/2)*size];};
            const group=element('g',{'data-map-model':selected,'data-model-preview':selected});
            const depth=f=>f.depth??f.points.reduce((a,p)=>a+p[0]+p[1]+p[2]*.015,0)/f.points.length;
            for(const face of [...model.faces].sort((a,b)=>depth(a)-depth(b)))element('polygon',{points:face.points.map(p=>position(p).join(',')).join(' '),fill:face.color,stroke:face.color,'stroke-width':.3},group);
            const title=element('text',{x:18,y:25,'font-size':15,'font-weight':650,fill:'var(--color-text)'});title.textContent=buildingLabel(selected)+' · '+model.name;
            const hint=element('text',{x:18,y:height-15,'font-size':12,fill:'var(--color-text-muted)'});hint.textContent='外观示意 · 地图位置待核对';
        }
        function draw(){
            if(!data)return;
            const presentation=selectionView(data,selected,selectedVariant);
            if(!overview&&presentation.mode==='model-preview'){drawPreview(presentation.model);return;}
            svg.dataset.viewMode='map';
            dialog.querySelector('.site-map__legend').textContent='蓝点：采集位置　浅绿：附近楼栋　金色楼栋：当前选择（外观示意）';svg.replaceChildren();svg.setAttribute('viewBox',`${view.x} ${view.y} ${view.w} ${view.h}`);
            const ratio=Math.max(view.w/Math.max(1,svg.clientWidth),view.h/Math.max(1,svg.clientHeight)),near=new Set(data.nearby.buildings.map(b=>b.code));
            const bg=element('g',{'aria-hidden':'true'});
            for(const f of context?.features||[]){
                const points=coordinates(f),kind=f.properties.kind;
                element(f.geometry.type==='Polygon'?'polygon':'polyline',{points:points.map(p=>p.join(',')).join(' '),
                    fill:({building:'#deded4',water:'#c6d8df',green:'#dce3ce',road:'none'})[kind],
                    stroke:kind==='road'?'#fffefa':kind==='building'?'#c8cabe':'none',
                    'stroke-width':kind==='road'?(f.properties.highway==='footway'?3:7):.8},bg);
            }
            const location=data.location;
            if(location&&Number.isFinite(location.latitude)){
                const [x,y]=project([location.longitude,location.latitude]);
                element('circle',{cx:x,cy:y,r:location.accuracy,fill:'#74b7e5','fill-opacity':'.13',stroke:'#559ccf','stroke-width':ratio,'stroke-dasharray':`${5*ratio} ${4*ratio}`});
            }
            const occupied=[],labels=[];
            const selectable=new Set(choices(data).map(b=>b.code));
            for(const f of data.buildings.features){
                if(!selectable.has(f.properties.code))continue;
                const code=f.properties.code,active=code===selected;
                const polygon=element('path',{d:footprintPath(f),'fill-rule':'evenodd',
                    fill:active?'var(--color-accent-strong)':near.has(code)?'#a9bb9f':'#c8cbb8',stroke:active?'var(--color-accent-strong)':'#7b8878',
                    'stroke-width':(active?3:1)*ratio,'aria-pressed':String(active),tabindex:0,role:'button','aria-label':`选择${code}教`,'data-building-code':code});
                polygon.addEventListener('click',()=>{if(!drag?.moved)pick(code);});
                polygon.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();pick(code);}});
                const [x,y]=buildingAnchor(f.geometry.coordinates.map(ring=>ring.map(project)));if(active)continue;if(x<view.x||x>view.x+view.w||y<view.y||y>view.y+view.h)continue;
                labels.push({x,y,code});
            }
            if(location&&Number.isFinite(location.latitude)){
                const [x,y]=project([location.longitude,location.latitude]);
                element('circle',{cx:x,cy:y,r:5*ratio,fill:'#267eb8',stroke:'white','stroke-width':2*ratio,'pointer-events':'none'});
            }
            // Keep the ground footprint visible; lift only the selected building.
            const chosen=mappedFeature(data,selected,selectedVariant);
            if(chosen){
                const model=buildingModel(selected,selectedVariant);
                const base=coordinates(chosen),[cx,cy]=centre(chosen);
                const lift=(svg.clientWidth<400?18:30)*ratio,dx=lift*.3;
                const shape=extrudeFootprint(base,dx,lift),points=p=>p.map(v=>v.join(',')).join(' ');
                const moving=animateSelection&&!window.matchMedia('(prefers-reduced-motion: reduce)').matches;
                const marker=element('g',{'data-map-selection':selected,'pointer-events':'none','aria-hidden':'true'});
                element('path',{d:footprintPath(chosen),'fill-rule':'evenodd',fill:'var(--color-highlight)',stroke:'var(--color-highlight)','stroke-width':10*ratio,'stroke-linejoin':'round'},marker);
                element('path',{d:footprintPath(chosen,12*ratio,10*ratio),'fill-rule':'evenodd',fill:'#29333e','fill-opacity':'.16'},marker);
                const followsFootprint=['26','27','28','29'].includes(selected);
                if(!model||followsFootprint){
                const walls=[...shape.walls].sort((a,b)=>(a[0][1]+a[1][1])-(b[0][1]+b[1][1]));
                for(const wall of walls){
                    const face=element('polygon',{points:points(wall),fill:followsFootprint?(selected==='27'?'#b98169':selected==='29'?'#bb9f77':'#d4d1c3'):(wall[1][0]>=wall[0][0]?'#9d8867':'#c7b18b'),stroke:'#88795f','stroke-width':.7*ratio,'data-map-wall':''},marker);
                    if(moving)element('animate',{attributeName:'points',from:points([wall[0],wall[1],wall[1],wall[0]]),to:points(wall),dur:'0.28s',fill:'freeze'},face);
                    if(followsFootprint){
                        marker.setAttribute('data-map-model',selected);
                        const mix=(a,b,t)=>a.map((v,i)=>v+(b[i]-v)*t);
                        for(let floor=0;floor<4;floor++){
                            const lo=.1+floor*.22,hi=lo+.13;
                            const band=[mix(wall[0],wall[3],lo),mix(wall[1],wall[2],lo),mix(wall[1],wall[2],hi),mix(wall[0],wall[3],hi)];
                            element('polygon',{points:points(band),fill:selected==='29'?'#a0b4ac':'#7397a2','data-map-window-band':''},marker);
                        }
                    }
                }
                const roof=element('polygon',{points:points(shape.roof),fill:followsFootprint?'#ddd9cb':'var(--color-accent)',stroke:'var(--color-surface)','stroke-width':2*ratio,'stroke-linejoin':'round','data-map-roof':''},marker);
                if(moving)element('animate',{attributeName:'points',from:points(base.slice(0,shape.roof.length)),to:points(shape.roof),dur:'0.28s',fill:'freeze'},roof);
                }
                let modelTop=cy-lift;
                if(model&&!followsFootprint){
                    const placement=modelPlacement(model,base,ratio),projectModel=placement.project;
                    const mesh=element('g',{'data-map-model':selected},marker);
                    const depth=f=>f.depth??f.points.reduce((v,p)=>v+p[0]+p[1]+p[2]*.015,0)/f.points.length;
                    for(const f of [...model.faces].sort((a,b)=>depth(a)-depth(b))){
                        const points=f.points.map(projectModel);modelTop=Math.min(modelTop,...points.map(p=>p[1]));
                        const polygon=element('polygon',{points:points.map(p=>p.join(',')).join(' '),fill:f.color,stroke:f.color,'stroke-width':.25*ratio,'stroke-linejoin':'round'},mesh);
                        if(moving)element('animate',{attributeName:'points',from:f.points.map(([x,y])=>projectModel([x,y,0]).join(',')).join(' '),to:points.map(p=>p.join(',')).join(' '),dur:'0.28s',fill:'freeze'},polygon);
                    }
                }
                const x=cx+dx,y=model&&!followsFootprint?modelTop+10*ratio:cy-lift,labelWidth=Math.max(110,(String(selected).length+7)*13)*ratio;
                element('circle',{cx:x,cy:y,r:4*ratio,fill:'var(--color-highlight)'},marker);
                element('line',{x1:x,y1:y-6*ratio,x2:x,y2:y-26*ratio,stroke:'var(--color-accent-strong)','stroke-width':2*ratio},marker);
                element('rect',{x:x-labelWidth/2,y:y-58*ratio,width:labelWidth,height:32*ratio,rx:8*ratio,fill:'var(--color-accent-strong)',stroke:'var(--color-surface)','stroke-width':2*ratio},marker);
                const label=element('text',{x,y:y-37*ratio,'text-anchor':'middle','font-size':14*ratio,'font-weight':700,fill:'var(--color-surface)'},marker);
                label.textContent=buildingLabel(selected)+' · 已选中';
                const markerBox=marker.getBBox();occupied.push({x:markerBox.x-4*ratio,y:markerBox.y-4*ratio,width:markerBox.width+8*ratio,height:markerBox.height+8*ratio});
            }
            animateSelection=false;
            // Labels are drawn after every footprint/model so later buildings cannot cover them.
            const leaderLayer=element('g',{'data-map-leaders':'','pointer-events':'none'});
            const labelLayer=element('g',{'data-map-labels':'','pointer-events':'none'});
            occupied.push({x:view.x,y:view.y,width:50*ratio,height:34*ratio},
                {x:view.x,y:view.y+view.h-48*ratio,width:90*ratio,height:48*ratio});
            for(const item of labels){
                const text=element('text',{'font-size':14*ratio,'font-weight':650,fill:'#264b3b',
                    stroke:'#f4f7f0','stroke-width':3*ratio,'paint-order':'stroke','data-map-label':item.code,
                    'pointer-events':'auto',role:'button',tabindex:0,'aria-label':`选择${item.code}教`,'data-building-code':item.code},labelLayer);
                text.addEventListener('click',()=>{if(!drag?.moved)pick(item.code);});
                text.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();pick(item.code);}});
                text.textContent=buildingLabel(item.code);
                const measured=text.getBBox(),padding=3*ratio;
                const box=placeMapLabel(item.x,item.y,measured.width+padding*2,measured.height+padding*2,
                    {x:view.x+padding,y:view.y+padding,width:view.w-padding*2,height:view.h-padding*2},
                    [...occupied,...labels.filter(other=>other!==item).map(other=>({x:other.x-5*ratio,y:other.y-5*ratio,width:10*ratio,height:10*ratio}))],ratio);
                if(!box){text.remove();continue;}
                occupied.push(box);
                text.setAttribute('data-anchor-x',item.x);text.setAttribute('data-anchor-y',item.y);
                if(Math.hypot(box.x+box.width/2-item.x,box.y+box.height/2-item.y)>2*ratio){
                    const endX=Math.max(box.x,Math.min(box.x+box.width,item.x)),endY=Math.max(box.y,Math.min(box.y+box.height,item.y));
                    const attrs={x1:item.x,y1:item.y,x2:endX,y2:endY,'data-map-leader':item.code};
                    element('line',{...attrs,stroke:'#f4f7f0','stroke-width':4*ratio},leaderLayer);
                    element('line',{...attrs,stroke:'#456151','stroke-width':1.2*ratio},leaderLayer);
                    element('circle',{cx:item.x,cy:item.y,r:2.5*ratio,fill:'#264b3b',stroke:'#f4f7f0','stroke-width':ratio},leaderLayer);
                }
                text.setAttribute('x',box.x+padding-measured.x);text.setAttribute('y',box.y+padding-measured.y);
            }
            const north=element('text',{x:view.x+16*ratio,y:view.y+25*ratio,'font-size':12*ratio,fill:'#456151'});north.textContent='北 ↑';
            const meters=view.w>1600?500:view.w>700?200:100;
            element('line',{x1:view.x+18*ratio,y1:view.y+view.h-22*ratio,x2:view.x+18*ratio+meters,y2:view.y+view.h-22*ratio,stroke:'#456151','stroke-width':2*ratio});
            const scale=element('text',{x:view.x+18*ratio,y:view.y+view.h-30*ratio,'font-size':11*ratio,fill:'#456151'});scale.textContent=meters+'米';
        }
        async function load(){
            if(!record)return;const version=++revision,id=record.id;data=null;selected=null;selectedVariant='A';overview=false;previewZoom=1;variantSelect.hidden=true;select.reset();use.disabled=true;locate.disabled=true;
            search.value='';list.replaceChildren();svg.replaceChildren();picked.textContent='点击建筑或列表，再确认教学楼。';note.textContent='正在加载校园地图…';dialog.showModal();
            const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),12000);
            try{
                const [response,background]=await Promise.all([fetch(`/user/api/site-capture/${id}/map`,{signal:abort.signal}).then(r=>r.json()),
                    context?Promise.resolve(context):fetch(dialog.dataset.contextUrl,{signal:abort.signal}).then(r=>r.json()).catch(()=>null)]);
                if(version!==revision||id!==record?.id||!dialog.open)return;
                if(!response.success)throw new Error(response.message||'地图暂不可用');data=response.data;context=background;

                note.textContent='选择所在教学楼，再点击“使用此教学楼”。也可搜索楼号。';
                if(!context)note.textContent+=' 背景暂未加载，楼栋仍可选择。';
                renderList();const codes=new Set(data.nearby.buildings.map(b=>b.code));
                const features=data.buildings.features.filter(f=>codes.has(f.properties.code));fit(features.length?features:data.buildings.features);
            }catch(e){if(version===revision)note.textContent='地图暂不可用，可关闭后手动输入教学楼。';}
            finally{clearTimeout(timer);}
        }
        window.addEventListener('lecture-site-capture-loaded',e=>{
            if(record?.id!==e.detail.id||e.detail.course_confirmed||e.detail.submitted){revision++;dialog.close();select.reset();}
            record=e.detail;open.disabled=!!(record.course_confirmed||record.submitted);
        });
        document.getElementById('lectureForm').addEventListener('reset',()=>{record=null;revision++;dialog.close();select.reset();});
        open.addEventListener('click',load);use.addEventListener('click',()=>select.confirm());
        dialog.querySelector('[data-map-close]').addEventListener('click',()=>{revision++;dialog.close();});
        search.addEventListener('input',()=>{if(data)renderList();});
        dialog.querySelector('[data-map-zoom-in]').addEventListener('click',()=>zoom(.65));
        dialog.querySelector('[data-map-zoom-out]').addEventListener('click',()=>zoom(1.5));
        dialog.querySelector('[data-map-overview]').addEventListener('click',()=>{if(data){overview=true;fit(data.buildings.features);}});
        locate.addEventListener('click',()=>{const f=data&&mappedFeature(data,selected,selectedVariant);if(f)fit([f]);});
        variantSelect.addEventListener('change',()=>{if(selected)pick(selected,variantSelect.value);});
        svg.addEventListener('wheel',e=>{e.preventDefault();zoom(e.deltaY>0?1.15:.87);},{passive:false});
        svg.addEventListener('pointerdown',e=>{if(svg.dataset.viewMode==='model-preview')return;drag={x:e.clientX,y:e.clientY,view:{...view},moved:false,code:e.target.dataset?.buildingCode};svg.setPointerCapture(e.pointerId);});
        svg.addEventListener('pointermove',e=>{
            if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;
            if(Math.abs(dx)+Math.abs(dy)<5&&!drag.moved)return;drag.moved=true;
            const r=svg.getBoundingClientRect(),scale=Math.min(r.width/drag.view.w,r.height/drag.view.h);
            view={...view,x:drag.view.x-dx/scale,y:drag.view.y-dy/scale};draw();
        });
        svg.addEventListener('pointerup',()=>{if(drag&&!drag.moved&&drag.code)pick(drag.code);setTimeout(()=>{drag=null;},0);});
        svg.addEventListener('pointercancel',()=>{drag=null;});
        window.addEventListener('resize',()=>{if(dialog.open&&data)draw();});
    }
    return {buildingAnchor,placeMapLabel,choices,createSelection,fitView,extrudeFootprint,buildingModel,modelPlacement,selectionView,mount};
});
