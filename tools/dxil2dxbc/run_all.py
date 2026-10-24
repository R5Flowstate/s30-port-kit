import os, sys, glob, json, collections, re
from concurrent.futures import ProcessPoolExecutor as ThreadPoolExecutor
import dxil2s21, twin_gate



def role(t):
    names = {b[0] for b in __import__('refl').bindings(t, False)}
    return tuple(sorted(names & {'g_modelInst', 'CBufModelInstance', 'DitherTexture', 'g_boneTransforms'})), t.count('\nvs_') > 0


def one(job):
    f, static = job
    try:
        r = dxil2s21.convert(f, static=static)
    except Exception as e:
        import traceback
        r = {'ok': False, 'err': 'EXC ' + traceback.format_exc()[-600:]}
    r['n'] = os.path.basename(f)[:-5]
    r['static'] = static
    g = int(r['n'].split('_')[0], 16)
    tw = None
    if r.get('ok'):
        mine = role(twin_gate.disasm(r['dxbc']))
        for k in range(8):
            p = os.path.join('s21all', '0x%016X_%d.dxbc' % (g, k))
            if os.path.exists(p) and role(twin_gate.disasm(p)) == mine:
                tw = p
                break
        r['twin_path'] = tw
    if tw:
        try:
            r['twin'] = twin_gate.compare(r['dxbc'], tw)
        except Exception as e:
            r['twin'] = {'err': str(e)}
    return r


if __name__ == '__main__':
    fs = sorted(glob.glob('blobs/*.dxbc'))
    if len(sys.argv) > 1:
        fs = [f for f in fs if re.search(sys.argv[1], f)]
    with ThreadPoolExecutor(12) as ex:
        res = list(ex.map(one, [(f, False) for f in fs]))
        vs = [r for r in res if r.get('ok') and r.get('kind') == 1]
        res += [r for r in ex.map(one, [('blobs/%s.dxbc' % r['n'], True) for r in vs]) if not r.get('skip')]
    json.dump(res, open('run_all.json', 'w'), indent=0)
    c = collections.Counter()
    um = collections.Counter()
    errs = collections.Counter()
    for r in res:
        if r.get('skip'):
            c['skip_' + r['skip']] += 1
        elif r.get('ok'):
            c['ok'] += 1
        else:
            c['fail'] += 1
            for k, v in r.get('unmapped', {}).items():
                um[k] += 1
            if r.get('err') and r['err'] != 'unmapped':
                e = re.sub(r'\(\d+,\d+(-\d+)?\)', '', r['err'])
                e = re.sub(r'[A-Z]:\\[^:]*\.hlsl', '', e)
                errs[e[:220]] += 1
    print(dict(c))
    print('unmapped fields (blobs):')
    for k, v in um.most_common(): print('  ', v, k)
    print('errors:')
    for k, v in errs.most_common(25): print('  ', v, k)
    tw = [r['twin'] for r in res if r.get('twin') and 'err' not in r['twin']]
    print('twins', len(tw), 'in_ok', sum(t['in'] for t in tw), 'out_ok', sum(t['out'] for t in tw),
          'binds_exact', sum(not t['binds_extra'] and not t['binds_missing'] for t in tw),
          'cb_exact', sum(not t['cb_extra'] and not t['cb_missing'] for t in tw))
