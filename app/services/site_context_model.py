"""Uncalibrated robust ranking and active questions for photo-first listening."""
from dataclasses import dataclass
import math
import re
from statistics import median

from app.services.campus_buildings import _distance, _project, load_buildings
from app.services.lecture_site_capture import PERIODS, normalize_location
from app.services.listening_assistant_contracts import parse_period


@dataclass(frozen=True)
class ModelParameters:
    systematic_scale_m: float = 70
    map_scale_m: float = 25
    wide_scale_m: float = 350
    outlier_fraction: float = .2
    position_weight: float = .9
    door_weight: float = 2.7
    time_weight: float = 1.5
    time_scale_minutes: float = 8
    question_temperature: float = 2


PARAMETERS = ModelParameters()
FACT_KINDS = ('building', 'room_number', 'period', 'teacher_name')
PROMPTS = {'building':'你在哪栋教学楼？','room_number':'门牌上的房间号是哪一个？',
           'period':'你听的是哪几节课？','teacher_name':'授课教师是哪位？'}
COSTS = {'building':1, 'room_number':.8, 'period':1.1, 'teacher_name':1.4}


def _number(value):
    text = str(value).strip()
    return str(int(text)) if text.isdigit() else text


def split_room(room):
    match = re.fullmatch(r'([\w\u4e00-\u9fff]+)-([A-Za-z]?\d{3,4})', str(room))
    return (_number(match[1]), _number(match[2])) if match else ('', str(room))


def validate_facts(facts):
    if not isinstance(facts, dict) or any(k not in FACT_KINDS for k in facts):
        raise ValueError('确认信息格式无效。')
    result = {}
    for key, value in facts.items():
        if not isinstance(value, str) or not value.strip() or len(value) > 120 or any(ord(c)<32 for c in value):
            raise ValueError('请填写有效的确认信息。')
        value = value.strip()
        if key == 'building':
            if not re.fullmatch(r'[\w\u4e00-\u9fff]{1,40}',value):raise ValueError('教学楼格式无效。')
            value = _number(value)
        elif key == 'room_number':
            if not re.fullmatch(r'[A-Za-z]?\d{3,4}',value):raise ValueError('门牌号格式无效。')
            value = _number(value)
        elif key == 'period':
            period = parse_period(value)
            if period is None:raise ValueError('节次格式无效。')
            value = f'{period[0]}-{period[1]}'
        result[key] = value
    return result


def location_summary(location, parameters=PARAMETERS):
    """Inferred centre/scale, not a replacement for any recorded measurement."""
    best = normalize_location(location)
    if not best or best['accuracy'] > 1000:
        return {'available':False,'reliability':0,'unstable':False}
    raw = location.get('samples', []) if isinstance(location.get('samples', []),list) else []
    points = [p for sample in raw[-12:] if (p := normalize_location(sample)) and p['accuracy']<=1000]
    if not points:points=[best]
    lat, lon = median(p['latitude'] for p in points), median(p['longitude'] for p in points)
    distances = [math.hypot(*_project([p['longitude'],p['latitude']],lat,lon)) for p in points]
    accuracy = median(p['accuracy'] for p in points)
    spread = 1.4826 * median(distances)
    unstable = max(distances) > accuracy + parameters.systematic_scale_m + 20
    scale = math.sqrt((accuracy/2.448)**2 + parameters.systematic_scale_m**2
                      + parameters.map_scale_m**2 + spread**2)
    reliability = min(.9,200/(accuracy+200)) * (.55 if unstable else 1)
    return {'available':True,'latitude':lat,'longitude':lon,'scale_m':scale,'accuracy_m':accuracy,
            'reliability':reliability,'unstable':unstable,'spread_m':spread}


def _area_points(feature):
    """Uniform quadrature over footprint + 15m doorway band; normalised per building."""
    ring = feature['geometry']['coordinates'][0]
    lons,lats = zip(*ring)
    lat0 = sum(lats)/len(lats)
    dy=15/111195;dx=dy/max(.1,math.cos(math.radians(lat0)))
    left,right=min(lons)-dx,max(lons)+dx;bottom,top=min(lats)-dy,max(lats)+dy
    points=[]
    for ix in range(5):
        for iy in range(5):
            lon=left+(ix+.5)*(right-left)/5;lat=bottom+(iy+.5)*(top-bottom)/5
            if _distance(feature,lat,lon)<=15:points.append((lon,lat))
    return points or [tuple(ring[0])]


def _position_likelihood(feature, summary, parameters):
    narrow=summary['scale_m'];wide=math.hypot(narrow,parameters.wide_scale_m)
    mix=parameters.outlier_fraction
    peak=(1-mix)/narrow**2+mix/wide**2
    values=[]
    for lon,lat in _area_points(feature):
        x,y=_project([lon,lat],summary['latitude'],summary['longitude']);d2=x*x+y*y
        values.append(((1-mix)*math.exp(-d2/(2*narrow*narrow))/narrow**2
                       + mix*math.exp(-d2/(2*wide*wide))/wide**2)/peak)
    return max(1e-5,sum(values)/len(values))


def normalize_hypotheses(hypotheses):
    found={}
    for h in hypotheses or []:
        if isinstance(h,str):h={'value':h,'score':.5,'method':'legacy_unscored'}
        if not isinstance(h,dict):continue
        value,score=h.get('value'),h.get('score')
        if (not isinstance(value,str) or not re.fullmatch(r'\d{3,4}',value)
                or type(score) not in (float,int) or not math.isfinite(score) or not .35<=score<=1):continue
        canonical=_number(value)
        if canonical not in found or score>found[canonical]['score']:
            found[canonical]={'value':value,'score':score,'method':str(h.get('method','ocr'))[:40]}
    # A few thousandths of an uncalibrated OCR score do not establish the
    # direction of a numeric-only sign. Keep the image's direct reading first
    # in near ties and preserve raw scores; competing orientations still ask
    # for human confirmation. This ordering must survive saved evidence reload.
    return sorted(found.values(),key=lambda h:(-round(h['score'],2),
        h['method'].endswith('_rot180'),-h['score'],h['value']))[:8]


def _edit_distance(a,b):
    previous=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        current=[i]
        for j,y in enumerate(b,1):
            substitution=0 if x==y else (.6 if {x,y} in ({'6','9'},{'0','8'},{'1','7'}) else 1)
            current.append(min(current[-1]+1,previous[j]+1,previous[j-1]+substitution))
        previous=current
    return previous[-1]


def _door_likelihood(room,hypotheses):
    strengths=[min(.85,h['score']) for h in hypotheses]
    value=sum(s*math.exp(-1.8*_edit_distance(room,_number(h['value']))) for s,h in zip(strengths,hypotheses))/sum(strengths)
    return .15+.85*value


def _minutes(clock):
    if not isinstance(clock,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',clock):return None
    h,m=map(int,clock.split(':'));return h*60+m


def _time_likelihood(period,clock,parameters):
    moment=_minutes(clock)
    if moment is None or not period or period[1]>len(PERIODS):return 1,None
    start,end=period
    intervals=[(_minutes(PERIODS[i-1][0]),_minutes(PERIODS[i-1][1])) for i in range(start,end+1)]
    delta=min(0 if a<=moment<b else max(.01,min(abs(moment-a),abs(moment-b))) for a,b in intervals)
    return .12+.88*math.exp(-.5*(delta/parameters.time_scale_minutes)**2),delta


def _value(candidate,kind):
    building,room=split_room(candidate['room'])
    if kind=='building':return building
    if kind=='room_number':return room
    if kind=='period':return f"{candidate['period'][0]}-{candidate['period'][1]}"
    return candidate.get('teacher_name','')


def _next_question(candidates,facts,asked,parameters,hypotheses=(), *, minimum=4,max_options=None):
    if len(set(asked))>=4:return None
    if hypotheses and 'room_number' not in facts and 'room_number' not in asked:
        if len(hypotheses)>1:
            options=[{'value':_number(h['value']),'label':h['value'],
                      'candidate_count':sum(split_room(c['room'])[1]==_number(h['value']) for c in candidates)} for h in hypotheses]
            return {'kind':'room_number','prompt':'照片上有不同读数，请核对门牌号：',
                    'options':options,'expected_gain_bits':None}
        # Questions concern the plausible physical-room scene. Unmatched/online
        # rows remain available as courses, but do not overwhelm photo questions.
        supported=[c for c in candidates if _door_likelihood(split_room(c['room'])[1],hypotheses)>=.45
                   and re.fullmatch(r'[A-Za-z]?\d{3,4}',split_room(c['room'])[1])]
        if supported:candidates=supported
    if len(candidates)<minimum:return None
    peak=max(c['match_score'] for c in candidates)
    masses=[math.exp((c['match_score']-peak)/parameters.question_temperature) for c in candidates]
    total=sum(masses);weights=[m/total for m in masses]
    choices=[]
    for index,kind in enumerate(FACT_KINDS):
        if kind in facts or kind in asked:continue
        groups={}
        for c,w in zip(candidates,weights):
            value=_value(c,kind)
            if not value or len(value)>120:continue
            if kind=='room_number' and not re.fullmatch(r'[A-Za-z]?\d{3,4}',value):continue
            mass,count=groups.get(value,(0,0));groups[value]=(mass+w,count+1)
        if len(groups)<2 or (max_options is not None and len(groups)>max_options):continue
        # Question answers partition candidate mass: their entropy is expected reduction.
        covered=sum(m for m,_ in groups.values())
        gain=-sum((m/covered)*math.log2(m/covered) for m,_ in groups.values())*covered
        options=[]
        for value,(mass,count) in sorted(groups.items(),key=lambda kv:(
                -_door_likelihood(kv[0],hypotheses) if kind=='room_number' and hypotheses else 0,
                -kv[1][0],kv[0])):
            label=(f'第{value}节' if kind=='period' else
                   f'{value}教' if kind=='building' and value.isdigit() else
                   value.zfill(4) if kind=='room_number' and value.isdigit() else value)
            options.append({'value':value,'label':label,'candidate_count':count})
        reading_cost = 1 + (len(groups)-1)/3
        choices.append((gain/(COSTS[kind]*reading_cost),-index,{'kind':kind,'prompt':PROMPTS[kind],
                        'options':options,'expected_gain_bits':round(gain,4)}))
    return max(choices,key=lambda x:(x[0],x[1]))[2] if choices else None


def rank_site_candidates(candidates, *, location=None, door_hypotheses=(), clock=None,
                         time_reliability=1, confirmed_facts=None, asked=(), buildings=None,
                         parameters=PARAMETERS, question_count=0):
    from app.services.site_context_guidance import choose_guidance
    if type(question_count) is not int or not 0<=question_count<=2:
        raise ValueError('补问次数格式无效。')
    facts=validate_facts(confirmed_facts or {})
    if not isinstance(asked,(tuple,list)) or len(asked)>4 or any(k not in FACT_KINDS for k in asked):
        raise ValueError('提问记录格式无效。')
    data=load_buildings() if buildings is None else buildings
    summary=location_summary(location,parameters)
    hypotheses=normalize_hypotheses(door_hypotheses)
    uncertainties=[]
    if len(hypotheses)>1 and 'room_number' not in facts:uncertainties.append('门牌读数需核对')
    if not summary['available']:uncertainties.append('定位不可用，可手动选楼')
    elif summary['unstable']:uncertainties.append('定位有跳动')
    geo={}
    if summary['available']:
        for feature in data['features']:
            if feature['properties'].get('campus')!='beibei':continue
            value=_position_likelihood(feature,summary,parameters)
            code=feature['properties']['code'];geo[code]=max(value,geo.get(code,0))
        if not geo or max(geo.values())<.001:
            geo={};summary['reliability']=0
            uncertainties.append('定位与已知校区不吻合，可手动选楼')
    scoped_location=bool(geo and summary['reliability']>=.5 and not summary['unstable'])
    radius=max(250,summary.get('accuracy_m',0)+50)
    geometry={f['properties']['code']:f for f in data['features'] if f['properties'].get('campus')=='beibei'}
    ranked=[];seen=set()
    for candidate in candidates:
        c=candidate.to_public_dict() if hasattr(candidate,'to_public_dict') else dict(candidate)
        if c.get('source_kind','primary')!='primary' or c['candidate_id'] in seen:continue
        if any(_value(c,key)!=value for key,value in facts.items()):continue
        period=parse_period(c.get('period'))
        if period is None:continue
        seen.add(c['candidate_id'])
        building,room=split_room(c['room']);reasons=[];flags=[]
        campus=c.get('campus','beibei')
        use_geo=building in geo and campus in ('beibei','北区','南区','北碚','西塔学院')
        position=geo.get(building,1)
        location_weight=parameters.position_weight*summary['reliability'] if use_geo else 0
        if 'building' in facts:reasons.append('楼栋已由你确认')
        elif not use_geo:flags.append('该楼栋定位证据不足')
        elif position>.45:reasons.append('定位较接近')
        else:flags.append('定位可能偏移')
        door=_door_likelihood(room,hypotheses) if hypotheses and 'room_number' not in facts else 1
        if 'room_number' in facts:reasons.append('门牌已由你确认')
        elif hypotheses and any(room==_number(h['value']) for h in hypotheses):reasons.append('门牌读数吻合')
        elif hypotheses:flags.append('门牌需核对')
        time,delta=_time_likelihood(period,clock,parameters)
        distance=_distance(geometry[building],summary['latitude'],summary['longitude']) if use_geo else None
        door_distance=min((_edit_distance(room,_number(h['value'])) for h in hypotheses),default=None)
        door_supported=('room_number' in facts or any(room==_number(h['value']) and h['score']>=.65 for h in hypotheses))
        support={'building':building,'room_number':room if re.fullmatch(r'[A-Za-z]?\d{3,4}',room) else '',
                 'period':f'{period[0]}-{period[1]}','door_supported':door_supported,
                 'door_distance':0 if 'room_number' in facts else door_distance,
                 'time_supported':'period' in facts or (delta is not None and delta<=10 and time_reliability>=.5),
                 'nearby':distance is not None and distance<=radius}
        manual_time=period[1]>len(PERIODS)
        if manual_time:flags.append('节次超出已配置作息，需手动核对')
        if delta is not None:
            if delta==0:reasons.append('拍照时段吻合')
            elif delta<=10:reasons.append('接近该课程时段')
            else:flags.append('时间需核对')
        door_weight=parameters.door_weight*min(.85,max((h['score'] for h in hypotheses),default=0))/.85
        time_weight=parameters.time_weight*max(0,min(1,time_reliability)) if delta is not None else 0
        # Missing geometry must not win merely because a mapped, displaced building
        # used to receive a negative score. Location is bounded positive support.
        components={'location':location_weight*math.log1p(position),
                    'door':door_weight*math.log(door),'time':time_weight*math.log(time)}
        ranked.append({**c,'match_score':round(sum(components.values()),6),
                       'match_components':components,'match_reasons':reasons,
                       'match_uncertainties':flags,'requires_manual_time':manual_time,
                       'site_support':support,'site_distance_m':round(distance) if distance is not None else None})
    ranked.sort(key=lambda c:(-c['match_score'],c['candidate_id']))
    guidance=choose_guidance(ranked,facts=facts,asked=asked,question_count=question_count,
        scoped_location=scoped_location,question_picker=lambda pool:_next_question(
            pool,facts,asked,parameters,minimum=2,max_options=6))
    guidance.update(location_scoped=scoped_location,nearby_radius_m=round(radius) if scoped_location else None)
    return {'model_version':'site-context-v3','score_kind':'uncalibrated_match','guidance':guidance,
            'candidates':ranked,'question':_next_question(ranked,facts,asked,parameters,hypotheses),
            'confirmed_facts':facts,'requires_confirmation':True,'manual_available':True,
            'uncertainties':uncertainties}
