"""Contract checks for the full song pass, independent of weights/network."""
import copy
import base64
import io
import wave
from pathlib import Path
import sys
import tempfile
import unittest
import threading
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from songs.store import SongStore
from songs.consensus import consensus, phrases_from_words
from songs.arrangement import edit_structure, edit_chords
from songs.import_audio import AudioImporter
from songs.review import review_command, _pitch_filter
from songs.analysis import SongAnalyzer
from songs.public import public_song, public_song_commands
from songs.chordino import parse_chords


class FullSongTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SongStore(Path(self.tmp.name))
        self.row = self.store.create('Synthetic')
        self.sid = self.row['song_id']
        self.phrases = phrases_from_words([{'words':[
            {'word':'Здесь','start':1.,'end':1.5},
            {'word':'тестовая','start':1.5,'end':2.2},
            {'word':'строка.','start':2.2,'end':3.1},
            {'word':'Вторая','start':4.,'end':5.},
            {'word':'строка.','start':5.,'end':6.}]}],'run')

    def result(self):
        self.store.patch(self.sid,{'status':'running','active_run_id':'run'})
        return self.store.save_full_analysis(self.sid,'run',self.phrases,'GigaAM',
            {'hypotheses':[{'model':'Whisper-test','phrases':self.phrases}],
             'harmony':{'events':[{'name':'Am','start_ms':1500,'verified':False}]}})

    def test_split_uses_actual_pause_and_char_offsets(self):
        self.assertEqual(len(self.phrases),2)
        self.assertEqual(self.phrases[1]['start_ms'],4000)
        self.assertEqual(self.phrases[0]['words'][1]['char_start'],6)

    def test_pitch_preview_filter_preserves_duration_chain_and_bounds_steps(self):
        self.assertIsNone(_pitch_filter(44100, 0))
        chain = _pitch_filter(44100, 12)
        self.assertIn('asetrate=', chain)
        self.assertIn('atempo=', chain)
        self.assertEqual(chain.count('aresample='), 2)

    def test_votes_do_not_invent_missing_words_or_timing(self):
        before = copy.deepcopy(self.phrases)
        other = [{'text':'Здесь другая строка. Совсем другая вторая строка.'}]
        rows = consensus(self.phrases,[{'model':'qwen','phrases':other},{'model':'qwen','phrases':other}])
        self.assertEqual(rows[0]['text'],before[0]['text'])
        self.assertEqual(rows[0]['words'],before[0]['words'])
        self.assertEqual(rows[0]['agreement'][1]['models'],2)
        self.assertTrue(rows[0]['agreement'][1]['needs_review'])

    def test_full_pass_retains_evidence_and_chord_word_position(self):
        row = self.result()
        self.assertEqual(row['guitar_sheet'][0]['chords'][0]['char_index'],6)
        self.assertEqual(row['timing_quality'],'word_estimated')
        self.assertEqual(len(row['analysis_runs'][0]['hypotheses']),2)
        self.assertFalse(row['chords'][0]['verified'])

    def test_majority_substitution_requires_two_models_and_new_alignment(self):
        original=copy.deepcopy(self.phrases)
        others=[{'model':m,'phrases':[{'text':'Здесь другая строка. Вторая строка.'}]} for m in ('gigaam','qwen')]
        selected=consensus(self.phrases,others,choose_majority=True)
        self.assertEqual(selected[0]['text'],'Здесь другая строка.')
        self.assertEqual(selected[0]['words'],[])
        self.assertTrue(selected[0]['alignment_stale'])
        self.assertEqual(selected[0]['agreement'][1]['agree'],2)
        self.assertEqual(self.phrases,original)

    def test_full_pass_cannot_overwrite_manual_phrase(self):
        row = self.result()
        row = self.store.edit_phrase(self.sid,'run-0','Текст исправлен','',False,row['revision'])
        row = self.result()
        self.assertEqual(row['phrases'][0]['text'],'Текст исправлен')
        self.assertEqual(row['candidate_run_id'],'run')

    def test_qwen_spine_has_editable_ids_and_persists_audio_comparison(self):
        self.store.patch(self.sid,{'status':'running','active_run_id':'qwen-run'})
        rows=[{'text':'Полная строка для правки','start_ms':0,'end_ms':5000}]
        report={'accepted':1,'needs_review':0,'proposals':[]}
        row=self.store.save_full_analysis(self.sid,'qwen-run',[],'not-selected',{
            'selected':{'phrases':rows},'comparison':report})
        self.assertEqual(row['analysis_runs'][-1]['comparison'],report)
        self.assertEqual(row['phrases'][0]['phrase_id'],'qwen-run-0')
        updated=self.store.edit_phrase(self.sid,'qwen-run-0','Исправленная строка','',False,row['revision'])
        self.assertEqual(updated['phrases'][0]['text'],'Исправленная строка')
        self.assertNotIn('phrase_id',rows[0])

    def test_manual_split_without_timing_is_rejected(self):
        row = self.result()
        row['phrases'][0]['words']=[]
        row = self.store.patch(self.sid,{'phrases':row['phrases']})
        with self.assertRaisesRegex(ValueError,'время разделения'):
            edit_structure(self.store,dict(song_id=self.sid,base_revision=row['revision'],
                phrase_id='run-0',action='split',char_index=6))

    def test_split_merge_and_section_survive_restart(self):
        row = self.result()
        row = edit_structure(self.store,dict(song_id=self.sid,base_revision=row['revision'],
            phrase_id='run-0',action='split',char_index=6))
        self.assertEqual(row['phrases'][0]['end_ms'],1500)
        row = edit_structure(self.store,dict(song_id=self.sid,base_revision=row['revision'],
            phrase_id='run-0',action='merge'))
        row = edit_structure(self.store,dict(song_id=self.sid,base_revision=row['revision'],
            phrase_id='run-0',action='section',kind='verse'))
        self.assertEqual(SongStore(self.store.root).get(self.sid)['sections'][0]['kind'],'verse')
        self.assertEqual(len(row['phrases']),2)

    def test_manual_chord_requires_revision_and_text_edit_invalidates_anchor(self):
        row = self.result()
        event = {'name':'E7','start_ms':1600,'anchor':{'phrase_id':'run-0','grapheme_index':8},'verified':True}
        with self.assertRaises(ValueError):
            edit_chords(self.store,dict(song_id=self.sid,chords=[event]))
        row = edit_chords(self.store,dict(song_id=self.sid,base_revision=row['revision'],chords=[event]))
        self.assertEqual(row['guitar_sheet'][0]['chords'][0]['char_index'],8)
        row = self.store.edit_phrase(self.sid,'run-0','Другая тестовая фраза','',False,row['revision'])
        self.assertNotIn('anchor',row['chords'][0])
        self.assertEqual(row['guitar_sheet'][0]['chords'][0]['anchor_quality'],'estimated_phrase')

    def test_cancelled_full_worker_never_applies_late_output(self):
        self.store.patch(self.sid,{'status':'cancelled','active_run_id':None})
        self.assertIsNone(self.store.save_full_analysis(self.sid,'old',self.phrases,'test',{}))

    def test_audio_upload_above_bridge_limit_uses_small_frames(self):
        buffer = io.BytesIO()
        with wave.open(buffer,'wb') as wav:
            wav.setnchannels(2);wav.setsampwidth(2);wav.setframerate(48000)
            wav.writeframes(b'\0'*(17*1024*1024))
        data=buffer.getvalue(); uploader=AudioImporter(self.store)
        token=uploader.command('song_upload_begin',{'song_id':self.sid,'name':'song.wav','size':len(data)})['upload_id']
        for offset in range(0,len(data),1024*1024):
            uploader.command('song_upload_chunk',{'upload_id':token,'offset':offset,
                'data':base64.b64encode(data[offset:offset+1024*1024]).decode()})
        row=uploader.command('song_upload_finish',{'upload_id':token})
        self.assertEqual((self.store.root/row['audio']['path']).stat().st_size,len(data))
        self.assertEqual(row['audio']['channels'],2)

    def test_wrong_upload_order_and_late_finish_do_not_replace_audio(self):
        uploader=AudioImporter(self.store)
        token=uploader.command('song_upload_begin',{'song_id':self.sid,'name':'song.mp3','size':4})['upload_id']
        with self.assertRaisesRegex(ValueError,'Порядок'):
            uploader.command('song_upload_chunk',{'upload_id':token,'offset':1,'data':'dGVzdA=='})
        uploader.command('song_upload_chunk',{'upload_id':token,'offset':0,'data':'dGVzdA=='})
        before=self.result()
        with self.assertRaisesRegex(ValueError,'Карточка изменилась'):
            uploader.command('song_upload_finish',{'upload_id':token})
        self.assertEqual(self.store.get(self.sid)['audio'],before['audio'])
        uploader.command('song_upload_cancel',{'upload_id':token})
        self.assertEqual(uploader.jobs,{})

    def test_ready_requires_explicit_review_and_new_edit_reopens_it(self):
        row=self.result()
        args={'song_id':self.sid,'base_revision':row['revision'],'lyrics_checked':True}
        with self.assertRaisesRegex(ValueError,'Подтвердите'):
            review_command(self.store,'song_finish_review',args)
        args['chords_checked']=True
        row=review_command(self.store,'song_finish_review',args)
        self.assertEqual(row['status'],'ready')
        self.assertTrue(all(c['verified'] for c in row['chords']))
        row=self.store.edit_phrase(self.sid,'run-0','Текст изменён','',False,row['revision'])
        self.assertEqual(row['status'],'review')

    def test_silence_is_not_a_ready_chord_arrangement(self):
        self.store.patch(self.sid,{'status':'running','active_run_id':'silence'})
        row=self.store.save_full_analysis(self.sid,'silence',[],'GigaAM',
            {'harmony':{'events':[{'name':'N','start_ms':0}]}})
        self.assertEqual(row['status'],'partial')
        self.assertFalse(row['capabilities']['chords'])

    def test_missing_local_stt_does_not_block_other_recognizers(self):
        audio=self.store.root/self.sid/'audio.wav'; audio.write_bytes(b'RIFF')
        self.store.patch(self.sid,{'audio':{'path':str(audio.relative_to(self.store.root))}})
        done=threading.Event()
        class Host:
            def current_stt(self): raise RuntimeError('Cached local recognizer unavailable')
        analyzer=SongAnalyzer(self.store.root,self.store,Host(),
            lambda event,**data: done.set() if not data.get('running') else None)
        with patch('songs.runtime.analyze',return_value={'hypotheses':[
                {'model':'Whisper-test','phrases':self.phrases}]}):
            analyzer.start(self.sid,full=True,models=['gigaam','whisper','qwen'])
            self.assertTrue(done.wait(3))
        row=self.store.get(self.sid)
        self.assertEqual(row['status'],'review')
        self.assertEqual(row['phrases'][0]['text'],self.phrases[0]['text'])
        self.assertEqual(row['analysis_errors'][0]['step'],'local_stt')

    def test_ui_payload_omits_archive_but_preserves_candidate_and_undo(self):
        row=self.result()
        row=self.store.edit_phrase(self.sid,'run-0','Ручная строка','',False,row['revision'])
        row=self.result()
        before=copy.deepcopy(row)
        row['edits'].append({'archive':'x'*(17*1024*1024)})
        ui=public_song(row)
        self.assertNotIn('edits',ui)
        self.assertEqual(ui['analysis_runs'][0]['run_id'],row['candidate_run_id'])
        self.assertEqual(ui['phrase_history']['run-0']['version_count'],2)
        self.assertEqual(row['phrases'],before['phrases'])
        class Commands:
            @public_song_commands
            def command(self,cmd,args): return [row] if cmd=='songs_list' else row
        cards=Commands().command('songs_list',{})
        self.assertNotIn('phrases',cards[0])
        self.assertNotIn('edits',Commands().command('song_get',{}))
        self.assertIs(Commands().command('unrelated',{}),row)

    def test_song_card_list_has_compact_projection(self):
        row = self.store.patch(self.sid, {'status': 'review', 'phrases': self.phrases,
            'analysis_runs': [{'run_id': 'large', 'hypotheses': [{'text': 'archive'}]}]})
        compact = self.store.list(compact=True)[0]
        self.assertEqual(compact['song_id'], row['song_id'])
        self.assertIn('capabilities', compact)
        self.assertNotIn('analysis_runs', compact)
        self.assertNotIn('guitar_sheet', compact)

    def test_chordino_preserves_bass_and_half_diminished_without_fake_score(self):
        events=parse_chords('0.000: N\n0.100: B7/D#\n2.500: Bm7b5\n',4000)
        self.assertEqual([e['name'] for e in events],['N','B7/D#','Bm7b5'])
        self.assertEqual(events[1]['end_ms'],2500)
        self.assertIsNone(events[1]['confidence'])
        with self.assertRaises(ValueError):parse_chords('2.0: Am\n1.0: E',4000)

if __name__ == '__main__': unittest.main()
