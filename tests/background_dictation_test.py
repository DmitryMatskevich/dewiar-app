"""Background voice commands and draft persistence, without devices or real app mutation."""
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from background_dictation import parse, insert_at_focus
from engine_audio import AudioMixin

class Engine(AudioMixin):
    def __init__(self):
        self._dictation_lock = threading.RLock()
        self.dictation_active = True
        self._dictation_session = 3
        self._background_dictation = True
        self._background_draft = []
        self.aliases = {'lira':['лира']}
        self.s = SimpleNamespace(get=lambda *args:'lira')
        self.events = []
        self.notes = []
    def emit(self, kind, **kw): self.events.append((kind, kw))
    def note(self, value): self.notes.append(value)
    def _window_cmd(self, arg): return False
    def _voice_control_cmd(self, arg): return False
    def ask(self, arg): self.command = arg; self.command_done.set()

class Background(unittest.TestCase):
    def test_wake_command_starts_background_without_opening_window(self):
        e = Engine()
        e._intro_ear = None
        e._is_self_echo = lambda text: False
        e.pending_confirm = None
        e.tasks = SimpleNamespace(waiting=lambda:None)
        e.dictation_start = lambda **kwargs: e.events.append(('start', kwargs))
        AudioMixin.handle_text(e, 'Лира, слушай', True, False)
        AudioMixin.handle_text(e, 'Лира, записывай', True, False)
        self.assertEqual(e.events, [('start', {'background':True}), ('start', {'background':True})])

    def test_parse_named_commands_and_literal_text(self):
        self.assertEqual(parse('Добрый день. Лира, вставить', ['лира']), ('Добрый день.', ('insert','')))
        self.assertEqual(parse('Лира, добавить', ['лира']), ('', ('insert','')))
        self.assertEqual(parse('Лира, текст', ['лира']), ('', ('insert','')))
        self.assertEqual(parse('Мы обсудили Лира и другие вопросы', ['лира'])[1], None)
        self.assertEqual(parse('Лира', ['лира']), ('', ('show','')))
        self.assertEqual(parse('Лира, снимок экрана', ['лира']), ('', ('command','снимок экрана')))
        self.assertEqual(parse('вставить текст', ['лира']), ('вставить текст', None))

    def test_voice_copy_also_works_in_normal_dictation(self):
        e = Engine()
        e._background_dictation = False
        e._dictation_result('Последняя фраза, Лира, текст', 'test', 3)
        self.assertEqual(e.events[0][1]['text'], 'Последняя фраза')
        self.assertEqual(e.events[1][1]['state'], 'copy_requested')
        self.assertTrue(e.dictation_active)
        e.events.clear()
        e._dictation_result('Лира, скопировать', 'test', 3)
        self.assertEqual(e.events[0][1]['action'], 'copy')

    def test_copy_command_replaces_image_even_without_focused_field(self):
        from background_dictation import copy_and_insert
        board = SimpleNamespace(contents='image')
        board.clearContents = lambda:setattr(board, 'contents', None)
        def put(text, kind): board.contents=text; return True
        board.setString_forType_ = put
        appkit=SimpleNamespace(NSPasteboard=SimpleNamespace(generalPasteboard=lambda:board), NSPasteboardTypeString='text')
        with patch.dict(sys.modules, AppKit=appkit), patch('background_dictation.insert_at_focus', side_effect=RuntimeError('Нет поля')):
            result=copy_and_insert('Текст вместо изображения')
        self.assertEqual(board.contents, 'Текст вместо изображения')
        self.assertEqual(result, {'copied':True,'inserted':False,'reason':'Нет поля'})

    def test_voice_copy_does_not_paste_in_background(self):
        e = Engine()
        e._dictation_result('Тестовый текст', 'test', 3)
        with patch('background_dictation.copy_text') as copy, patch('background_dictation.copy_and_insert') as insert:
            e._dictation_result('Лира, скопировать', 'test', 3)
        copy.assert_called_once_with('Тестовый текст')
        insert.assert_not_called()
        self.assertEqual(e._background_draft, ['Тестовый текст'])

    def test_commands_also_work_after_dictation_stops(self):
        e = Engine()
        e.dictation_active = False
        e._intro_ear = None
        e._is_self_echo = lambda text: False
        e.pending_confirm = None
        e.tasks = SimpleNamespace(waiting=lambda:None)
        e.set_state = lambda state: None
        AudioMixin.handle_text(e, 'Лира, вставить', True, False)
        AudioMixin.handle_text(e, 'Лира, скопировать', True, False)
        AudioMixin.handle_text(e, 'скопировать', True, False, forced='lira')
        self.assertEqual(e.events, [('draft_command', {'action':'insert'}), ('draft_command', {'action':'copy'}), ('draft_command', {'action':'copy'})])
    def test_insert_continues_listening_and_keeps_text_on_error(self):
        e = Engine()
        e._dictation_result('Добрый день.', 'test', 3)
        e._dictation_result('Как дела?', 'test', 3)
        with patch('background_dictation.copy_and_insert', return_value={'copied':True,'inserted':False,'reason':'Нет фокуса'}):
            e._dictation_result('Лира, вставить', 'test', 3)
        self.assertEqual(e._background_draft, ['Добрый день.', 'Как дела?'])
        self.assertIn('скопирован', e.notes[-1])
        with patch('background_dictation.copy_and_insert', return_value={'copied':True,'inserted':True}) as insert:
            e._dictation_result('Лира, добавить', 'test', 3)
            insert.assert_called_once_with('Добрый день. Как дела?')
        self.assertEqual(e._background_draft, [])
        self.assertTrue(e.dictation_active)
        e._dictation_result('Ещё текст', 'test', 3)
        e._dictation_result('Лира', 'test', 3)
        self.assertIn(('window', {'do':'show'}), e.events)
        self.assertEqual(e._background_draft, ['Ещё текст'])
        e._dictation_result('Лира, отмена', 'test', 3)
        self.assertEqual(e._background_draft, [])
    def test_named_command_runs_without_dropping_draft(self):
        e = Engine(); e.command_done = threading.Event()
        e._dictation_result('Черновик.', 'test', 3)
        e._dictation_result('Лира, снимок экрана', 'test', 3)
        self.assertTrue(e.command_done.wait(2))
        self.assertEqual(e.command, 'снимок экрана')
        self.assertEqual(e._background_draft, ['Черновик.'])

    def test_edit_during_new_speech_preserves_both(self):
        e = Engine()
        e._dictation_result('Привет', 'test', 3)
        state = e.background_dictation_state()
        e._dictation_result('как дела', 'test', 3)
        result = e.background_dictation_edit('Здравствуйте', 3, state['revision'], state['text'])
        self.assertEqual(result['text'], 'Здравствуйте как дела')
        with self.assertRaises(ValueError):
            e.background_dictation_edit('не потерять', 3, state['revision'], 'другой текст')
        self.assertEqual(e.background_dictation_state()['text'], 'Здравствуйте как дела')

    def test_insertion_rechecks_focus_and_rejects_secure_fields(self):
        app = SimpleNamespace(bundleIdentifier=lambda: 'org.example.editor', processIdentifier=lambda: 17)
        workspace = SimpleNamespace(frontmostApplication=lambda: app)
        appkit = SimpleNamespace(NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace))
        focus = object()
        values = {'AXFocusedUIElement': focus, 'AXRole': 'AXTextArea', 'AXSubrole': ''}
        writes = []
        ax = SimpleNamespace(AXIsProcessTrusted=lambda: True, AXUIElementCreateSystemWide=lambda: 'system',
            AXUIElementCopyAttributeValue=lambda obj, key, out: (0, values.get(key)),
            AXUIElementIsAttributeSettable=lambda obj, key, out: (0, True),
            AXUIElementSetAttributeValue=lambda obj, key, value: writes.append((obj,key,value)) or 0)
        with patch.dict(sys.modules, ApplicationServices=ax, AppKit=appkit):
            insert_at_focus('Привет')
            self.assertEqual(writes, [(focus, 'AXSelectedText', 'Привет')])
            values['AXSubrole'] = 'AXSecureTextField'
            with self.assertRaisesRegex(RuntimeError, 'защищённое'): insert_at_focus('secret')
            self.assertEqual(len(writes), 1)
            values['AXSubrole'] = ''
            calls = iter([focus, object()])
            ax.AXUIElementCopyAttributeValue = lambda obj, key, out: (0, next(calls) if key == 'AXFocusedUIElement' else values.get(key))
            with self.assertRaisesRegex(RuntimeError, 'Фокус изменился'): insert_at_focus('wrong')
            self.assertEqual(len(writes), 1)

    def test_clipboard_fallback_preserves_newer_clipboard(self):
        from background_dictation import insert_at_focus
        focus = object()
        app = SimpleNamespace(bundleIdentifier=lambda:'test.app', processIdentifier=lambda:7)
        board = SimpleNamespace(revision=0, value='before')
        board.pasteboardItems = lambda: []
        board.changeCount = lambda: board.revision
        def clear(): board.revision += 1; board.value = ''
        def put(value, kind): board.revision += 1; board.value = value; return True
        board.clearContents = clear
        board.setString_forType_ = put
        board.writeObjects_ = lambda old: True
        appkit = SimpleNamespace(NSWorkspace=SimpleNamespace(sharedWorkspace=lambda:SimpleNamespace(frontmostApplication=lambda:app)),
            NSPasteboard=SimpleNamespace(generalPasteboard=lambda:board), NSPasteboardItem=None, NSPasteboardTypeString='text')
        vals = {'AXFocusedUIElement':focus, 'AXRole':'AXTextArea', 'AXSubrole':''}
        ax = SimpleNamespace(AXIsProcessTrusted=lambda:True, AXUIElementCreateSystemWide=lambda:'system',
            AXUIElementCopyAttributeValue=lambda obj,key,out:(0,vals.get(key)),
            AXUIElementIsAttributeSettable=lambda obj,key,out:(0,key == 'AXValue'),
            AXUIElementSetAttributeValue=lambda *args:0)
        events=[]; timers=[]
        quartz = SimpleNamespace(CGEventCreateKeyboardEvent=lambda source,key,down:down,
            CGEventSetFlags=lambda event,flags:None, CGEventPost=lambda target,event:events.append(event),
            kCGEventFlagMaskCommand=1, kCGHIDEventTap=0)
        def timer(delay, fn):
            t=SimpleNamespace(daemon=False, start=lambda:timers.append(fn)); return t
        with patch.dict(sys.modules, ApplicationServices=ax, AppKit=appkit, Quartz=quartz), patch('background_dictation.threading.Timer', timer):
            insert_at_focus('Фраза')
            self.assertEqual(events, [True,False])
            self.assertEqual(board.value, 'Фраза')
            put('Новая копия', 'text')
            timers[0]()
            self.assertEqual(board.value, 'Новая копия')

if __name__ == '__main__': unittest.main()
