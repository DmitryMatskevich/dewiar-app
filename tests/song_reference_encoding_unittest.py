"""Poetry-page decoding regression; all HTTP/DNS replaced by local fixtures."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from io import BytesIO

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from songs.sources import _decode_reference_html, check_reference


class ReferenceEncodingTests(unittest.TestCase):
    def test_windows_1251_http_header_preserves_poetry(self):
        original='<title>Стихи</title><p>Дело было ночью, целовались и балдели.</p>'
        text,encoding=_decode_reference_html(original.encode('cp1251'),'text/html; charset=windows-1251')
        self.assertEqual(text,original)
        self.assertEqual(encoding,'windows-1251')

    def test_html5_and_legacy_meta_declarations(self):
        for meta in ['<meta charset="windows-1251">',
                     '<META content="text/html; charset=windows-1251" http-equiv="Content-Type">']:
            original=meta+'<p>Две строки стихотворения</p>'
            self.assertEqual(_decode_reference_html(original.encode('cp1251'))[0],original)

    def test_utf8_and_unknown_declared_encoding_have_safe_fallback(self):
        text='<p>Ёлочка и снег</p>'
        self.assertEqual(_decode_reference_html(text.encode(), 'text/html; charset=unknown')[0],text)
        self.assertEqual(_decode_reference_html(b'\xef\xbb\xbf'+text.encode(), 'text/html; charset=windows-1251')[0],text)
        self.assertEqual(_decode_reference_html(b'\xff','text/html')[1],'utf-8-replacement')

    def test_script_is_not_a_charset_declaration(self):
        source='<script>const charset="windows-1251"</script><p>Привет</p>'
        self.assertEqual(_decode_reference_html(source.encode())[0],source)

    def test_check_reference_exposes_readable_cyrillic_and_encoding(self):
        source='<title>Стих</title><p>Am</p><p>Тихий вечер</p><p>Dm</p><p>Свет в окне</p>'
        response=BytesIO(source.encode('cp1251'))
        response.headers={'Content-Type':'text/html; charset=windows-1251'}
        response.status=200
        response.geturl=lambda:'https://example.org/poem'
        with patch('songs.sources._public_http_url'), patch('songs.sources.build_opener') as opener:
            opener.return_value.open.return_value=response
            result=check_reference('https://example.org/poem')
        self.assertIn('Тихий вечер',result['extracted_text'])
        self.assertEqual(result['text_encoding'],'windows-1251')
        self.assertEqual(result['page_title'],'Стих')
        self.assertTrue(result['chords_extractable'])


if __name__=='__main__':unittest.main()
