"""Named Mac application/window actions. No shell scripts, forced quits, clicks or arbitrary processes."""
import re
import subprocess
import time
from pathlib import Path
from macsys import applications


ALIASES={'сафари':'safari','телеграм':'telegram','телеграмм':'telegram','хром':'google chrome','гугл хром':'google chrome',
         'калькулятор':'calculator','терминал':'terminal','заметки':'notes','календарь':'calendar',
         'текстэдит':'textedit','текстовый редактор':'textedit','файндер':'finder','настройки системы':'system settings',
         'яндекс браузер':'yandex','яндекс':'yandex','вскод':'code','вс код':'code',
         'девиар':'dewiar','оллама':'ollama','страта':'strata','курсор':'cursor','visual studio code':'code'}


class AppNotFound(ValueError):pass


def norm(text):return ' '.join(str(text).casefold().replace('ё','е').strip(' «»".,').split())


def parse(text):
    t=' '.join(text.strip().split()).rstrip('.!')
    if re.search(r'\b(?:сайт|страниц|вкладк|задач|файл|папк|ссылк)\w*\b',t,re.I):return None
    m=re.fullmatch(r'(?:открой|запусти|открыть|запустить)\s+(?:приложение\s+|программу\s+)?(.+)',t,re.I)
    if m:return {'action':'open','application':m[1]}
    m=re.fullmatch(r'(?:переключись\s+на|перейди\s+в|активируй|покажи\s+приложение)\s+(.+)',t,re.I)
    if m:return {'action':'focus','application':m[1]}
    m=re.fullmatch(r'(?:закрой|закрыть|заверши|завершить)\s+(?:приложение\s+|программу\s+)?(.+)',t,re.I)
    if m:return {'action':'quit','application':m[1]}
    m=re.fullmatch(r'(?:перенеси|перемести|перенести|переместить)\s+(?:окно\s+)?(.+?)\s+на\s+(.+?)\s+(?:экран|монитор)',t,re.I)
    if m:return {'action':'move','application':m[1],'screen':m[2]}
    m=re.fullmatch(r'(?:перемести|переведи|перенеси|наведи)\s+(?:курсор|мышку|указатель|курсор мыши)\s+(?:к|на|в)\s+(?:окну\s+|окно\s+|приложение\s+)?(.+)',t,re.I)
    if m:return {'action':'cursor','application':m[1]}
    if re.fullmatch(r'(?:какие|покажи|список)\s+(?:подключены\s+)?(?:экраны|экранах|мониторы|мониторов)',t,re.I):return {'action':'screens'}
    return None


def resolve(query,rows):
    q=norm(query);q=ALIASES.get(q,q)
    exact=[r for r in rows if q in (norm(r['name']),norm(r['id']),norm(Path(r['path']).stem) if r.get('path') else '')]
    candidates=exact or [r for r in rows if len(q)>=3 and re.search(r'(?:^|\s)'+re.escape(q)+r'(?:\s|$)',norm(r['name']))]
    if not candidates:raise AppNotFound('Не найдено приложение «'+str(query)+'»')
    if len(candidates)>1:raise ValueError('Уточните приложение: '+', '.join(r['name'] for r in candidates[:8]))
    return candidates[0]


def permission():
    from ApplicationServices import AXIsProcessTrusted
    return {'granted':bool(AXIsProcessTrusted())}


def running():
    from AppKit import NSWorkspace
    result=[]
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        if app.activationPolicy()!=0 or app.isTerminated():continue
        result.append({'name':str(app.localizedName() or ''),'id':str(app.bundleIdentifier() or ''),
                       'pid':int(app.processIdentifier()),'_app':app})
    return result


def screens():
    from AppKit import NSScreen
    from Quartz import CGMainDisplayID
    raw=list(NSScreen.screens());main_id=int(CGMainDisplayID())
    main=next((s for s in raw if int(s.deviceDescription()['NSScreenNumber'])==main_id),raw[0] if raw else None)
    if not main:return []
    top=main.frame().origin.y+main.frame().size.height
    result=[]
    for s in raw:
        f=s.visibleFrame();full=s.frame();sid=int(s.deviceDescription()['NSScreenNumber'])
        result.append({'id':sid,'name':str(s.localizedName()),'main':sid==main_id,
                       'x':float(f.origin.x),'y':float(top-f.origin.y-f.size.height),'w':float(f.size.width),'h':float(f.size.height),
                       'full':(float(full.origin.x),float(top-full.origin.y-full.size.height),float(full.size.width),float(full.size.height))})
    result.sort(key=lambda s:(not s['main'],s['x'],s['y'],s['id']))
    return [dict(s,number=i+1) for i,s in enumerate(result)]


def intersection(a,b):return max(0,min(a['x']+a['w'],b['x']+b['w'])-max(a['x'],b['x']))*max(0,min(a['y']+a['h'],b['y']+b['h'])-max(a['y'],b['y']))


def choose_screen(value,displays,rect):
    if not displays:raise ValueError('Нет доступных экранов')
    current=max(displays,key=lambda s:intersection(rect,s));q=norm(value or 'следующий')
    nums={'первый':1,'первом':1,'основной':1,'главный':1,'второй':2,'втором':2,'третий':3,'четвертый':4}
    number=int(q) if q.isdecimal() else nums.get(q)
    if number is not None:
        target=next((s for s in displays if s['number']==number),None)
        if not target:raise ValueError(f'Экран {number} не подключён; доступно {len(displays)}')
    elif q in ('другой','следующий','предыдущий'):
        if len(displays)<2:raise ValueError('Подключён только один экран')
        target=displays[(displays.index(current)+(-1 if q=='предыдущий' else 1))%len(displays)]
    elif q in ('левый','правый','верхний','нижний'):
        axis='x' if q in ('левый','правый') else 'y';sign=-1 if q in ('левый','верхний') else 1
        candidates=[s for s in displays if sign*(s[axis]-current[axis])>1]
        if not candidates:raise ValueError('В этом направлении другого экрана нет')
        target=min(candidates,key=lambda s:abs(s[axis]-current[axis]))
    else:raise ValueError('Укажите номер экрана, основной, другой, левый или правый')
    if target['id']!=current['id'] and target.get('full')==current.get('full'):
        raise ValueError('Экраны дублируются; для переноса нужен расширенный рабочий стол')
    return current,target


def fit(rect,source,target):
    w,h=min(rect['w'],target['w']),min(rect['h'],target['h'])
    rx=max(0,min(1,(rect['x']-source['x'])/max(1,source['w']-rect['w'])))
    ry=max(0,min(1,(rect['y']-source['y'])/max(1,source['h']-rect['h'])))
    return {'x':target['x']+rx*(target['w']-w),'y':target['y']+ry*(target['h']-h),'w':w,'h':h}


def _get(element,name):
    from ApplicationServices import AXUIElementCopyAttributeValue
    code,value=AXUIElementCopyAttributeValue(element,name,None)
    return value if code==0 else None


def _set(element,name,value):
    from ApplicationServices import AXUIElementSetAttributeValue
    code=AXUIElementSetAttributeValue(element,name,value)
    if code!=0:raise ValueError('Приложение не разрешило изменение окна ('+str(code)+')')


def _rect(window):
    from ApplicationServices import AXValueGetValue,kAXValueCGPointType,kAXValueCGSizeType
    p=_get(window,'AXPosition');s=_get(window,'AXSize')
    if p is None or s is None:raise ValueError('Не удалось прочитать положение окна')
    ok,pos=AXValueGetValue(p,kAXValueCGPointType,None);ok2,size=AXValueGetValue(s,kAXValueCGSizeType,None)
    if not ok or not ok2:raise ValueError('Недоступны координаты окна')
    return {'x':float(pos.x),'y':float(pos.y),'w':float(size.width),'h':float(size.height)}


# Accessibility actions are the desktop equivalent of Codex's computer-use
# tool: first inspect the visible controls, then address one control by its
# title/description and finally verify the returned result.  Coordinates are
# deliberately not accepted here; a stale coordinate can hit another app.
_UI_LIMIT = 180
_UI_ROLES = {"AXButton", "AXCheckBox", "AXRadioButton", "AXPopUpButton", "AXMenuItem",
             "AXTextField", "AXTextArea", "AXSearchField", "AXDateField", "AXTokenField", "AXComboBox", "AXSlider", "AXLink",
             "AXStaticText", "AXScrollArea", "AXWebArea", "AXGroup"}


def _ui_attrs(element):
    from ApplicationServices import AXUIElementCopyAttributeValue
    result = {}
    for key in ("AXRole", "AXSubrole", "AXTitle", "AXDescription", "AXHelp", "AXPlaceholderValue", "AXEnabled", "AXValue", "AXFocused", "AXSettable"):
        try:
            code, value = AXUIElementCopyAttributeValue(element, key, None)
        except Exception:  # noqa: BLE001 — an inaccessible child must not abort the tree
            continue
        if code != 0 or value is None:
            continue
        if key == "AXValue" and str(result.get("AXSubrole", "")).lower().find("secure") >= 0:
            continue
        if key == "AXValue" and result.get("AXRole") in ("AXTextField", "AXTextArea") and "password" in str(result.get("AXTitle", "")).lower():
            continue
        if isinstance(value, (str, int, float, bool)):
            result[key] = value
    return result


def _ui_children(element):
    from ApplicationServices import AXUIElementCopyAttributeValue
    try:
        code, value = AXUIElementCopyAttributeValue(element, "AXChildren", None)
    except Exception:  # noqa: BLE001
        return []
    return list(value or []) if code == 0 else []


def _ui_tree(element, depth=0, state=None):
    """Build a bounded, serialisable accessibility tree and retain elements for actions."""
    state = state or {"elements": [], "count": 0}
    if state["count"] >= _UI_LIMIT or depth > 8:
        return None
    attrs = _ui_attrs(element)
    role = attrs.get("AXRole", "")
    if role not in _UI_ROLES and not attrs.get("AXTitle") and not attrs.get("AXDescription"):
        # Containers without a label are still traversed, but do not consume a
        # visible node in the result.
        children = [_ui_tree(child, depth + 1, state) for child in _ui_children(element)]
        return {"children": [x for x in children if x]} if any(children) else None
    state["count"] += 1
    index = len(state["elements"])
    state["elements"].append(element)
    node = {"index": index, "role": role}
    labels = {"title": "AXTitle", "description": "AXDescription", "help": "AXHelp", "placeholder": "AXPlaceholderValue",
              "value": "AXValue", "enabled": "AXEnabled", "focused": "AXFocused", "settable": "AXSettable"}
    for out, key in labels.items():
        if key in attrs and attrs[key] not in ("", None):
            value = attrs[key]
            if out == "value" and isinstance(value, str):
                value = value[:240]
            node[out] = value
    children = [_ui_tree(child, depth + 1, state) for child in _ui_children(element)]
    node["children"] = [x for x in children if x]
    return node


def _ui_label(node):
    return " ".join(str(node.get(k, "")) for k in ("title", "description", "help", "placeholder", "value", "role")).strip()


def _find_ui(root, target):
    state = {"elements": [], "count": 0}
    tree = _ui_tree(root, state=state)
    query = norm(target)
    if not query:
        raise ValueError("Укажите название кнопки, поля или другого элемента интерфейса")
    nodes = []
    def visit(node):
        if not node:
            return
        nodes.append(node)
        for child in node.get("children", []):
            visit(child)
    visit(tree)
    exact = [n for n in nodes if query == norm(_ui_label(n)) or query == norm(str(n.get("title", "")))]
    candidates = exact or [n for n in nodes if query in norm(_ui_label(n))]
    if not candidates:
        raise ValueError(f"В окне не найден элемент «{target}». Сначала выполните inspect и используйте точное название.")
    if len(candidates) > 1:
        labels = [(_ui_label(n) or n.get("role", "элемент"))[:80] for n in candidates[:6]]
        raise ValueError("Нашлось несколько элементов: " + "; ".join(labels) + ". Уточните target.")
    return state["elements"][candidates[0]["index"]], candidates[0], tree


def _activate_window(row, element):
    from AppKit import NSApplicationActivateIgnoringOtherApps
    from ApplicationServices import AXUIElementPerformAction
    app = row["_app"]
    app.unhide()
    if not app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps):
        raise ValueError("macOS не приняла переключение приложения")
    if AXUIElementPerformAction(element, "AXRaise") != 0:
        raise ValueError("Не удалось вывести выбранное окно вперёд")


def _window_item(row, args):
    root, items = windows(row)
    for _ in range(15):
        if items:
            break
        time.sleep(0.2)
        root, items = windows(row)
    if not items:
        raise ValueError("У приложения нет доступного обычного окна")
    index = args.get("window")
    if index is not None:
        if isinstance(index, bool) or not isinstance(index, int) or not 1 <= index <= len(items):
            raise ValueError("Укажите существующий номер окна")
        item = items[index - 1]
    else:
        focused = _get(root, "AXFocusedWindow")
        item = next((i for i in items if i["_element"] == focused), None)
        if item is None and len(items) > 1:
            raise ValueError("Несколько окон: укажите номер окна из списка")
        item = item or items[0]
    return root, item


def _key_event(key):
    import Quartz
    raw = str(key or "").strip().lower().replace("+", " ")
    parts = raw.split()
    if not parts:
        raise ValueError("Укажите клавишу: Enter, Tab, Escape, Cmd+A и т. п.")
    name = parts[-1]
    codes = {"return": 36, "enter": 36, "tab": 48, "space": 49, "backspace": 51, "delete": 117,
             "escape": 53, "esc": 53, "left": 123, "right": 124, "down": 125, "up": 126,
             "home": 115, "end": 119, "pageup": 116, "pagedown": 121}
    codes.update(dict(zip("abcdefghijklmnopqrstuvwxyz", (0, 11, 8, 2, 14, 3, 5, 4, 34, 38, 40, 37, 46,
                                                              45, 31, 35, 12, 15, 1, 17, 32, 9, 13, 7, 16, 6))))
    if name not in codes:
        raise ValueError("Неподдерживаемая клавиша. Используйте Enter, Tab, Escape, стрелки, Delete или Cmd/Ctrl + клавишу.")
    flags = 0
    flags |= Quartz.kCGEventFlagMaskCommand if "cmd" in parts or "command" in parts else 0
    flags |= Quartz.kCGEventFlagMaskControl if "ctrl" in parts or "control" in parts else 0
    flags |= Quartz.kCGEventFlagMaskShift if "shift" in parts else 0
    flags |= Quartz.kCGEventFlagMaskAlternate if "alt" in parts or "option" in parts else 0
    code = codes[name]
    down = Quartz.CGEventCreateKeyboardEvent(None, code, True)
    up = Quartz.CGEventCreateKeyboardEvent(None, code, False)
    for event in (down, up):
        Quartz.CGEventSetFlags(event, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    return name


def _type_unicode(text):
    """Type Unicode without putting the user's text on the clipboard."""
    import Quartz
    down = Quartz.CGEventCreateKeyboardEvent(None, 0, True)
    up = Quartz.CGEventCreateKeyboardEvent(None, 0, False)
    Quartz.CGEventKeyboardSetUnicodeString(down, len(text), text)
    Quartz.CGEventKeyboardSetUnicodeString(up, len(text), text)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


def windows(row):
    from ApplicationServices import AXUIElementCreateApplication,AXUIElementSetMessagingTimeout
    root=AXUIElementCreateApplication(row['pid']);AXUIElementSetMessagingTimeout(root,2)
    result=[]
    for element in _get(root,'AXWindows') or []:
        AXUIElementSetMessagingTimeout(element,2)
        if _get(element,'AXSubrole') not in (None,'AXStandardWindow'):continue
        try:rect=_rect(element)
        except ValueError:continue
        result.append({'number':len(result)+1,'title':str(_get(element,'AXTitle') or ''),'rect':rect,'_element':element})
    return root,result


def execute(args):
    action=args.get('action');query=str(args.get('application') or '').strip()
    if action not in ('screens','list','open','quit','focus','move','cursor','windows','inspect','click','type','key','scroll'):
        raise ValueError('Неизвестное действие с приложением')
    if action=='screens':return {'screens':screens()}
    # Opening an application does not require Accessibility or a snapshot of
    # running windows.  In the frozen Mac build AppKit can bridge a localized
    # application name through an unavailable codec (``utf-16be``) while
    # enumerating runningApplications(); that used to make the harmless
    # command «Открой календарь» fail before /usr/bin/open was reached.
    # Resolve the installed bundle from its plist and launch it directly.
    if action=='open':
        row=resolve(query,applications())
        p=subprocess.run(['/usr/bin/open','-a',row['path']],capture_output=True,text=True,timeout=15)
        if p.returncode:raise ValueError('macOS не открыла приложение: '+row['name'])
        return {'message':'Приложение «'+row['name']+'» открыто.'}
    rows=running()
    if action=='list':return {'applications':[{k:v for k,v in row.items() if k!='_app'} for row in rows]}
    row=resolve(query,rows);app=row['_app']
    if action=='quit':
        if row['id'] in ('com.apple.finder','pro.dewiar.macos'):raise ValueError('Это приложение не завершаю этой командой')
        if not app.terminate():raise ValueError('macOS не приняла запрос завершения')
        for _ in range(15):
            if app.isTerminated():return {'message':'Приложение «'+row['name']+'» закрыто.'}
            time.sleep(.1)
        return {'message':'Завершение «'+row['name']+'» запрошено. Если есть несохранённые документы, ответьте на диалог приложения; принудительное закрытие не применяется.'}
    from AppKit import NSApplicationActivateIgnoringOtherApps
    if action=='focus':
        app.unhide()
        if not app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps):raise ValueError('macOS не приняла переключение приложения')
        return {'message':'Переключение на «'+row['name']+'» запрошено.'}
    if action in ('inspect','click','type','key','scroll'):
        if not permission()['granted']:
            raise PermissionError('Разрешите Dewiar: Системные настройки → Конфиденциальность и безопасность → Универсальный доступ. Затем повторите команду.')
        root, item = _window_item(row, args)
        element = item['_element']
        if action == 'inspect':
            state = {'elements': [], 'count': 0}
            tree = _ui_tree(element, state=state)
            return {'application': row['name'], 'window': item['number'], 'title': item['title'],
                    'tree': tree or {}, 'count': state['count'],
                    'next': 'Используйте title/description элемента как target для click или type.'}
        if action == 'key':
            _activate_window(row, element)
            return {'message': f"Клавиша «{_key_event(args.get('key'))}» отправлена в окно «{row['name']}»."}
        target = str(args.get('target') or '').strip()
        target_element, target_node, _ = _find_ui(element, target)
        role = target_node.get('role', '')
        if action == 'click':
            _activate_window(row, element)
            from ApplicationServices import AXUIElementPerformAction
            code = AXUIElementPerformAction(target_element, 'AXPress')
            if code != 0:
                raise ValueError(f"Элемент «{target}» не принимает нажатие (код macOS {code})")
            return {'message': f"Нажат элемент «{target}» в приложении «{row['name']}».", 'role': role}
        if action == 'type':
            if role not in ('AXTextField', 'AXTextArea', 'AXSearchField', 'AXDateField', 'AXTokenField', 'AXComboBox'):
                raise ValueError(f"«{target}» не является полем ввода (роль {role or 'неизвестна'})")
            if target_node.get('settable') is False:
                raise ValueError(f"Поле «{target}» не разрешает ввод")
            text = str(args.get('text') or '')
            if len(text) > 20000:
                raise ValueError('Слишком длинный ввод: максимум 20 000 символов')
            mode = str(args.get('mode') or 'replace').lower()
            if mode not in ('replace', 'append'):
                raise ValueError('mode должен быть replace или append')
            current = _get(target_element, 'AXValue') if mode == 'append' else ''
            value = (str(current or '') + text) if mode == 'append' else text
            _activate_window(row, element)
            method = 'accessibility'
            try:
                _set(target_element, 'AXValue', value)
            except ValueError:
                # Chromium/WebKit sometimes exposes a field as settable but
                # rejects AXValue.  Focus it and type through HID events,
                # without copying the text to the clipboard.
                from ApplicationServices import AXUIElementPerformAction
                if AXUIElementPerformAction(target_element, 'AXPress') != 0:
                    raise
                _type_unicode(text)
                method = 'keyboard'
            return {'message': f"Текст введён в поле «{target}» приложения «{row['name']}».", 'characters': len(text), 'mode': mode, 'method': method}
        if action == 'scroll':
            direction = norm(args.get('direction') or 'down')
            if direction not in ('up', 'down', 'left', 'right'):
                raise ValueError('direction должен быть up, down, left или right')
            _activate_window(row, element)
            from ApplicationServices import AXUIElementPerformAction
            action_name = {'up': 'AXScrollUp', 'down': 'AXScrollDown', 'left': 'AXScrollLeft', 'right': 'AXScrollRight'}[direction]
            code = AXUIElementPerformAction(target_element, action_name)
            if code != 0:
                raise ValueError(f"Элемент «{target}» не поддерживает прокрутку (код macOS {code})")
            return {'message': f"Прокрутка «{direction}» выполнена в «{target}» приложения «{row['name']}»."}
    if action not in ('move','cursor','windows'):raise ValueError('Неизвестное действие с приложением')
    if not permission()['granted']:raise PermissionError('Разрешите Dewiar: Системные настройки → Конфиденциальность и безопасность → Универсальный доступ. Затем повторите команду.')
    root,items=windows(row)
    if action=='windows':return {'windows':[{k:v for k,v in item.items() if k!='_element'} for item in items]}
    if not items:raise ValueError('У приложения нет доступного обычного окна')
    index=args.get('window')
    if index is not None:
        if isinstance(index,bool) or not isinstance(index,int) or not 1<=index<=len(items):raise ValueError('Укажите существующий номер окна')
        item=items[index-1]
    else:
        focused=_get(root,'AXFocusedWindow')
        item=next((i for i in items if i['_element']==focused),None)
        if item is None and len(items)>1:raise ValueError('Несколько окон: укажите номер окна из списка')
        item=item or items[0]
    element=item['_element'];rect=item['rect']
    if _get(element,'AXFullScreen'):raise ValueError('Выйдите из полноэкранного режима приложения и повторите команду')
    if _get(element,'AXMinimized'):raise ValueError('Окно свёрнуто. Сначала разверните его')
    displays=screens()
    if action=='move':
        source,target=choose_screen(args.get('screen','другой'),displays,rect);new=fit(rect,source,target)
        from ApplicationServices import AXValueCreate,kAXValueCGPointType,kAXValueCGSizeType
        from Quartz import CGPointMake,CGSizeMake
        if new['w']!=rect['w'] or new['h']!=rect['h']:
            _set(element,'AXSize',AXValueCreate(kAXValueCGSizeType,CGSizeMake(new['w'],new['h'])))
            actual=_rect(element);new=fit(actual,source,target)
        _set(element,'AXPosition',AXValueCreate(kAXValueCGPointType,CGPointMake(new['x'],new['y'])))
        actual=_rect(element)
        if not (target['x']<=actual['x']+actual['w']/2<=target['x']+target['w'] and target['y']<=actual['y']+actual['h']/2<=target['y']+target['h']):raise ValueError('Приложение не подтвердило перенос на выбранный экран')
        return {'message':f"Окно «{row['name']}» перенесено на экран {target['number']}.",'screen':target['number'],'rect':actual}
    from ApplicationServices import AXUIElementPerformAction
    from Quartz import CGWarpMouseCursorPosition,CGPointMake
    app.unhide()
    if not app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps) or AXUIElementPerformAction(element,'AXRaise')!=0:
        raise ValueError('Не удалось вывести выбранное окно вперёд; курсор не перемещён')
    rect=_rect(element);visible=max(displays,key=lambda s:intersection(rect,s))
    x=max(visible['x']+1,min(visible['x']+visible['w']-1,rect['x']+rect['w']/2))
    y=max(visible['y']+1,min(visible['y']+visible['h']-1,rect['y']+rect['h']/2))
    if not rect['x']<=x<=rect['x']+rect['w'] or not rect['y']<=y<=rect['y']+rect['h']:raise ValueError('Окно вне видимой области экрана')
    if CGWarpMouseCursorPosition(CGPointMake(x,y))!=0:raise ValueError('macOS не переместила курсор')
    return {'message':'Курсор перемещён к окну «'+row['name']+'», без нажатия.','point':{'x':x,'y':y}}
