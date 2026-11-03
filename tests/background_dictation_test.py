"""Background voice commands and draft persistence, without devices or real app mutation."""
import itertools
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
import background_dictation
from background_dictation import parse, paste_into_focus
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
        self.assertEqual(parse('Давай наберём текст, который будем копировать. Лира, вставить текст.', ['лира']),
                         ('Давай наберём текст, который будем копировать.', ('insert','')))
        self.assertEqual(parse('Лира ставить', ['лира']), ('', ('insert','')))
        self.assertEqual(parse('Лира, скопировать текст', ['лира']), ('', ('copy','')))
        self.assertEqual(parse('Лира, сотри', ['лира']), ('', ('clear','')))
        self.assertEqual(parse('Лира, очисти текст', ['лира']), ('', ('clear','')))
        self.assertEqual(parse('Решение принято. Лира, стоп', ['лира']), ('Решение принято.', ('stop','')))

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
        e.events.clear()
        e._dictation_result('Лира, сотри', 'test', 3)
        self.assertEqual(e.events[0][1]['action'], 'clear')

    def test_named_command_is_not_inserted_into_normal_dictation(self):
        e = Engine()
        e._background_dictation = False
        e.command_done = threading.Event()
        e._dictation_result('Лира, снимок экрана', 'test', 3)
        self.assertTrue(e.command_done.wait(2))
        self.assertEqual(e.command, 'снимок экрана')
        self.assertFalse(any(event.get('state') == 'partial' for _, event in e.events))
        self.assertTrue(e.dictation_active)
        e._dictation_result('Лира, появись', 'test', 3)
        self.assertIn(('window', {'do':'show'}), e.events)

    def test_named_stop_preserves_recognised_text(self):
        e = Engine()
        e._background_dictation = False
        e.set_state = lambda state: None
        e._dictation_result('Последняя фраза. Лира, стоп', 'test', 3)
        self.assertFalse(e.dictation_active)
        self.assertEqual(e.events[0][1]['text'], 'Последняя фраза.')
        self.assertEqual(e.events[-1][1]['state'], 'cancelled')
        e._dictation_result('Опоздавший результат', 'test', 3)
        self.assertEqual(len(e.events), 2)

    def test_named_stop_preserves_background_draft(self):
        e = Engine()
        e.set_state = lambda state: None
        e._dictation_result('Важный черновик. Лира, стоп', 'test', 3)
        self.assertFalse(e.dictation_active)
        self.assertEqual(e._background_draft, ['Важный черновик.'])
        self.assertEqual(e.events[-1][1]['state'], 'stopped')
        self.assertEqual(e.events[-1][1]['text'], 'Важный черновик.')

    def test_copy_command_replaces_image_even_without_focused_field(self):
        from background_dictation import copy_and_insert
        board = SimpleNamespace(contents='image')
        board.clearContents = lambda:setattr(board, 'contents', None)
        def put(text, kind): board.contents=text; return True
        board.setString_forType_ = put
        appkit=SimpleNamespace(NSPasteboard=SimpleNamespace(generalPasteboard=lambda:board), NSPasteboardTypeString='text')
        with patch.dict(sys.modules, AppKit=appkit), patch('background_dictation.paste_into_focus', side_effect=RuntimeError('Нет поля')):
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
        AudioMixin.handle_text(e, 'Лира, очисти', True, False)
        self.assertEqual(e.events, [('draft_command', {'action':'insert'}), ('draft_command', {'action':'copy'}), ('draft_command', {'action':'copy'}), ('draft_command', {'action':'clear'})])
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

    def test_paste_runs_outside_lock_and_keeps_new_words(self):
        e = Engine()
        e._dictation_result('Добрый день.', 'test', 3)
        def paste(text):
            free = []
            t = threading.Thread(target=lambda: free.append(e._dictation_lock.acquire(timeout=.5) and (e._dictation_lock.release() or True)))
            t.start(); t.join()
            self.assertEqual(free, [True])      # другой поток может работать с диктовкой во время ⌘V
            e._background_draft.append('Новое.')   # правка из окна во время вставки
            return {'copied':True, 'inserted':True, 'app':'Заметки'}
        with patch('background_dictation.copy_and_insert', side_effect=paste):
            e._dictation_result('Лира, вставить', 'test', 3)
        self.assertEqual(e._background_draft, ['Новое.'])
        self.assertEqual(e.events[-1][1]['state'], 'inserted')
        self.assertEqual(e.events[-1][1]['app'], 'Заметки')

    def test_paste_does_not_remove_concurrent_edit_or_new_session(self):
        for new_session in (False, True):
            e = Engine()
            e._dictation_result('Старый текст', 'test', 3)
            def paste(text):
                if new_session:
                    e._dictation_session += 1
                    e._background_draft = ['Старый текст']
                else:
                    e._background_draft = ['Исправленный текст']
                return {'copied': True, 'inserted': True}
            with patch('background_dictation.copy_and_insert', side_effect=paste):
                e._dictation_result('Лира, вставить', 'test', 3)
            self.assertEqual(e._background_draft, ['Старый текст' if new_session else 'Исправленный текст'])

    def mac(self, focused, windows, trusted=True, role='AXTextArea', subrole='', switches=True, chars=None, accepts=True):
        """Fake ApplicationServices and Quartz: AX knows the active pid, the window list goes front to back.
        chars — the field shows its length to accessibility; accepts — the app reacts to ⌘V."""
        state = SimpleNamespace(focused=focused, raised=[], prompts=0, chars=chars)
        def copy(obj, key, out):
            if key == 'AXFocusedApplication':return (0, ('app', state.focused)) if state.focused else (-25212, None)
            if key == 'AXNumberOfCharacters':return (0, state.chars) if state.chars is not None else (-25205, None)
            return (0, {'AXFocusedUIElement':'field', 'AXRole':role, 'AXSubrole':subrole}.get(key))
        def set_value(obj, key, value):
            state.raised.append((obj, key, value))
            if switches and key == 'AXFrontmost':state.focused = obj[1]
            return 0
        def prompt(options):state.prompts += 1; return False
        ax = SimpleNamespace(AXIsProcessTrusted=lambda:trusted, AXIsProcessTrustedWithOptions=prompt,
            AXUIElementCreateSystemWide=lambda:'system', AXUIElementCreateApplication=lambda pid:('app', pid),
            AXUIElementCopyAttributeValue=copy, AXUIElementGetPid=lambda element, out:(0, element[1]),
            AXUIElementSetAttributeValue=set_value)
        keys = []
        def post(event):
            keys.append(event)
            if accepts and state.chars is not None and event[:2] == (9, True):state.chars += 5
        quartz = SimpleNamespace(CGEventCreateKeyboardEvent=lambda source, key, down:[key, down],
            CGEventSetFlags=lambda event, flags:event.append(flags), CGEventPost=lambda tap, event:post(tuple(event)),
            kCGEventFlagMaskCommand=8, kCGHIDEventTap=0, kCGWindowListOptionOnScreenOnly=1,
            kCGWindowListExcludeDesktopElements=16, kCGNullWindowID=0,
            CGWindowListCopyWindowInfo=lambda options, window:[{'kCGWindowLayer':layer, 'kCGWindowOwnerPID':pid,
                'kCGWindowOwnerName':name, 'kCGWindowBounds':{'Width':800, 'Height':600}} for pid, name, layer in windows])
        clock = SimpleNamespace(monotonic=itertools.count(0, .5).__next__, sleep=lambda seconds:None)
        background_dictation._prompted = False
        ctx = [patch.dict(sys.modules, ApplicationServices=ax, Quartz=quartz, AppKit=SimpleNamespace()),
               patch.object(background_dictation, 'time', clock),
               patch.object(background_dictation, '_dewiar_pids', lambda:{100}),
               patch.object(background_dictation, '_secure_input_on', lambda:False)]
        for c in ctx:c.start(); self.addCleanup(c.stop)
        return state, keys

    PASTE = [(55, True, 8), (9, True, 8), (9, False, 8), (55, False, 0)]

    def test_paste_goes_to_the_active_application(self):
        state, keys = self.mac(42, [(100, 'Dewiar', 3), (42, 'Заметки', 0)])
        self.assertEqual(paste_into_focus(), 'Заметки')
        self.assertEqual(keys, self.PASTE)
        self.assertEqual(state.raised, [])

    def test_dewiar_active_returns_to_the_application_behind_it(self):
        state, keys = self.mac(100, [(100, 'Dewiar', 0), (1, 'Window Server', 0), (77, 'Telegram', 0), (42, 'Заметки', 0)])
        self.assertEqual(paste_into_focus(), 'Telegram')
        self.assertEqual(state.raised, [(('app', 77), 'AXFrontmost', True)])
        self.assertEqual(keys, self.PASTE)

    def test_no_keys_when_switch_fails_or_field_is_secret(self):
        state, keys = self.mac(100, [(100, 'Dewiar', 0), (77, 'Telegram', 0)], switches=False)
        with self.assertRaisesRegex(RuntimeError, 'перейти в «Telegram»'):paste_into_focus()
        state2, keys2 = self.mac(77, [(77, 'Safari', 0)], subrole='AXSecureTextField')
        with self.assertRaisesRegex(RuntimeError, 'пароля'):paste_into_focus()
        self.assertEqual(keys + keys2, [])
        state3, keys3 = self.mac(0, [(100, 'Dewiar', 0)])
        with self.assertRaisesRegex(RuntimeError, 'куда вставить'):paste_into_focus()
        self.assertEqual(keys3, [])

    def test_missing_accessibility_is_explained_and_prompted_once(self):
        state, keys = self.mac(42, [(42, 'Заметки', 0)], trusted=False)
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, 'Универсальный доступ'):paste_into_focus()
        self.assertEqual(state.prompts, 1)
        self.assertEqual(keys, [])

    def test_chrome_and_electron_fields_are_pasted_without_accessibility_details(self):
        # Chrome, Codex, Claude, VS Code, Telegram Desktop do not expose the focused field.
        state, keys = self.mac(42, [(42, 'Google Chrome', 0)])
        import ApplicationServices as ax
        original = ax.AXUIElementCopyAttributeValue
        ax.AXUIElementCopyAttributeValue = lambda obj, key, out: (-25212, None) if key == 'AXFocusedUIElement' else original(obj, key, out)
        self.assertEqual(paste_into_focus(), 'Google Chrome')
        self.assertEqual(keys, self.PASTE)
        keys.clear()
        with patch.object(background_dictation, '_secure_input_on', lambda:True):
            with self.assertRaisesRegex(RuntimeError, 'защищённый ввод'):paste_into_focus()
        self.assertEqual(keys, [])

    def test_unknown_active_application_uses_the_topmost_window(self):
        state, keys = self.mac(0, [(77, 'Telegram', 0), (100, 'Dewiar', 0)])
        self.assertEqual(paste_into_focus(), 'Telegram')
        self.assertEqual(state.raised, [])
        self.assertEqual(keys, self.PASTE)

    def test_visible_field_confirms_or_reports_the_paste(self):
        state, keys = self.mac(42, [(42, 'Заметки', 0)], chars=10)
        self.assertEqual(paste_into_focus(), 'Заметки')
        self.assertEqual(state.chars, 15)
        state2, keys2 = self.mac(42, [(42, 'Заметки', 0)], chars=10, accepts=False)
        with self.assertRaisesRegex(RuntimeError, 'не появился.*перезапущенному'):paste_into_focus()
        self.assertEqual(keys2, self.PASTE)

    def test_posting_permission_is_checked_like_accessibility(self):
        state, keys = self.mac(42, [(42, 'Заметки', 0)])
        import Quartz
        requests = []
        Quartz.CGPreflightPostEventAccess = lambda:False
        Quartz.CGRequestPostEventAccess = lambda:requests.append(1)
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, 'Универсальный доступ'):paste_into_focus()
        self.assertEqual((requests, keys), ([1], []))

    def test_copy_and_insert_leaves_text_on_clipboard(self):
        from background_dictation import copy_and_insert
        board = SimpleNamespace(contents='старое')
        board.clearContents = lambda:setattr(board, 'contents', None)
        def put(text, kind):board.contents = text; return True
        board.setString_forType_ = put
        appkit = SimpleNamespace(NSPasteboard=SimpleNamespace(generalPasteboard=lambda:board), NSPasteboardTypeString='text')
        with patch.dict(sys.modules, AppKit=appkit), patch('background_dictation.paste_into_focus', return_value='TextEdit'):
            self.assertEqual(copy_and_insert('Фраза'), {'copied':True, 'inserted':True, 'app':'TextEdit'})
        self.assertEqual(board.contents, 'Фраза')

if __name__ == '__main__': unittest.main()
