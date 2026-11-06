"""Statistics date inputs use local calendar dates, including UTC+8 midnight."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('template', [
    'department_monthly_assessment_stats.html',
    'submission_count_stats.html',
    'review_assessment_stats.html',
])
@pytest.mark.parametrize('hour', [0, 15])
def test_default_range_is_november_first_through_today_in_shanghai(template, hour):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for template date execution')
    source = (Path(__file__).resolve().parents[1] / 'app/templates/admin' / template).read_text(encoding='utf-8')
    function = re.search(r'function setDefaultDates\(\) \{.*?\n\}', source, re.DOTALL)
    assert function is not None
    script = """
const NativeDate = Date;
const fixedNow = new NativeDate(2026, 10, 6, HOUR, 30, 0);
global.Date = class extends NativeDate {
    constructor(...args) { super(...(args.length ? args : [fixedNow.getTime()])); }
    static now() { return fixedNow.getTime(); }
};
const fields = {startDate: {value: ''}, endDate: {value: ''}};
const document = {getElementById: (id) => fields[id]};
FUNCTION
setDefaultDates();
process.stdout.write(JSON.stringify(fields));
""".replace('HOUR', str(hour)).replace('FUNCTION', function.group(0))
    result = subprocess.run([node, '-e', script], env={**os.environ, 'TZ': 'Asia/Shanghai'},
                            capture_output=True, text=True, timeout=15, check=True)
    fields = json.loads(result.stdout)
    assert fields['startDate']['value'] == '2026-11-01'
    assert fields['endDate']['value'] == '2026-11-06'
