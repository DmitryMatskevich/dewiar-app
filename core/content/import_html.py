"""Conservative HTML-to-block import: unsupported markup stays opaque and intact.

This intentionally accepts only plain structural HTML. Formatting, attributes,
forms, media and malformed markup are retained in the original, never rendered.
"""
from html.parser import HTMLParser
import hashlib
from .assets import put, read

VOID={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}


class Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.root={'tag':'root','attrs':[],'children':[]};self.stack=[self.root];self.count=0;self.malformed=False
    def handle_starttag(self,tag,attrs):
        self.count+=1
        if self.count>5000 or len(self.stack)>64:raise ValueError('HTML слишком сложен: предел 5000 элементов / 64 уровня')
        node={'tag':tag,'attrs':attrs,'children':[]};self.stack[-1]['children'].append(node)
        if tag not in VOID:self.stack.append(node)
    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in VOID:self.handle_endtag(tag)
    def handle_endtag(self,tag):
        if len(self.stack)>1 and self.stack[-1]['tag']==tag:self.stack.pop()
        else:self.malformed=True
    def handle_data(self,data):self.stack[-1]['children'].append(data)
    def handle_comment(self,data):self.stack[-1]['children'].append({'tag':'comment','attrs':[],'children':[]})
    def handle_decl(self,data):self.stack[-1]['children'].append({'tag':'doctype','attrs':[],'children':[]})


def text_only(node):
    result=[]
    for child in node['children']:
        if isinstance(child,str):result.append(child)
        elif child['tag']=='br' and not child['attrs']:result.append('\n')
        else:return None
    return ''.join(result)


def parse(raw,digest):
    parser=Tree();parser.feed(raw);parser.close();blocks=[]
    def add(kind,text,opaque=False):
        b={'id':'html-'+str(len(blocks)+1)+'-'+digest[:12],'type':kind,'text':text,'locked':opaque}
        if opaque:b['original_ref']=digest
        blocks.append(b)
    def opaque(tag):add('unsupported',f'HTML <{tag}> сохранён в оригинале. Нужна отдельная обработка перед публикацией.',True)
    def visit(node):
        if isinstance(node,str):
            if node.strip():add('paragraph',node)
            return
        tag=node['tag']
        if tag in ('html','body','article','main','section','div') and not node['attrs']:
            for child in node['children']:visit(child)
            return
        if node['attrs']:opaque(tag);return
        text=text_only(node)
        kind={'p':'paragraph','h1':'heading','h2':'heading','blockquote':'quote'}.get(tag)
        if kind and text is not None:add(kind,text);return
        if tag=='ul':
            items=[x for x in node['children'] if not isinstance(x,str) or x.strip()]
            if items and all(isinstance(x,dict) and x['tag']=='li' and not x['attrs'] and text_only(x) is not None and '\n' not in text_only(x) for x in items):
                add('list','\n'.join(text_only(x) for x in items));return
        opaque(tag)
    if parser.malformed or len(parser.stack)!=1:opaque('некорректная структура')
    else:
        for node in parser.root['children']:visit(node)
    if not blocks:raise ValueError('HTML не содержит материала')
    return blocks


def import_document(store,args):
    raw=args.get('html')
    if not isinstance(raw,str) or not raw.strip() or '\x00' in raw or len(raw.encode())>1048576:raise ValueError('Нужен HTML до 1 МиБ без нулевого символа')
    data=raw.encode();digest=hashlib.sha256(data).hexdigest();blocks=parse(raw,digest)
    from .models import validate_blocks
    validate_blocks(blocks)
    put(store,data,'text/html')
    source={'source_kind':'html','task_title':'Импорт HTML','path':'original.html.txt','asset_id':digest,'sha256':digest,'quality':'requires_editor_review'}
    return store.create({'title':args.get('title') or 'Материал из HTML','kind':args.get('kind','article'),
                         'author':args.get('author',''),'site_id':args.get('site_id'),'blocks':blocks},args.get('request_key'),source=source)


def original(store,mid):
    import base64
    sources=store.sources(mid);source=next((s for s in sources if s.get('source_kind')=='html'),None)
    if not source:raise ValueError('У материала нет HTML-оригинала')
    return {'filename':f'original-{mid}.html.txt','mime':'text/plain; charset=utf-8','b64':base64.b64encode(read(store,source['asset_id'])).decode()}
