"""Evaluate presentation and question routing using saved paired synthetic inputs."""
import json
from collections import Counter
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.site_context_replay import load_workbook_candidates
from app.services.site_context_model import rank_site_candidates,_value


def evaluate(source_path=None):
    source=json.loads((source_path or ROOT/'output/2026-10-02-site-context-refinement/paired-replay.json').read_text(encoding='utf-8'))
    daily,workbook=load_workbook_candidates(next(ROOT.glob('*.xlsx')))
    records=[]
    for record in source['records']:
        target=record['ground_truth'];rows=daily[target['lecture_date']];evidence=record['input']
        result=rank_site_candidates(rows,**evidence);initial=result['guidance'];initial_ranked=result['candidates'];facts={};asked=[];steps=[]
        while result['guidance']['mode']=='clarify' and len(steps)<2:
            q=result['guidance']['question'];kind=q['kind'];truth=_value(target,kind)
            offered=any(o['value']==truth for o in q['options'])
            if offered:facts[kind]=truth
            asked.append(kind);steps.append({'kind':kind,'answer':truth if offered else None,'offered_truth':offered})
            result=rank_site_candidates(rows,**evidence,confirmed_facts=facts,asked=asked,question_count=len(steps))
        target_id=target['candidate_id'];ids=initial['candidate_ids'][:3];final=result['guidance']
        records.append({'variant':record['variant'],'target_id':target_id,'target_room':target['room'],
            'previous_top3_hit':any(c['candidate_id']==target_id for c in record['fusion_top3']),
            'initial_mode':initial['mode'],'initial_ids':ids,
            'direct_hit':initial['mode']=='recommend' and target_id in ids,
            'direct_count':len(ids) if initial['mode']=='recommend' else 0,
            'previous_far_count':sum(c['site_distance_m'] is not None and initial['nearby_radius_m'] is not None
                and c['site_distance_m']>initial['nearby_radius_m'] for c in initial_ranked[:3]),
            'default_far_count':sum(c['candidate_id'] in ids and c['site_distance_m'] is not None
                and initial['nearby_radius_m'] is not None and c['site_distance_m']>initial['nearby_radius_m']
                for c in result['candidates']) if initial['mode']=='recommend' else 0,
            'questions':steps,'final_mode':final['mode'],
            'final_direct_hit':final['mode']=='recommend' and target_id in final['candidate_ids'][:3],
            'target_retained':any(c['candidate_id']==target_id for c in result['candidates'])})
    def summarize(rows):
        direct=[r for r in rows if r['initial_mode']=='recommend'];final=[r for r in rows if r['final_mode']=='recommend']
        return {'count':len(rows),'initial_modes':dict(Counter(r['initial_mode'] for r in rows)),
            'previous_top3_hits':sum(r['previous_top3_hit'] for r in rows),
            'direct_top3_hits':sum(r['direct_hit'] for r in direct),'direct_count':len(direct),
            'mean_direct_cards':sum(r['direct_count'] for r in direct)/len(direct) if direct else 0,
            'default_far_cards':sum(r['default_far_count'] for r in rows),
            'previous_far_cards':sum(r['previous_far_count'] for r in rows),
            'final_modes_after_ideal_answers':dict(Counter(r['final_mode'] for r in rows)),
            'final_direct_hits':sum(r['final_direct_hit'] for r in final),'final_direct_count':len(final),
            'mean_questions':sum(len(r['questions']) for r in rows)/len(rows),
            'max_questions':max(len(r['questions']) for r in rows),'targets_retained':sum(r['target_retained'] for r in rows)}
    return {'scope':'Same-source synthetic presentation evaluation, not field accuracy; answers use truth only when offered, otherwise skip',
        'source':workbook,'metrics':{'all':summarize(records),**{v:summarize([r for r in records if r['variant']==v]) for v in sorted({r['variant'] for r in records})}},'records':records}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'output/2026-10-02-adaptive-site-guidance/replay.json')
    args=parser.parse_args();result=evaluate(args.input);path=args.output
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result['metrics'],ensure_ascii=False,indent=2))
