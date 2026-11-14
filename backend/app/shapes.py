"""AST extraction; symbolic expressions are never executed or guessed."""
import json
from urllib.parse import urljoin
from tree_sitter import Language, Parser
import tree_sitter_javascript
from sqlalchemy import select
from .db import SessionLocal, EndpointSpec

def extract(source):
    raw=source.encode()
    tree=Parser(Language(tree_sitter_javascript.language())).parse(raw)
    def text(n): return raw[n.start_byte:n.end_byte].decode() if n else ''
    def value(n):
        if n is None: return None
        if n.type=='string': return text(n)[1:-1]
        if n.type=='template_string' and not any(c.type=='template_substitution' for c in n.named_children): return text(n)[1:-1]
        if n.type in ('number','true','false','null'): return json.loads(text(n))
        if n.type=='object':
            out={}
            for c in n.named_children:
                if c.type=='pair': out[text(c.child_by_field_name('key')).strip('\'"')]=value(c.child_by_field_name('value'))
                else: out['$spread']={'$expression':text(c)}
            return out
        if n.type=='array': return [value(c) for c in n.named_children]
        if n.type=='call_expression' and text(n.child_by_field_name('function'))=='JSON.stringify':
            args=n.child_by_field_name('arguments').named_children
            if args: return value(args[0])
        return {'$expression':text(n)}
    result=[]
    def walk(n):
        if n.type=='call_expression':
            fn=text(n.child_by_field_name('function'))
            argnode=n.child_by_field_name('arguments')
            args=argnode.named_children if argnode else []
            shape=None
            if args and fn in ('fetch','window.fetch'):
                cfg=value(args[1]) if len(args)>1 else {}
                cfg=cfg if isinstance(cfg,dict) else {}
                shape={'url':value(args[0]),'method':cfg.get('method','GET'),'body':cfg.get('body'),'headers':cfg.get('headers',{})}
            elif args and fn in ('axios','axios.request'):
                cfg=value(args[0])
                if isinstance(cfg,dict) and 'url' in cfg: shape={'url':cfg['url'],'method':cfg.get('method','GET'),'body':cfg.get('data'),'headers':cfg.get('headers',{}),'params':cfg.get('params',{})}
            elif args and fn.startswith('axios.') and fn.split('.')[-1] in ('get','post','put','patch','delete','options','head'):
                method=fn.split('.')[-1].upper()
                writes=method in ('POST','PUT','PATCH')
                index=2 if writes else 1
                cfg=value(args[index]) if len(args)>index else {}
                cfg=cfg if isinstance(cfg,dict) else {}
                shape={'url':value(args[0]),'method':method,'body':value(args[1]) if writes and len(args)>1 else None,'headers':cfg.get('headers',{}),'params':cfg.get('params',{})}
            if shape: result.append({**shape,'line':n.start_point.row+1})
        for c in n.named_children: walk(c)
    walk(tree.root_node)
    return result[:200]

def unresolved(value):
    if isinstance(value,dict): return '$expression' in value or any(unresolved(v) for v in value.values())
    return isinstance(value,list) and any(unresolved(v) for v in value)

async def capture(tid,source,url):
    shapes=extract(source)
    async with SessionLocal() as s:
        for shape in shapes:
            if not isinstance(shape['url'],str) or not isinstance(shape['method'],str): continue
            target=urljoin(url,shape['url'])
            old=(await s.execute(select(EndpointSpec).where(EndpointSpec.task_id==tid,EndpointSpec.url==target,EndpointSpec.method==shape['method'].upper(),EndpointSpec.source==url))).scalars().first()
            if not old: s.add(EndpointSpec(task_id=tid,url=target,method=shape['method'].upper(),shape=json.dumps(shape),source=url))
        await s.commit()
    return shapes
