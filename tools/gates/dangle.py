import csv, re, sys
ws = sys.argv[1]
oracle = {int(r['guid'], 16) for r in csv.DictReader(open(ws + '/inv/s21_oracle.csv', encoding='utf-8', errors='replace'))
          if not r['file_name'].lower().startswith('mp_lobby')}
t = open(ws + '/inv/deps.txt', errors='replace').read()
x = set(int(m, 16) for m in re.findall(r'([0-9A-Fa-f]{6,16})\*', t))
d = [g for g in x if g not in oracle]
print('unresolved', len(x), 'dangling', len(d), [hex(g) for g in d])
