"""Local macOS PDF editing. Previews and output use the same PDFKit operations."""
import base64
import hashlib
import math
import os
import re
import tempfile
from io import BytesIO

LIMIT = 64 * 1024 * 1024


def _load(path):
    import Quartz as q
    from Foundation import NSData
    if path.suffix.lower() != '.pdf' or not path.is_file():
        raise ValueError('Выберите существующий PDF.')
    with path.open('rb') as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError('Встроенный редактор поддерживает PDF до 64 МиБ. Откройте файл в Просмотре.')
    doc = q.PDFDocument.alloc().initWithData_(NSData.dataWithBytes_length_(raw, len(raw)))
    if doc is None or doc.isLocked() or doc.isEncrypted() or not doc.pageCount():
        raise ValueError('PDF не открылся или защищён. Откройте его в Просмотре.')
    if doc.pageCount() > 500:
        raise ValueError('Встроенный редактор поддерживает до 500 страниц.')
    return doc, raw, hashlib.sha256(raw).hexdigest()


def _thumbnail(page):
    import Quartz as q
    from AppKit import NSBitmapImageRep, NSBitmapImageFileTypePNG
    image = page.thumbnailOfSize_forBox_((840, 1120), q.kPDFDisplayBoxCropBox)
    bitmap = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
    data = bitmap.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
    return 'data:image/png;base64,' + base64.b64encode(bytes(data)).decode('ascii')


def _mark(page, note):
    import Quartz as q
    from AppKit import NSColor, NSFont
    if not isinstance(note, dict):
        raise ValueError('Неверная текстовая пометка.')
    x, y = note.get('x'), note.get('y')
    size, color, text = note.get('size'), note.get('color'), note.get('text')
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in (x, y)):
        raise ValueError('Точка пометки должна находиться на странице.')
    if type(size) is not int or not 8 <= size <= 72 or not isinstance(color, str) or not re.fullmatch('#[0-9a-fA-F]{6}', color):
        raise ValueError('Укажите размер 8–72 пт и цвет RGB.')
    if not isinstance(text, str) or not text.strip() or len(text) > 1000:
        raise ValueError('Введите текст пометки (до 1000 символов).')
    box = page.boundsForBox_(q.kPDFDisplayBoxCropBox)
    # The visual origin is upper-left; PDF user space starts at lower-left.
    px, py = {0:(x, 1-y),90:(y, x),180:(1-x,y),270:(1-y,1-x)}[page.rotation() % 360]
    w, h = box.size.width, box.size.height
    width = min(w, max(100, min(360, (len(text)+3)*size*.65)))
    rows = sum(max(1,math.ceil(len(line)*size*.65/max(width-12,1))) for line in text.splitlines())
    height = min(h, max(size+14, rows*size*1.35+12))
    bounds = q.CGRectMake(box.origin.x+min(px*w,w-width), box.origin.y+max(0,min(py*h-height,h-height)), width, height)
    ann = q.PDFAnnotation.alloc().initWithBounds_forType_withProperties_(bounds,q.PDFAnnotationSubtypeFreeText,None)
    ann.setContents_(text)
    ann.setFont_(NSFont.systemFontOfSize_(size))
    r,g,b = (int(color[i:i+2],16)/255 for i in (1,3,5))
    ann.setFontColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(r,g,b,1))
    ann.setColor_(NSColor.clearColor())
    page.addAnnotation_(ann)


def _operations(doc, a):
    total = doc.pageCount()
    order, rotations, notes = a.get('order'), a.get('rotations', {}), a.get('notes', {})
    if not isinstance(order,list) or not 0 < len(order) <= total or any(type(i) is not int or not 0 <= i < total for i in order) or len(set(order)) != len(order):
        raise ValueError('Укажите неповторяющиеся страницы; хотя бы одна должна остаться.')
    if not isinstance(rotations,dict) or not isinstance(notes,dict) or not (rotations.keys() | notes.keys()) <= {str(i) for i in order}:
        raise ValueError('Правки относятся к неизвестной странице.')
    if any(type(r) is not int or r not in (0,90,180,270) for r in rotations.values()):
        raise ValueError('Неверный угол поворота.')
    if any(not isinstance(v,list) for v in notes.values()) or sum(map(len,notes.values())) > 200:
        raise ValueError('Допускается до 200 новых пометок.')
    return order, rotations, notes


def _signature_present(raw):
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(raw))
    if reader.trailer['/Root'].get('/Perms'):
        return True
    return any(f.get('/FT') == '/Sig' and f.get('/V') for f in (reader.get_fields() or {}).values())


def _edited_copy(path, data):
    # Link a complete temporary file without overwriting an existing copy.
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.dewiar-pdf-', delete=False) as stream:
        tmp = stream.name
    try:
        with open(tmp,'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        for n in range(1,10000):
            tail = '' if n == 1 else f' ({n})'
            out = path.with_name(f'{path.stem} — правки{tail}.pdf')
            try:
                os.link(tmp,out)
                return out
            except FileExistsError:
                pass
        raise ValueError('Слишком много копий этого документа.')
    finally:
        os.unlink(tmp)


def command(workspace, cmd, a):
    import sys
    if sys.platform != 'darwin':
        raise ValueError('Этот PDF-редактор использует системный PDFKit на Mac.')
    path = workspace.safe(a.get('path'))
    doc, raw, digest = _load(path)
    if cmd == 'pdf_editor_info':
        return {'name':path.name,'pages':doc.pageCount(),'revision':digest,'signed':_signature_present(raw)}
    if a.get('revision') != digest:
        raise ValueError('Исходный PDF изменился. Откройте его заново.')
    order, rotations, notes = _operations(doc,a)

    def prepare(index):
        page = doc.pageAtIndex_(index)
        page.setRotation_((page.rotation()+rotations.get(str(index),0))%360)
        for note in notes.get(str(index),[]):
            _mark(page,note)
        return page

    if cmd == 'pdf_editor_page':
        i=a.get('page')
        if type(i) is not int or i not in order:
            raise ValueError('Эта страница отсутствует.')
        return {'image':_thumbnail(prepare(i))}
    if cmd != 'pdf_editor_save':
        raise ValueError('Неизвестная команда PDF.')
    if _signature_present(raw):
        raise ValueError('В PDF есть цифровая подпись. Сохранение правок отключено, чтобы не нарушить её проверку.')
    for i in order:
        prepare(i)
    current = list(range(doc.pageCount()))
    for i in range(len(current)-1,-1,-1):
        if current[i] not in order:
            doc.removePageAtIndex_(i)
            current.pop(i)
    for i, wanted in enumerate(order):
        if current[i] != wanted:
            j=current.index(wanted)
            doc.exchangePageAtIndex_withPageAtIndex_(i,j)
            current[i],current[j]=current[j],current[i]
    data=doc.dataRepresentation()
    if not data:
        raise ValueError('PDFKit не сохранил документ.')
    out=_edited_copy(path,bytes(data))
    return {'name':out.name,'path':str(out.relative_to(workspace.ensure().resolve()))}
