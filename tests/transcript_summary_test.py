import tempfile
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))

from transcript_summary import VERSION, ensure_summary


class SummaryTest(unittest.TestCase):
    def test_llm_summary_is_saved_and_reused(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)
            (p / 'transcript.clean.md').write_text('## Текст\n\nИван Петров рассказывает о модернизации ЦОД.\n', encoding='utf-8')
            calls = []
            def generate(prompt):
                calls.append(prompt)
                return '## О чём ролик\n\nО модернизации ЦОД.\n\n## Кто выступает\n\nИван Петров.\n\n## Основные идеи\n\n- Энергоэффективность.\n\n## Заключение и вывод\n\nНужна поэтапная модернизация.'
            first = ensure_summary(p, 'ЦОД', 'https://example.test/video', generate)
            second = ensure_summary(p, 'ЦОД', 'https://example.test/video', lambda _: (_ for _ in ()).throw(AssertionError('не должен вызываться')))
            self.assertEqual(first['version'], VERSION)
            self.assertEqual(second['cached'], True)
            self.assertEqual(len(calls), 1)
            self.assertIn('## Кто выступает', (p / 'transcript.summary.md').read_text(encoding='utf-8'))

    def test_local_fallback_is_honest(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)
            (p / 'transcript.clean.md').write_text('Ролик объясняет, как работает резервирование.', encoding='utf-8')
            out = ensure_summary(p, 'Резервирование')
            self.assertEqual(out['method'], 'local-fallback')
            self.assertIn('нет надёжно выделенного имени', (p / 'transcript.summary.md').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
