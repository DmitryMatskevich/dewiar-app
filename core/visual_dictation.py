"""Session-scoped editor dictation, reusing the existing audio pipeline."""
import threading


def command(engine, cmd, args):
    owner=str(args.get('asset_id') or '')
    with engine._dictation_lock:
        if cmd=='visual_dictation_start':
            if engine.dictation_active:raise ValueError('Другая диктовка уже идёт. Сначала завершите её.')
            if getattr(engine,'hold',None) is not None:raise ValueError('Идёт голосовой ввод в другом окне.')
            engine._visual_dictation_target=owner
            try:
                result=engine.dictation_start()
            except Exception:
                engine._visual_dictation_target=None
                raise
            session=result['session']
            def expire():
                with engine._dictation_lock:
                    if getattr(engine,'_visual_dictation_target',None)==owner and engine._dictation_session==session:
                        engine.dictation_finish(submit=False,keep_draft=True)
                        def abandon():
                            with engine._dictation_lock:
                                if getattr(engine,'_visual_dictation_target',None)==owner and engine._dictation_session==session:
                                    engine.dictation_cancel()
                        grace=threading.Timer(30,abandon);grace.daemon=True;grace.start()
            timer=threading.Timer(120,expire);timer.daemon=True;timer.start()
            return result
        if getattr(engine,'_visual_dictation_target',None)!=owner or args.get('session')!=engine._dictation_session:
            return {'ok':False,'state':'idle'}
        if cmd=='visual_dictation_finish':return engine.dictation_finish(submit=False,keep_draft=True)
        if cmd=='visual_dictation_cancel':return engine.dictation_cancel()
        raise ValueError('Неизвестная команда диктовки')
