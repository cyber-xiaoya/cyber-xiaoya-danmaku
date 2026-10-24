import base64
from bilibili_client import _pb_fields

s1 = ('CN7umocCEhBiaWxpXzQ3MTE0NTI2ODQ2Gi5odHRwOi8vaTAuaGRzbGIuY29tL2Jmcy9mYWNlL21lbWJlci9ub2ZhY2UuanBnQgBSiwUI0YkCEg/nsonkuJ3lm6Lnga/niYwYASABKGQwZDhkQgRnb2xkShM0ODE3MzU1ODM1NjY5NzkxMjMyUOzypdUGWAFiPGJhdGNoOmdpZnQ6Y29tYm9faWQ6NTUxOTkxMTM0OjExOTQ2Mjg3OjM0MDAxOjE3ODk0OTE1NjQuNDA3OGgKcGR4BYUBAACAP4gBAZIBBuaKleWWgsABnwPqARMKDOWkqeW3peW8gOW4pxCvktkFigLLAQivktkFEsMBCgzlpKnlt6XlvIDluKcSSmh0dHBzOi8vaTIuaGRzbGIuY29tL2Jmcy9mYWNlLzkyOWZjMDc1OTRhNGFiMGIzNGYyMDQzMWQ0ZGQyYmZhOGIwYTMyZWEuanBnMloKDOWkqeW3peW8gOW4pxJKaHR0cHM6Ly9pMi5oZHNsYi5jb20vYmZzL2ZhY2UvOTI5ZmMwNzU5NGE0YWIwYjM0ZjIwNDMxZDRkZDJiZmE4YjBhMzJlYS5qcGc6CyD///////////8BkgIHCI')
s2 = ('COrfICjAjv...')  # 样本2只截到开头, 不用

for label, s in (('样本1', s1),):
    raw = base64.b64decode(s + '=' * (-len(s) % 4))
    print(f'==== {label} ====')
    for f, wt, v in _pb_fields(raw):
        if isinstance(v, bytes):
            try:
                txt = v.decode('utf-8')
                printable = all(31 < ord(c) < 127 or ord(c) > 127 for c in txt)
            except Exception:
                txt, printable = '', False
            if printable and len(txt) < 60 and txt:
                print(f'  field={f} wt={wt} len={len(v)} text={txt!r}')
            else:
                print(f'  field={f} wt={wt} len={len(v)} hex={v[:12].hex(" ")}...')
        else:
            print(f'  field={f} wt={wt} val={v}')
