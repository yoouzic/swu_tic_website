/* Select real, fresh browser observations. Never synthesize GPS accuracy. */
(function(root,factory){
    const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;
    else root.SiteLocation=api;
})(typeof window==='object'?window:globalThis,function(){
    'use strict';
    function distance(a,b){
        const rad=Math.PI/180;
        const dlat=(b.latitude-a.latitude)*rad,dlon=(b.longitude-a.longitude)*rad;
        const h=Math.sin(dlat/2)**2+Math.cos(a.latitude*rad)*Math.cos(b.latitude*rad)*Math.sin(dlon/2)**2;
        return 6371000*2*Math.asin(Math.sqrt(Math.min(1,h)));
    }
    function start({geo=typeof navigator==='object'?navigator.geolocation:null,now=Date.now,
                    onUpdate=()=>{},onSample=()=>{},setTimer=setTimeout,clearTimer=clearTimeout,budget=12000}={}){
        let best=null,pending=null,watch=null,timer=null,done=false,stable=0,lastTimestamp=-Infinity;
        const observations=[];
        const samples=()=>observations.filter(p=>now()-p.timestamp<=30000).map(p=>({...p}));
        const stop=()=>{if(done)return;done=true;if(watch!==null)geo?.clearWatch?.(watch);if(timer!==null)clearTimer(timer);};
        const success=position=>{
            if(done)return;
            const c=position.coords,timestamp=position.timestamp;
            if(!c||![c.latitude,c.longitude,c.accuracy,timestamp].every(Number.isFinite)||
               Math.abs(c.latitude)>90||Math.abs(c.longitude)>180||c.accuracy<=0||c.accuracy>100000||timestamp<=lastTimestamp||
               now()-timestamp>5000||timestamp-now()>2000)return;
            lastTimestamp=timestamp;
            const sample={latitude:c.latitude,longitude:c.longitude,accuracy:c.accuracy,timestamp};
            observations.push(sample);if(observations.length>12)observations.shift();onSample({...sample});
            if(best&&distance(best,sample)>best.accuracy+sample.accuracy+20){
                if(!pending||now()-pending.timestamp>5000||distance(pending,sample)>pending.accuracy+sample.accuracy+10){pending=sample;return;}
            }
            pending=null;
            if(best&&distance(best,sample)<=best.accuracy+sample.accuracy+10)stable++;
            else stable=0;
            if(!best||sample.accuracy<best.accuracy){best=sample;onUpdate({...best});}
            if(best.accuracy<=15&&stable>=1)stop();
        };
        if(!geo)return {best:()=>null,samples,stop};
        timer=setTimer(stop,budget);
        try{
            const options={enableHighAccuracy:true,maximumAge:0,timeout:10000};
            if(typeof geo.watchPosition==='function'){
                watch=geo.watchPosition(success,error=>{if(error.code===1)stop();},options);
                if(done&&watch!==null)geo.clearWatch(watch);
            }else geo.getCurrentPosition(success,stop,options);
        }catch(error){stop();}
        return {best:()=>best&&now()-best.timestamp<=5000?{...best}:null,samples,stop};
    }
    return {start};
});
