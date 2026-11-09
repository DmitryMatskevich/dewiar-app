"""Checks derived from defects found in the nine real pilot recordings."""
import copy
import re
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'core'))
from songs.evidence import phrase_warnings, compare_reference, review_evidence
from songs.public import public_song


class EvidenceTests(unittest.TestCase):
    def test_quarantined_credit_remains_visible_in_review_evidence(self):
        credit={'text':'Субтитры добавил Петров','start_ms':180000,'end_ms':195000}
        row={'phrases':[{'phrase_id':'last','text':'Последняя строка','start_ms':150000,'end_ms':160000}],
             'selected_run_id':'r','analysis_runs':[{'run_id':'r','comparison':{
                 'quarantined_phrases':[{'phrase':credit,'label':'Возможная служебная вставка'}]}}]}
        before=copy.deepcopy(row)
        issue=next(i for i in review_evidence(row)['issues'] if i['code']=='possible_asr_credit')
        self.assertIn(credit['text'],issue['label'])
        self.assertEqual(issue['start_ms'],180000)
        self.assertEqual(row,before)

    def test_display_cues_keep_aligned_times_and_estimate_only_missing_words(self):
        from songs.playback_layout import display_words
        p={'phrase_id':'p','text':'Мы тихо идём домой','start_ms':0,'end_ms':8000,
           'words':[{'char_start':3,'char_end':7,'start_ms':1000,'end_ms':2500}]}
        before=copy.deepcopy(p)
        cues=display_words(p)
        self.assertEqual(cues[1]['start_ms'],1000)
        self.assertEqual(cues[1]['end_ms'],2500)
        self.assertEqual(cues[1]['quality'],'aligned')
        self.assertEqual(cues[0]['quality'],'estimated_phrase')
        self.assertEqual(cues[-1]['end_ms'],8000)
        self.assertTrue(all(a['end_ms']<=b['start_ms'] for a,b in zip(cues,cues[1:])))
        self.assertEqual(p,before)
        p['alignment_stale']=True
        self.assertTrue(all(c['quality']=='estimated_phrase' for c in display_words(p)))

    def test_phrase_display_breaks_keep_cues_at_the_original_word(self):
        from songs.chord_layout import build_guitar_sheet
        from songs.sheet_lines import readable_sheet
        p={'phrase_id':'p','text':'Тихим вечером мы возвращались домой Сердце радовалось встрече.',
           'start_ms':0,'end_ms':12000}
        lines=readable_sheet(build_guitar_sheet([p],[]))
        self.assertTrue(any(l['text'].startswith('Сердце') for l in lines))
        self.assertTrue(all(len(l['text'])<=42 for l in lines))
        words=[l['text'][w['char_start']:w['char_end']] for l in lines for w in l['display_words']]
        self.assertEqual(words,p['text'].split())
        cues=[w for line in lines for w in line['display_words']]
        self.assertTrue(all(w['phrase_id']=='p' for w in cues))
        self.assertEqual([w['phrase_char_start'] for w in cues],
                         [m.start() for m in re.finditer(r'\S+',p['text'])])

    def test_public_sheet_keeps_source_phrase_id_for_inline_editing_wrapped_lines(self):
        phrase={'phrase_id':'p','text':'Тихим вечером мы возвращались домой Сердце радовалось встрече.',
                'start_ms':0,'end_ms':12000}
        row=public_song({'song_id':'inline-edit','revision':1,'title':'Тест','phrases':[phrase],
                         'sections':[],'chords':[],'audio':{'duration_ms':12000}})
        lines=[line for line in row['guitar_sheet'] if line.get('text')]
        self.assertGreater(len(lines),1)
        self.assertTrue(all(line['source_phrase_ids']==['p'] for line in lines))

    def test_missing_opening_uses_two_independent_models(self):
        from songs.consensus import choose_spine, phrases_from_words
        baseline = {'model':'gigaam', 'phrases':[{'text':'one two three four five six seven eight nine ten eleven', 'start_ms':0,'end_ms':20000}]}
        timed = {'model':'Whisper-test','phrases':[{'text':'one','start_ms':0,'end_ms':1000}]}
        third = dict(baseline,model='qwen')
        self.assertIs(choose_spine([baseline,timed,third]),baseline)
        self.assertIs(choose_spine([baseline,timed]),timed)
        phrases=phrases_from_words([{'text':'One two', 'start':0,'end':3, 'words':[{'word':'two','start':2,'end':3}]}],'test')
        self.assertEqual(phrases[0]['text'],'One two')
        self.assertEqual(phrases[0]['words'],[])

    def test_local_omission_recovery_preserves_existing_text_and_requires_peer(self):
        from songs.consensus import recover_omissions
        base = [{'phrase_id':'a','text':'Начало фразы', 'start_ms':0,'end_ms':2000},
                {'phrase_id':'b','text':'Конец фразы', 'start_ms':8000,'end_ms':10000}]
        other = {'model':'gigaam','phrases':[{'phrase_id':'g','text':'Начало фразы три потерянных важных слова Конец фразы', 'start_ms':0,'end_ms':10000}]}
        peer = dict(other,model='qwen')
        before = copy.deepcopy(base)
        self.assertEqual(recover_omissions(base,[other]),base)
        restored = recover_omissions(base,[other,peer])
        self.assertEqual([p['text'] for p in restored],['Начало фразы','три потерянных важных слова','Конец фразы'])
        self.assertEqual(restored[1]['words'],[])
        self.assertEqual(base,before)
        self.assertEqual(recover_omissions(restored,[other,peer]),restored)
        distant = {'model':'qwen','phrases':[dict(other['phrases'][0],start_ms=60000,end_ms=70000)]}
        self.assertEqual(recover_omissions(base,[other,distant]),base)

    def test_zero_word_times_do_not_erase_any_segment_text(self):
        from songs.consensus import phrases_from_words
        rows = phrases_from_words([{'text':'Полная первая строка', 'start':0,'end':8,
            'words':[{'word':'строка','start':1,'end':1}]},
            {'text':'Полная последняя строка', 'start':8,'end':15}], 'preserve')
        self.assertEqual([p['text'] for p in rows],['Полная первая строка','Полная последняя строка'])

    def test_chord_continues_above_next_lyric_line_but_not_after_silence(self):
        from songs.chord_layout import build_guitar_sheet
        phrases=[{'text':'Opening', 'start_ms':5000,'end_ms':8000}]
        events=[{'name':'Am','start_ms':0,'end_ms':9000}]
        self.assertEqual(build_guitar_sheet(phrases,events)[-1]['cells'][0]['chords'],['Am'])
        events.append({'name':'N','start_ms':4000,'end_ms':9000})
        self.assertEqual(build_guitar_sheet(phrases,events)[-1]['cells'][0]['chords'],[])

    def test_display_lines_join_shards_without_losing_text_or_chords(self):
        from songs.chord_layout import build_guitar_sheet
        from songs.sheet_lines import readable_sheet
        phrases=[{'phrase_id':'a','text':'Тихий вечер на дворе. Не','start_ms':0,'end_ms':5000},
                 {'phrase_id':'b','text':'Погасли окна вдалеке.','start_ms':5000,'end_ms':9000}]
        original=copy.deepcopy(phrases)
        events=[{'name':'Am','start_ms':1000},{'name':'E7','start_ms':5500}]
        lines=readable_sheet(build_guitar_sheet(phrases,events))
        self.assertEqual([l['text'] for l in lines],['Тихий вечер на дворе.','Не Погасли окна вдалеке.'])
        self.assertEqual(phrases,original)
        for event in events:
            self.assertTrue(any(m['start_ms']==event['start_ms'] and m['name']==event['name'] for l in lines for m in l['chords']))
        self.assertTrue(all(not l['unplaced_chords'] for l in lines))
        self.assertTrue(all(''.join(c['text'] for c in l['cells'])==l['text'] for l in lines))

    def test_complete_sheet_does_not_repeat_carry_in_across_lines_or_sections(self):
        from songs.chord_layout import build_guitar_sheet
        from songs.sheet_lines import readable_sheet
        phrases=[{'phrase_id':'a','text':'Первая строка. Вторая строка.', 'start_ms':1000,'end_ms':6000},
                 {'phrase_id':'b','text':'Третья строка.', 'start_ms':6000,'end_ms':9000}]
        events=[{'name':'F#m','start_ms':0,'end_ms':4500},
                {'name':'Ab','start_ms':4500,'end_ms':10000}]
        original=copy.deepcopy(events)
        lines=readable_sheet(build_guitar_sheet(phrases,events),section_starts=['b'])
        displayed=[c for line in lines for cell in line['cells'] for c in cell['chords']]
        displayed += [c['name'] for line in lines for c in line['unplaced_chords']]
        self.assertEqual(sorted(displayed),['Ab','F#m'])
        self.assertEqual([l['text'] for l in lines if l['text']],
                         ['Первая строка.','Вторая строка.','Третья строка.'])
        self.assertEqual(events,original)

    def test_complete_sheet_keeps_return_to_chord_and_after_silence(self):
        from songs.chord_layout import build_guitar_sheet
        from songs.sheet_lines import readable_sheet
        phrases=[{'phrase_id':str(i),'text':'Строка.', 'start_ms':i*2000,'end_ms':(i+1)*2000}
                 for i in range(4)]
        events=[{'name':'Am','start_ms':0},{'name':'E7','start_ms':2000},
                {'name':'Am','start_ms':4000},{'name':'N','start_ms':5500},
                {'name':'Am','start_ms':6000}]
        lines=readable_sheet(build_guitar_sheet(phrases,events))
        self.assertEqual([c for line in lines for cell in line['cells'] for c in cell['chords']],
                         ['Am','E7','Am','Am'])

    def test_display_split_retains_manual_character_anchor(self):
        from songs.chord_layout import build_guitar_sheet
        from songs.sheet_lines import readable_sheet
        # Lines follow the verse (09.11.2026): a one-word sentence stays on its
        # line; the manual anchor keeps its letter on the second verse line.
        text='Первая строка у нас длинная. Любовь приходит внезапно'
        p={'phrase_id':'p','text':text,'start_ms':0,'end_ms':8000}
        event={'name':'E7','start_ms':6000,'anchor':{'phrase_id':'p','kind':'manual','grapheme_index':text.index('Любовь')+3}}
        lines=readable_sheet(build_guitar_sheet([p],[event]))
        self.assertTrue(lines[1]['text'].startswith('Любовь'))
        mark=next(m for m in lines[1]['chords'] if m['anchor_quality']=='manual')
        self.assertEqual(mark['char_index'],3)

    def test_width_wrap_does_not_leave_one_word_tail_or_move_chord_off_word(self):
        from songs.chord_layout import build_guitar_sheet
        from songs.sheet_lines import readable_sheet
        text='Мы долго смотрели на звёзды, потом решили вернуться домой.'
        p={'phrase_id':'p','text':text,'start_ms':0,'end_ms':10000}
        event={'name':'Am','start_ms':8000,'anchor':{'phrase_id':'p','kind':'manual','grapheme_index':text.index('домой')}}
        lines=readable_sheet(build_guitar_sheet([p],[event]),max_chars=50)
        self.assertEqual(' '.join(l['text'] for l in lines),text)
        self.assertTrue(all(len(l['text'].split())>=3 for l in lines))
        mark=next((l,m) for l in lines for m in l['chords'])
        self.assertEqual(mark[0]['text'][mark[1]['char_index']:],'домой.')

    def test_short_answer_and_explicit_newline_are_not_rebalanced(self):
        from songs.chord_layout import build_guitar_sheet
        from songs.sheet_lines import readable_sheet
        p={'phrase_id':'p','text':'Ты меня слышишь? Да.\nДо встречи.','start_ms':0,'end_ms':10000}
        # An explicit line break is kept; a short answer stays in its verse line.
        self.assertEqual([l['text'] for l in readable_sheet(build_guitar_sheet([p],[]))],
                         ['Ты меня слышишь? Да.','До встречи.'])

    def test_decoder_loop_is_flagged_without_rewriting(self):
        phrase = {'text':'Пара'*110+' Пока!', 'start_ms':201820, 'end_ms':204000}
        before = copy.deepcopy(phrase)
        self.assertIn('repetition_loop', [w['code'] for w in phrase_warnings(phrase)])
        self.assertEqual(phrase, before)
        self.assertEqual(phrase_warnings({'text':'Ла-ла-ла, о-о-о', 'start_ms':0, 'end_ms':5000}), [])

    def test_missing_line_has_estimated_anchor_not_fake_word_time(self):
        rows = [{'phrase_id':'a', 'text':'Свет погас', 'start_ms':1000},
                {'phrase_id':'b', 'text':'Настало утро', 'start_ms':5000}]
        ref = {'plain_lyrics':'Свет погас. Тихая ночь. Настало утро', 'url':'https://example.org'}
        result = compare_reference(rows, ref)
        gap = result['differences'][0]
        self.assertEqual(gap['kind'], 'insert')
        self.assertTrue(gap['anchor_estimated'])
        self.assertEqual(gap['phrase_id'], 'b')
        self.assertFalse(result['version_verified'])

    def test_repeats_are_differences_not_deleted_or_counted_as_errors(self):
        rows = [{'text':'Свет погас. Свет погас.', 'start_ms':0}]
        before = copy.deepcopy(rows)
        result = compare_reference(rows, {'plain_lyrics':'Свет погас'})
        self.assertEqual(result['differences'][0]['kind'], 'delete')
        self.assertEqual(rows, before)
        self.assertNotIn('wer', result)
        self.assertEqual(compare_reference(rows, {'plain_lyrics':''}), None)

    def test_current_edits_drive_evidence_and_list_stays_compact(self):
        row = {'song_id':'id', 'revision':1, 'phrases':[{'text':'Редактор', 'phrase_id':'p'}],
               'source_candidates':[{'kind':'lyrics', 'plain_lyrics':'Свет погас'}]}
        self.assertTrue(public_song(row)['review_evidence']['issues'])
        self.assertNotIn('review_evidence', public_song(row, summary=True))
        row['phrases'][0]['text'] = 'Свет погас'
        self.assertFalse(review_evidence(row)['issues'])
        self.assertFalse(review_evidence(row)['references'][0]['differences'])


if __name__ == '__main__': unittest.main()
