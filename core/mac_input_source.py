"""One-shot TIS worker: native calls run on this process's main thread.

The core server has no Cocoa run loop, so its NSOperationQueue.mainQueue
never drains. Calling TIS from a worker thread instead can abort the process.
Keep this entry point independent of the engine, UI, settings and models.
"""
import ctypes
import json
import sys
import threading
import time


def desired_input_source(layout_ids, language):
    layouts = [str(source_id).casefold() for source_id in layout_ids]
    if language == 'russian':
        matches = [sid for sid in layouts if 'russian' in sid]
        if not matches:
            raise ValueError('В настройках macOS не включена русская раскладка клавиатуры')
        return next((sid for sid in matches if sid == 'com.apple.keylayout.russian'), matches[0])
    if language != 'english':
        raise ValueError('Выберите английскую или русскую раскладку')
    matches = [sid for sid in layouts
               if any(label in sid for label in ('.abc', '.us', '.english', '.british', '.australian'))]
    if not matches:
        raise ValueError('В настройках macOS не включена английская раскладка клавиатуры')
    return next((sid for sid in matches if sid.endswith('.abc')),
                next((sid for sid in matches if sid.endswith('.us')), matches[0]))


class InputSources:
    def __init__(self):
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError('TIS must run on the worker process main thread')
        self.tis = ctypes.CDLL('/System/Library/Frameworks/Carbon.framework/Frameworks/HIToolbox.framework/HIToolbox')
        self.cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        ptr, index = ctypes.c_void_p, ctypes.c_long
        for library, name, args, result in (
            (self.tis, 'TISCreateInputSourceList', [ptr, ctypes.c_bool], ptr),
            (self.tis, 'TISCopyCurrentKeyboardInputSource', [], ptr),
            (self.tis, 'TISGetInputSourceProperty', [ptr, ptr], ptr),
            (self.tis, 'TISSelectInputSource', [ptr], ctypes.c_int32),
            (self.cf, 'CFArrayGetCount', [ptr], index),
            (self.cf, 'CFArrayGetValueAtIndex', [ptr, index], ptr),
            (self.cf, 'CFStringGetLength', [ptr], index),
            (self.cf, 'CFStringGetMaximumSizeForEncoding', [index, ctypes.c_uint32], index),
            (self.cf, 'CFStringGetCString', [ptr, ctypes.c_char_p, index, ctypes.c_uint32], ctypes.c_bool),
            (self.cf, 'CFBooleanGetValue', [ptr], ctypes.c_bool),
            (self.cf, 'CFRelease', [ptr], None),
        ):
            fn = getattr(library, name)
            fn.argtypes, fn.restype = args, result

    def constant(self, name):
        return ctypes.c_void_p.in_dll(self.tis, name).value

    def string(self, value):
        if not value:
            return ''
        encoding = 0x08000100  # kCFStringEncodingUTF8
        size = self.cf.CFStringGetMaximumSizeForEncoding(self.cf.CFStringGetLength(value), encoding) + 1
        buffer = ctypes.create_string_buffer(size)
        if not self.cf.CFStringGetCString(value, buffer, size, encoding):
            raise RuntimeError('Не удалось прочитать название раскладки macOS')
        return buffer.value.decode('utf-8')

    def property(self, source, name):
        return self.tis.TISGetInputSourceProperty(source, self.constant(name))

    def current(self):
        source = self.tis.TISCopyCurrentKeyboardInputSource()
        if not source:
            raise RuntimeError('macOS не сообщила текущую раскладку')
        try:
            source_id = self.string(self.property(source, 'kTISPropertyInputSourceID'))
        finally:
            self.cf.CFRelease(source)
        sid = source_id.casefold()
        language = ('russian' if 'russian' in sid else 'english' if any(
            tag in sid for tag in ('.abc', '.us', '.english', '.british', '.australian')) else 'other')
        return {'source_id': source_id, 'language': language}

    def select(self, language=None, source_id=None):
        sources = self.tis.TISCreateInputSourceList(None, False)
        if not sources:
            raise ValueError('В настройках macOS нет доступных раскладок клавиатуры')
        try:
            selectable = {}
            layouts = []
            layout_type = self.string(self.constant('kTISTypeKeyboardLayout'))
            for i in range(self.cf.CFArrayGetCount(sources)):
                source = self.cf.CFArrayGetValueAtIndex(sources, i)
                if not all(self.cf.CFBooleanGetValue(self.property(source, prop)) for prop in (
                        'kTISPropertyInputSourceIsEnabled', 'kTISPropertyInputSourceIsSelectCapable')):
                    continue
                sid = self.string(self.property(source, 'kTISPropertyInputSourceID')).casefold()
                selectable[sid] = source
                if self.string(self.property(source, 'kTISPropertyInputSourceType')) == layout_type:
                    layouts.append(sid)
            target = source_id.casefold() if source_id else desired_input_source(layouts, language)
            if target not in selectable:
                raise ValueError('Источник ввода не включён в настройках macOS')
            status = self.tis.TISSelectInputSource(selectable[target])
            if status != 0:
                raise RuntimeError(f'macOS не переключила раскладку клавиатуры (код {status})')
        finally:
            self.cf.CFRelease(sources)
        for attempt in range(6):
            actual = self.current()
            if actual['source_id'].casefold() == target:
                actual['message'] = ('Включена русская раскладка.' if actual['language'] == 'russian'
                                     else 'Включена английская раскладка.' if actual['language'] == 'english'
                                     else 'Источник ввода переключён.')
                return actual
            if attempt < 5:
                time.sleep(.05)
        raise RuntimeError('macOS не подтвердила переключение раскладки клавиатуры')


def worker_main(argv):
    try:
        if argv == ['current']:
            result = InputSources().current()
        elif len(argv) == 2 and argv[0] == 'select' and argv[1] in ('english', 'russian'):
            result = InputSources().select(language=argv[1])
        elif len(argv) == 2 and argv[0] == 'select-id':
            result = InputSources().select(source_id=argv[1])
        else:
            raise ValueError('Неизвестная команда переключения раскладки')
        print(json.dumps({'ok': True, **result}, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(worker_main(sys.argv[1:]))
