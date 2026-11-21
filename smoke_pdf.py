import json, os, subprocess, sys, tempfile, difflib
from datetime import datetime
import report as R

DB = json.load(open('questions.json', encoding='utf-8'))
SNAP = 'snap'
NOW = datetime(2026, 1, 1, 12, 0)
EXTRA = {
    'pcl5': {'clusters': {'B': 10, 'C': 4, 'D': 14, 'E': 12}, 'cutoff_met': True, 'dsm_provisional': False},
    'express': {'scales': [{'name': 'PHQ-9 (депресія)', 'score': 9, 'max': 27, 'level_ua': 'x'},
                           {'name': 'GAD-7 (тривога)', 'score': 11, 'max': 21, 'level_ua': 'y'}]},
}

def vals(spec, i):
    qo = spec.get('qoptions')
    return qo[i]['values'] if qo else spec['values']

def answers(spec, mode):
    out = []
    for i in range(len(spec['questions'])):
        v = sorted(vals(spec, i))
        out.append({'min': v[0], 'max': v[-1], 'mid': v[len(v) // 2]}[mode])
    return out

def cases():
    for t, spec in DB.items():
        for m in ('min', 'mid', 'max'):
            yield f'{t}_{m}', t, answers(spec, m)
        if t in ('phq9', 'express'):
            a = answers(spec, 'max'); a[8] = sorted(vals(spec, 8))[0]
            yield f'{t}_i9_0', t, a
            b = list(a); b[8] = sorted(vals(spec, 8))[1]
            yield f'{t}_i9_1', t, b

def build(t, ans):
    d = {'answers': ans, 'sex': 'M'}
    d.update(EXTRA.get(t, {}))
    return {'test_type': t, 'test_name': t, 'patient_name': 'Тест', 'completed_at': NOW,
            'score': sum(ans), 'severity': 'pending', 'details': d}

def snapshot(res):
    t, d = res['test_type'], res['details']
    out = {}
    for name, fn in (('rows', lambda: R.item_rows(t, res, d)), ('lines', lambda: R.item_lines(t, res, d)),
                     ('i9', lambda: R.item9_alert(res, d)),
                     ('sev', lambda: R.severity_ua(t, res['severity'], res['score']))):
        try: out[name] = fn()
        except Exception as e: out[name] = f'EXC {e!r}'
    try:
        buf = R.generate_pdf_report(res)
        with tempfile.NamedTemporaryFile(suffix='.pdf') as f:
            f.write(buf.read()); f.flush()
            out['pdf'] = subprocess.run(['pdftotext', '-layout', f.name, '-'], capture_output=True,
                                        text=True, check=True).stdout.splitlines()
    except Exception as e:
        out['pdf'] = f'EXC {e!r}'
    return json.dumps(out, ensure_ascii=False, indent=1, default=str)

if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'check'
    os.makedirs(SNAP, exist_ok=True)
    bad = 0
    for name, t, ans in cases():
        cur, path = snapshot(build(t, ans)), f'{SNAP}/{name}.json'
        if mode == 'snap':
            open(path, 'w', encoding='utf-8').write(cur)
        else:
            old = open(path, encoding='utf-8').read() if os.path.exists(path) else ''
            if old != cur:
                bad += 1
                print(f'--- DIFF {name}')
                print('\n'.join(list(difflib.unified_diff(old.splitlines(), cur.splitlines(), lineterm='', n=1))[:30]))
    print('cases:', len(list(cases())), 'diffs:', bad)
    sys.exit(1 if bad else 0)
