"""Minimal S-expression reader/writer for KiCad files (symbols, schematics)."""
import re

_TOKEN = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+')


class Sym(str):
    """Bare (unquoted) atom."""


def parse(text):
    stack, cur = [], []
    for m in _TOKEN.finditer(text):
        t = m.group(0)
        if t == '(':
            stack.append(cur)
            cur = []
        elif t == ')':
            done = cur
            cur = stack.pop()
            cur.append(done)
        elif t[0] == '"':
            cur.append(t[1:-1].replace('\\"', '"').replace('\\\\', '\\'))
        else:
            cur.append(Sym(t))
    return cur[0] if len(cur) == 1 else cur


def q(s):
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'


def dump(node, indent=0):
    """Serialise nested lists; str -> quoted, Sym/int/float -> bare."""
    if not isinstance(node, list):
        if isinstance(node, Sym):
            return str(node)
        if isinstance(node, bool):
            return 'yes' if node else 'no'
        if isinstance(node, (int, float)):
            return fmt_num(node)
        return q(node)
    if not node:
        return '()'
    head = dump(node[0])
    simple = all(not isinstance(x, list) for x in node[1:])
    if simple:
        return '(' + ' '.join(dump(x) for x in node) + ')'
    pad = '\t' * (indent + 1)
    parts = [head]
    for x in node[1:]:
        if isinstance(x, list):
            parts.append('\n' + pad + dump(x, indent + 1))
        else:
            parts.append(' ' + dump(x))
    return '(' + ''.join(parts) + '\n' + '\t' * indent + ')'


def fmt_num(v):
    if isinstance(v, int):
        return str(v)
    s = ('%.4f' % v).rstrip('0').rstrip('.')
    return '0' if s in ('-0', '') else s


def find(node, key):
    for x in node:
        if isinstance(x, list) and x and x[0] == key:
            return x
    return None


def find_all(node, key):
    return [x for x in node if isinstance(x, list) and x and x[0] == key]
