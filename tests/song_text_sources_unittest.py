"""Manual text-source review: isolated store, no network/settings/models."""
import copy
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from songs.store import SongStore
from songs.text_sources import command,edit_span
from songs.reference_text import tokens,reference_candidates
from songs.text_reference import run
from songs.sources import _embedded_lyrics

TEXT='Тихий вечер согревает окна нашего дома'
BAD=TEXT.replace('согревает','согревае')
def phrase(text=BAD):
    return dict(phrase_id='p',text=text,start_ms=1000,end_ms=9000,
        words=[dict(text=t['text'],char_start=t['start'],char_end=t['end'],start_ms=1000+i*1000,end_ms=1500+i*1000,method='forced_alignment') for i,t in enumerate(tokens(text))])

class TextSourcesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=SongStore(Path(self.tmp.name));r=self.store.create('ТЕСТ')
        self.row=self.store.patch(r['song_id'],dict(phrases=[phrase()],selected_source_id='recording',audio={'path':'kept.wav'},chords=[{'name':'Am','start_ms':8000}]))
    def cmd(self,action,**args):
        self.row=command(self.store,action,dict(song_id=self.row['song_id'],base_revision=self.row['revision'],**args));return self.row
    def add(self):return self.cmd('add',lyrics=TEXT)
    def confirm(self):return self.cmd('confirm',candidate_id=self.row['text_reference']['suggested_sources'][0]['candidate_id'])
    def test_manual_source_without_consensus_confirm_compare_apply_reload_and_undo(self):
        before=copy.deepcopy(self.row);self.add()
        self.assertEqual(self.row['text_reference']['state'],'needs_confirmation');self.assertEqual(self.row['phrases'],before['phrases'])
        self.confirm();self.assertTrue(self.row['text_reference']['references'][0]['user_confirmed'])
        self.assertEqual(len(self.row['text_reference']['candidates']),1)
        self.cmd('apply',indexes=[0]);saved=self.store.get(self.row['song_id'])
        self.assertEqual(saved['phrases'][0]['text'],TEXT)
        self.assertEqual(saved['audio'],before['audio']);self.assertEqual(saved['selected_source_id'],'recording');self.assertEqual(saved['chords'],before['chords'])
        self.assertEqual([(w['start_ms'],w['end_ms']) for w in saved['phrases'][0]['words']],[(w['start_ms'],w['end_ms']) for w in before['phrases'][0]['words']])
        self.assertEqual(saved['phrase_history']['p']['versions'][0]['text'],BAD)
        with self.assertRaises(ValueError):self.cmd('apply',indexes=[0])
    def test_stale_revision_does_not_overwrite(self):
        with self.assertRaises(ValueError):command(self.store,'add',dict(song_id=self.row['song_id'],base_revision=1,lyrics=TEXT))
    def test_apply_all_accepts_all_unprotected_corrections(self):
        self.add();self.confirm()
        self.row=command(self.store,'apply_all',dict(song_id=self.row['song_id'],base_revision=self.row['revision']))
        self.assertEqual(self.row['phrases'][0]['text'],TEXT)
    def test_unavailable_page_stays_actionable_without_fake_reference(self):
        with patch('songs.sources._public_http_url'):
            self.row=command(self.store,'add',dict(song_id=self.row['song_id'],base_revision=self.row['revision'],url='https://example.org/poem'),fetch=lambda u:{'status':'unavailable','error':'HTTP 403'})
        self.assertEqual(self.row['text_reference']['state'],'source_unavailable')
        self.assertEqual(self.row['text_reference'].get('references',[]),[])
        self.assertEqual(self.row['text_reference']['suggested_sources'][0]['source_check']['error'],'HTTP 403')
    def test_user_edit_can_be_replaced_by_selected_source_correction(self):
        self.row=self.store.patch(self.row['song_id'],{'phrases':[dict(phrase(),manual_edited=True)]})
        self.add();self.confirm()
        self.row=self.cmd('apply',indexes=[0])
        self.assertNotEqual(self.row['phrases'][0]['text'], phrase()['text'])
    def test_confirmed_source_is_used_when_consensus_absent(self):
        self.add();self.confirm()
        updated,report=run(self.row['phrases'],self.row,search=lambda q:[],fetch=lambda u:(_ for _ in ()).throw(AssertionError('No fetch for pasted text')))
        self.assertEqual(report['state'],'compared');self.assertEqual(updated,self.row['phrases']);self.assertEqual(len(report['references']),1)
    def test_extra_repeats_each_get_candidate_but_no_text_deletion(self):
        ps=[dict(phrase(),phrase_id=str(i),start_ms=i*10000,end_ms=(i+1)*10000) for i in range(3)]
        cs=reference_candidates(ps,[{'plain_lyrics':TEXT,'url':'https://example.org/poem'}])
        self.assertEqual({c['current']['phrase_id'] for c in cs},{'0','1','2'})
        self.assertTrue(all(c['current']['text']=='согревае' and c['alternative']['text']=='согревает' for c in cs))
    def test_split_word_keeps_pause_before_next_word(self):
        p={'text':'вдоме Река','words':[{'text':'вдоме','char_start':0,'char_end':5,'start_ms':1000,'end_ms':2000},
                                     {'text':'Река','char_start':6,'char_end':10,'start_ms':6000,'end_ms':7000}]}
        edit_span(p,0,10,'в доме\nРеки')
        self.assertEqual([w['text'] for w in p['words']],['в','доме','Реки'])
        self.assertEqual(p['words'][1]['end_ms'],2000)
        self.assertEqual(p['words'][2]['start_ms'],6000)
    def test_punctuation_and_partial_timing_survive_correction(self):
        p={'text':'Поездд. Тихий вечер','timing_quality':'word_partial','words':[
            {'text':'Поездд.','char_start':0,'char_end':7,'start_ms':1000,'end_ms':2000},
            {'text':'Тихий','char_start':8,'char_end':13,'start_ms':5000,'end_ms':6000}]}
        edit_span(p,0,6,'Поезд')
        self.assertEqual(p['words'][0]['text'],'Поезд.')
        self.assertEqual(p['words'][0]['char_end'],6)
        self.assertEqual(p['timing_quality'],'word_partial')
    def test_genius_extracts_nested_lyrics_not_header_or_footer(self):
        doc='<h1>Wrong title</h1><div data-lyrics-container="true"><div data-exclude-from-selection="true"><span>3 Contributors</span><h2>Lyrics</h2></div>[Куплет 1]<br>Тихий <a><span>вечер</span></a><br>Наш дом</div><p>Buy tickets</p><div data-lyrics-container="true">[Припев]<br>Свет в окне</div>'
        self.assertEqual(_embedded_lyrics(doc,'https://genius.com/test-lyrics'),'Тихий вечер\nНаш дом\n\nСвет в окне')

if __name__=='__main__':unittest.main()
