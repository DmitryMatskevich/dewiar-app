import json
import tempfile
import unittest
from pathlib import Path

from media_translation import ensure_translation


class MediaTranslationTest(unittest.TestCase):
    def test_translation_artifacts_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'transcript.clean.md').write_text('# Оригинал\nHello world.\n', encoding='utf-8')
            (root / 'segments.json').write_text(json.dumps([[0, 1.2, 'Hello world.']]), encoding='utf-8')

            def generate(prompt):
                if 'JSON-массив' in prompt:
                    return '[{"id": 0, "text": "Привет, мир."}]'
                return 'Привет, мир.'

            result = ensure_translation(root, 'en', 'ru', generate, title='Demo', source='demo')
            self.assertEqual(result['status'], 'prepared')
            self.assertTrue((root / 'translation.md').is_file())
            self.assertTrue((root / 'translation.srt').read_text(encoding='utf-8').startswith('1\n00:00:00,000 --> 00:00:01,200'))
            manifest = json.loads((root / 'openmontage.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['target_language'], 'ru')
            self.assertEqual(manifest['render'], 'not_started')


if __name__ == '__main__':
    unittest.main()
