"""Shared command dispatch for the engine and isolated UI harness."""
from .store import ContentStore, FIELDS

COMMANDS = frozenset(('content_list', 'content_get', 'content_create', 'content_update',
                     'content_archive', 'content_sites', 'content_revisions',
                     'content_revision_get', 'content_restore_revision', 'content_sources', 'content_import_source', 'content_source_refs', 'content_export', 'content_diff', 'content_import_html', 'content_html_original', 'content_copy'))
GENERATION_COMMANDS=frozenset(('content_models','content_generate','content_generation_get','content_generation_list','content_generation_cancel','content_generation_apply'))
REVIEW_COMMANDS=frozenset(('content_review','content_review_save','content_source_excerpt'))
PACKAGE_COMMANDS=frozenset(('content_import_package',))


def dispatch(store, cmd, args, tasks=None):
    from . import source_records
    if cmd in source_records.COMMANDS:return source_records.dispatch(store,cmd,args,tasks)
    from . import video_review
    if cmd in video_review.COMMANDS:return video_review.dispatch(store,cmd,args)
    from . import captions
    if cmd in captions.COMMANDS:return captions.dispatch(store,cmd,args)
    from . import authors
    if cmd in authors.COMMANDS:return authors.dispatch(store,cmd,args)
    from . import higgsfield
    if cmd in higgsfield.COMMANDS:return higgsfield.dispatch(store,cmd,args)
    from . import video_exchange
    if cmd in video_exchange.COMMANDS:return video_exchange.dispatch(store,cmd,args)
    from . import video_store
    if cmd in video_store.COMMANDS:return video_store.dispatch(store,cmd,args)
    from . import social_store
    if cmd in social_store.COMMANDS:return social_store.dispatch(store,cmd,args)
    from . import distribution
    if cmd in distribution.COMMANDS:return distribution.dispatch(store,cmd,args)
    from .sites import service
    if cmd in service.COMMANDS:
        if not isinstance(args,dict):raise ValueError('Некорректные параметры сайта')
        return service.dispatch(store,cmd,args)
    if cmd not in COMMANDS|REVIEW_COMMANDS|PACKAGE_COMMANDS or not isinstance(args, dict):
        raise ValueError('Неизвестная команда контента')
    if cmd=='content_import_package':
        from .import_package import import_package
        return import_package(store,args)
    if cmd in REVIEW_COMMANDS:
        from . import review
        if cmd=='content_review':return review.report(store,args.get('id'))
        if cmd=='content_review_save':return review.save(store,args)
        if cmd=='content_source_excerpt':
            from .store import integer
            offset=integer(args.get('offset',0),'Смещение',0,1048576);limit=integer(args.get('limit',20000),'Размер фрагмента',1,20000)
            texts=review.source_texts(store,args.get('id'),args.get('revision'));s=next((s for s in texts if s['source_id']==args.get('source_id')),None) if args.get('source_id') else next((s for s in texts if s['sha256']==args.get('sha256')),None)
            if not s:raise ValueError('Источник не найден')
            return {'text':s['text'][offset:offset+limit],'total':len(s['text']),'offset':offset}
    if cmd == 'content_import_html':
        from .import_html import import_document
        return import_document(store,args)
    if cmd == 'content_html_original':
        from .import_html import original
        return original(store,args.get('id'))
    if cmd == 'content_copy':
        sources=store.sources(args.get('id'),args.get('source_revision'))
        return store.create(args.get('data'),args.get('request_key'),sources=sources)
    if cmd == 'content_diff':
        from .diff import compare
        return compare(store,args.get('id'),args.get('from_revision'),args.get('to_revision'))
    if cmd == 'content_sources':
        from .sources import available
        return available(tasks or {})
    if cmd == 'content_import_source':
        from .sources import snapshot
        source=snapshot(tasks or {},args.get('task_id'),args.get('path'))
        return store.create({'title':args.get('title') or source['task_title'] or 'Статья из источника',
                             'kind':args.get('kind','article'),'author':args.get('author',''),
                             'body':source['text'],'site_id':args.get('site_id')},args.get('request_key'),source=source)
    if cmd == 'content_source_refs':
        return source_records.listing(store,args)
    if cmd == 'content_export':
        from .export import package
        return package(store,args.get('id'),args.get('revision'))
    if cmd == 'content_sites': return store.sites()
    if cmd == 'content_list': return store.list(**{k: args[k] for k in ('query','kind','archived','offset','limit') if k in args})
    if cmd == 'content_get': return store.get(args.get('id'))
    if cmd == 'content_create':
        if set(args) - FIELDS - {'request_key'}: raise ValueError('Некорректные поля создания')
        return store.create({k: args[k] for k in FIELDS if k in args}, args.get('request_key'))
    if cmd == 'content_update': return store.update(args.get('id'), args.get('expected_revision'), args.get('patch'))
    if cmd == 'content_archive':
        if type(args.get('archived')) is not bool: raise ValueError('Укажите состояние архива')
        return store.update(args.get('id'), args.get('expected_revision'), {}, archived=args['archived'])
    if cmd == 'content_revisions': return store.revisions(args.get('id'), args.get('offset',0), args.get('limit',50))
    if cmd == 'content_revision_get': return store.revision(args.get('id'), args.get('revision'))
    if cmd == 'content_restore_revision':
        from .store import integer
        integer(args.get('revision'), 'Ревизия истории', 1)
        return store.update(args.get('id'), args.get('expected_revision'), {}, restore=args['revision'])


def command(host, cmd, args):
    from . import archives
    if cmd in archives.COMMANDS:
        from interface_origin import require_interface
        require_interface()
        return archives.command(host,ContentStore(host.workspace.ensure()/'Контент'),cmd,args)
    from . import oauth_service
    if cmd in oauth_service.COMMANDS:
        from interface_origin import require_interface
        require_interface()
        return oauth_service.command(host,ContentStore(host.workspace.ensure()/'Контент'),cmd,args)
    # Resolve per call: switching workspace must never reuse a previous store.
    store=ContentStore(host.workspace.ensure() / 'Контент')
    if cmd in GENERATION_COMMANDS:
        from . import generation,providers
        if not isinstance(args,dict):raise ValueError('Некорректные параметры генерации')
        if cmd=='content_models':return providers.profiles(host)
        if cmd=='content_generate':return generation.start(store,args,providers.selected(host,args.get('profile_id')))
        if cmd=='content_generation_get':return generation.get(store,args.get('job_id'))
        if cmd=='content_generation_list':return generation.jobs(store,args.get('id'))
        if cmd=='content_generation_cancel':return generation.cancel(store,args.get('job_id'))
        if cmd=='content_generation_apply':return generation.apply(store,args.get('job_id'))
    return dispatch(store, cmd, args, tasks=host.tasks.tasks)
