"""Portable author snapshots. Catalog references are never inferred on import."""
import copy
import json
import re
from .store import integer

FIELDS={'format','origin','catalog_ref','source_catalog_ref','name','bio','site_names','byline','site_id'}


def text(value,limit,required=False):
    if not isinstance(value,str) or '\x00' in value or len(value)>limit or (required and not value.strip()):
        raise ValueError('Некорректные сведения об авторе')
    return value


def reference(value):
    if value is None:return None
    if not isinstance(value,dict) or set(value)!={'id','revision'} or not isinstance(value['id'],str) or not re.fullmatch('[a-f0-9]{32}',value['id']):
        raise ValueError('Некорректная ссылка на версию автора')
    integer(value['revision'],'Версия автора',1)
    return dict(value)


def snapshot(profile,site_id):
    return {'format':1,'origin':'catalog','catalog_ref':{'id':profile['id'],'revision':profile['revision']},
            'source_catalog_ref':None,'name':profile['name'],'bio':profile['bio'],'site_names':dict(profile['site_names']),
            'byline':profile['site_names'].get(site_id,profile['name']),'site_id':site_id}


def validate(value,author,*,c=None):
    if value is None:return None
    if not isinstance(value,dict) or set(value)!=FIELDS or type(value['format']) is not int or value['format']!=1 or value['origin'] not in ('catalog','package_claimed'):
        raise ValueError('Неизвестный формат сведений об авторе')
    clean=copy.deepcopy(value)
    for key,limit in [('name',240),('bio',4000),('byline',240)]:text(clean[key],limit,key!='bio')
    if clean['byline']!=author:raise ValueError('Подпись не совпадает со сведениями об авторе. Выберите профиль заново или отсоедините сведения.')
    if clean['site_id'] is not None:text(clean['site_id'],100,True)
    names=clean['site_names']
    if not isinstance(names,dict) or len(names)>100:raise ValueError('Некорректные подписи автора')
    for site,name in names.items():text(site,100,True);text(name,240,True)
    if clean['byline']!=names.get(clean['site_id'],clean['name']):raise ValueError('Некорректная подпись проекта')
    local=reference(clean['catalog_ref']);source=reference(clean['source_catalog_ref'])
    if clean['origin']=='package_claimed':
        if local is not None:raise ValueError('Импортированные сведения не связаны с местным каталогом')
    else:
        if local is None or source is not None:raise ValueError('Нужна точная версия местного автора')
        if c is not None:
            row=c.execute('SELECT snapshot FROM content_author_revisions WHERE author_id=? AND revision=?',(local['id'],local['revision'])).fetchone()
            if not row or snapshot(json.loads(row['snapshot']),clean['site_id'])!=clean:
                raise ValueError('Сведения не совпадают с сохранённой версией автора')
    return clean


def imported(value,author):
    clean=validate(value,author)
    if clean is not None:
        clean['source_catalog_ref']=clean['catalog_ref'] or clean['source_catalog_ref']
        clean['catalog_ref']=None;clean['origin']='package_claimed'
    return clean


def for_site(value,site_id):
    if value is None:return None
    result=copy.deepcopy(value);result['site_id']=site_id
    result['byline']=result['site_names'].get(site_id,result['name'])
    return result


def carry(previous,patch):
    """A manual byline/project change detaches the former profile by default."""
    out={**previous,**patch}
    if 'author_credit' not in patch and any(k in patch and patch[k]!=previous.get(k) for k in ('author','site_id')):
        out.pop('author_credit',None)
    return out
