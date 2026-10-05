"""Conservative presentation policy over a complete ranked candidate set.

Thresholds are initial UX/routing rules, not calibrated correctness probabilities.
The complete set is always retained for explicit expansion and correction.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class GuidanceParameters:
    max_questions: int = 2
    max_options: int = 6
    leading_margin: float = .7


POLICY = GuidanceParameters()


def choose_guidance(ranked, *, facts, asked, question_count, scoped_location, question_picker,
                    parameters=POLICY):
    def scope_ok(c):
        return not scoped_location or bool(facts.get('building')) or c['site_support']['nearby']

    def strong(c):
        s=c['site_support']
        return (not c['requires_manual_time'] and scope_ok(c) and s['door_supported']
                and (s['time_supported'] or 'building' in facts or 'teacher_name' in facts))

    qualified=[c for c in ranked if strong(c)]
    pool=qualified
    if not pool:
        # A nearby approximate door + plausible time can support a clarification,
        # but cannot silently replace the photographed number.
        pool=[c for c in ranked if scope_ok(c) and not c['requires_manual_time']
              and ((c['site_support']['door_distance'] is not None
                    and c['site_support']['door_distance']<=1 and c['site_support']['time_supported'])
                   or c['site_support']['door_supported']
                   or (c['site_support']['nearby'] and c['site_support']['time_supported']))]
    if not pool:
        pool=[c for c in ranked if c['site_support']['door_supported'] and not c['requires_manual_time']]
    if pool:
        peak=pool[0]['match_score']
        pool=[c for c in pool if peak-c['match_score']<parameters.leading_margin]
    result={'mode':'expand','candidate_ids':[],'question':None,
            'questions_used':question_count,'questions_remaining':max(0,parameters.max_questions-question_count),
            'total_candidates':len(ranked),'reason':'线索还不充分，可扩大范围查找，或调整教室信息。',
            'policy_version':'site-guidance-v1','thresholds_calibrated':False}
    if not ranked:
        result['reason']='暂未找到课程，照片已保存，可调整信息或稍后补充。'
        return result
    if qualified and len(pool)==1:
        result.update(mode='recommend',candidate_ids=[pool[0]['candidate_id']],reason='已找到更吻合的课程，请确认本次听课。')
        return result
    same_scene=len({(c['site_support']['building'],c['site_support']['room_number'],
                     c['site_support']['period']) for c in pool})==1
    if qualified and same_scene and len(pool)<=3:
        result.update(mode='recommend',candidate_ids=[c['candidate_id'] for c in pool],
                      reason='教室和节次已匹配，请核对课程或班级。')
        return result
    if question_count>=parameters.max_questions:
        result['reason']='已尝试两次核对，仍不确定。可扩大范围查看课程，或修改信息。'
        return result
    question=question_picker(pool) if pool else None
    if not question and pool and not qualified:
        # A single supported hypothesis may be asked as confirmation, with explicit
        # unsure/none controls in the UI. Do not manufacture values from absent evidence.
        top=pool[0]['site_support']
        missing=[kind for kind,needed in [('room_number',not top['door_supported']),
            ('building',scoped_location and not top['nearby']),('period',not top['time_supported'])]
            if needed and kind not in facts and kind not in asked]
        for kind in missing:
            options={}
            for c in pool:
                s=c['site_support'];value=s[kind]
                if not value:continue
                label=(value.zfill(4) if kind=='room_number' and value.isdigit() else
                       f'{value}教' if kind=='building' and value.isdigit() else
                       f'第{value}节' if kind=='period' else value)
                options.setdefault(value,{'value':value,'label':label,'candidate_count':0})['candidate_count']+=1
            if 0<len(options)<=parameters.max_options:
                question={'kind':kind,'prompt':{'room_number':'请核对门牌上的数字：',
                    'building':'定位与课程位置不一致，请核对教学楼：','period':'请核对本次听课节次：'}[kind],
                    'options':list(options.values()),'expected_gain_bits':None}
                break
    if question:
        reason=('未取得可靠定位，请补充教学楼以缩小范围。'
                if question['kind']=='building' and not scoped_location and 'building' not in facts else
                ('附近有多个可能的教学楼，请核对你所在的位置。' if all(c['site_support']['nearby'] for c in pool)
                 else '定位与门牌对应的课程位置不一致，请核对教学楼。')
                if question['kind']=='building' and scoped_location else
                '门牌读数还不确定，请核对照片上的数字。'
                if question['kind']=='room_number' else
                '还有一处信息不确定，补充后我会重新匹配。')
        result.update(mode='clarify',candidate_ids=[c['candidate_id'] for c in pool],question=question,
                      reason=reason)
    elif qualified and same_scene:
        result.update(mode='recommend',candidate_ids=[c['candidate_id'] for c in pool],
                      reason='这些课程的现场线索相同，请核对课程或班级。')
    return result
