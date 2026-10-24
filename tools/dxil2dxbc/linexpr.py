"""Tiny linear-expression resolver over SPIRV-Cross HLSL text.

lin(expr, defs) -> (terms {atom: coef}, const) where atoms are opaque strings.
Handles identifiers, uint/int literals, + - *, parentheses, and SSA uint
definitions `uint _N = <expr>;` found in the same function.
"""
import re

TOK = re.compile(r'\s*(?:(\d+)u?|(_\d+|[A-Za-z_]\w*(?:\.[xyzw])?)|(.))')


def tokenize(s):
    out = []
    pos = 0
    while pos < len(s):
        m = TOK.match(s, pos)
        if not m or m.end() == pos:
            break
        pos = m.end()
        if m.group(1) is not None:
            out.append(('n', int(m.group(1))))
        elif m.group(2) is not None:
            out.append(('id', m.group(2)))
        elif m.group(3) and not m.group(3).isspace():
            out.append(('op', m.group(3)))
    return out


class Opaque(Exception):
    pass


def lin(expr, defs, depth=0):
    toks = tokenize(expr)
    p = [0]

    def peek():
        return toks[p[0]] if p[0] < len(toks) else None

    def take():
        t = toks[p[0]]
        p[0] += 1
        return t

    def add(a, b, sign=1):
        ta, ca = a
        tb, cb = b
        t = dict(ta)
        for k, v in tb.items():
            t[k] = t.get(k, 0) + sign * v
        return ({k: v for k, v in t.items() if v}, ca + sign * cb)

    def mul(a, b):
        ta, ca = a
        tb, cb = b
        if ta and tb:
            raise Opaque()
        if not ta:
            ta, ca, tb, cb = tb, cb, ta, ca
        # a is (ta, ca), b is constant cb
        return ({k: v * cb for k, v in ta.items()}, ca * cb)

    def primary():
        t = peek()
        if t is None:
            raise Opaque()
        if t == ('op', '('):
            take()
            v = sum_()
            if peek() != ('op', ')'):
                raise Opaque()
            take()
            return v
        if t[0] == 'n':
            take()
            return ({}, t[1])
        if t[0] == 'id':
            take()
            name = t[1]
            if peek() in (('op', '('), ('op', '[')):
                raise Opaque()
            if name in defs and depth < 40:
                r = lin(defs[name], defs, depth + 1)
                if r is not None:
                    return r
            return ({name: 1}, 0)
        raise Opaque()

    def prod():
        v = primary()
        while peek() == ('op', '*'):
            take()
            v = mul(v, primary())
        return v

    def sum_():
        v = prod()
        while peek() in (('op', '+'), ('op', '-')):
            op = take()[1]
            v = add(v, prod(), 1 if op == '+' else -1)
        return v

    try:
        v = sum_()
        if p[0] != len(toks):
            raise Opaque()
        return v
    except (Opaque, IndexError):
        return None


def render(terms):
    parts = []
    for k, v in terms.items():
        parts.append(k if v == 1 else '(%s * %du)' % (k, v))
    return ' + '.join(parts) if parts else '0u'
