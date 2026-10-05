import re
import random

from tools.site_context_replay import evidence_for
from tests.test_site_context_model import candidate


def test_simulated_clock_rolls_over_hour_and_is_always_valid():
    e=evidence_for(candidate('a','8-609',(1,2)),'no_location',{},random.Random(1))
    assert e['clock']=='09:00'
    for p in range(1,15):
        e=evidence_for(candidate('a','8-609',(p,p)),'no_location',{},random.Random(1))
        assert re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',e['clock'])
