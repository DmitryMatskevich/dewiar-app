"""A rerun that merged times by list position is undone only with lexical support."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'core'))
import songs_restore_timing as restore_tool


def p(pid, text, a, b, **extra):
    return dict(phrase_id=pid, text=text, start_ms=a, end_ms=b, words=[], timing_quality='phrase', **extra)


class RestoreTimingTests(unittest.TestCase):
    def card(self):
        before = [p('r-0', 'Жил на лихоборах гоп со смыком', 640, 4560),
                  p('r-1', 'Славился своим огромным приком', 6000, 11000),
                  p('r-2', 'Девки жены и бабули', 14000, 18000)]
        # «Распознавание слов обновлено»: times of other lines, by position.
        after = [dict(before[0]), dict(before[1], start_ms=17590, end_ms=33670, timing_quality='word_partial'),
                 dict(before[2], start_ms=34799, end_ms=54399)]
        whisper = {'model': 'Whisper', 'phrases': [
            {'text': 'Жил на лихоборах гоп со смыком', 'start_ms': 0, 'end_ms': 5000},
            {'text': 'Славился своим огромным париком', 'start_ms': 6000, 'end_ms': 11000},
            {'text': 'Девки жены и бабули', 'start_ms': 14000, 'end_ms': 18000},
            {'text': 'Бегали на флэт к нему', 'start_ms': 18000, 'end_ms': 21000}]}
        gigaam = {'model': 'gigaam', 'phrases': [  # one 17-second block: no place information
            {'text': 'Жил на лихоборах Славился своим огромным приком Девки', 'start_ms': 0, 'end_ms': 17590}]}
        return {'phrases': [dict(x) for x in after], 'selected_run_id': 'r',
                'analysis_runs': [{'run_id': 'r', 'hypotheses': [gigaam, whisper]}],
                'edits': [{'at': '1', 'changes': {'phrases': before}},
                          {'at': '2', 'changes': {'phrases': after, 'stage_label': 'Распознавание слов обновлено; проверьте'}}]}

    def test_second_line_returns_to_its_place(self):
        phrases, changes = restore_tool.restore(self.card())
        self.assertEqual([(x['start_ms'], x['end_ms']) for x in phrases],
                         [(640, 4560), (6000, 11000), (14000, 18000)])
        self.assertEqual([c['phrase_id'] for c in changes], ['r-1', 'r-2'])
        self.assertEqual(phrases[1]['text'], 'Славился своим огромным приком')

    def test_later_deliberate_change_and_unsupported_moves_stay(self):
        card = self.card()
        card['phrases'][1].update(start_ms=60000, end_ms=62000)  # moved again later on purpose
        card['analysis_runs'][0]['hypotheses'][1]['phrases'][2]['text'] = 'совсем другие слова тут'
        phrases, changes = restore_tool.restore(card)
        self.assertEqual((phrases[1]['start_ms'], phrases[2]['start_ms']), (60000, 34799))
        self.assertEqual(changes, [])


if __name__ == '__main__':
    unittest.main()
