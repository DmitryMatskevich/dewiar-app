"""Annual dates, advance reminders, explicit birthday dialogue and persistent scheduling."""
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from when import next_time, parse, describe_repeat, annual_date
from birthdays import BirthdayDialog, candidate
from tools import Reminders, _when, ToolError

NOW=datetime(2026,10,27,12)


class Birthdays(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.addCleanup(patch.stopall)
        patch('tools.STATE_DIR',self.root).start(); patch('tools.threading.Thread.start').start()
        self.rem=Reminders(lambda item:None)
        self.dialog=BirthdayDialog(self.root/'pending.json',self.rem)

    def say(self,text,scope='chat:a',now=NOW):return self.dialog.handle(text,scope,now)

    def rule(self,**kwargs):return dict(kind='yearly',month=5,day=15,time='09:00',**kwargs)

    def test_yearly_parser_and_calendar_date(self):
        for text in ('напоминай каждый год 15 мая в 9 поздравить Анну','каждый год 15.05 в 9 напомни поздравить Анну','напоминай ежегодно 15 мая в 9 поздравить Анну'):
            r=parse(text,NOW)
            self.assertEqual(r['at'],datetime(2027,5,15,9))
            self.assertEqual(r['repeat']['kind'],'yearly')
            self.assertEqual(r['text'],'Поздравить Анну')
        self.assertIsNone(parse('напоминай каждый год в 9 поздравить Анну',NOW))
        self.assertIsNone(parse('напоминай каждый год 31 февраля в 9',NOW))
        early=parse('напоминай каждый год за неделю до 1 января в 9 поздравить Анну',NOW)
        self.assertEqual(early['at'],datetime(2026,12,25,9))
        self.assertEqual(early['repeat']['offset_days'],7)

    def test_same_day_and_year_boundary(self):
        rep=self.rule()
        self.assertEqual(next_time(rep,datetime(2027,5,15,8)),datetime(2027,5,15,9))
        self.assertEqual(next_time(rep,datetime(2027,5,15,9)),datetime(2028,5,15,9))
        r=dict(rep,month=1,day=1,offset_days=7)
        self.assertEqual(next_time(r,NOW),datetime(2026,12,25,9))
        self.assertEqual(next_time(r,datetime(2026,12,26)),datetime(2027,12,25,9))

    def test_leap_rule_does_not_drift_to_feb28_in_leap_year(self):
        rep=dict(self.rule(),month=2,day=29)
        self.assertEqual(next_time(rep,NOW),datetime(2027,2,28,9))
        self.assertEqual(next_time(rep,datetime(2027,3,1)),datetime(2028,2,29,9))
        self.assertEqual(next_time(dict(rep,leap_day='mar1'),NOW),datetime(2027,3,1,9))
        self.assertEqual(next_time(dict(rep,offset_days=1),datetime(2028,1,1)),datetime(2028,2,28,9))

    def test_invalid_rules_rejected_before_save(self):
        for values in ({'month':13},{'day':32},{'offset_days':366},{'time':'25:00'},{'leap_day':'guess'}):
            with self.assertRaises((ValueError,KeyError)):
                self.rem.add(NOW,'invalid',dict(self.rule(),**values))
        self.assertEqual(self.rem.items,[])
        with self.assertRaises(ToolError):_when({'in_minutes':5,'repeat':'yearly'})

    def test_initial_mention_never_creates_reminder(self):
        r=self.say('У Анны день рождения 15 мая')
        self.assertIn('ежегодными',r['text']); self.assertIn('9:00',r['text'])
        self.assertEqual(self.rem.items,[])

    def test_complete_annual_and_multiple_offsets(self):
        self.say('У Анны день рождения 15 мая')
        r=self.say('Да, каждый год за неделю и в сам день в 9:00')
        self.assertEqual(len(r['items']),2)
        self.assertEqual({x['repeat']['offset_days'] for x in self.rem.items},{0,7})
        self.assertEqual({x['at'] for x in self.rem.items},{'2027-05-08T09:00:00','2027-05-15T09:00:00'})
        self.assertTrue(all(x['repeat']['kind']=='yearly' for x in self.rem.items))
        self.assertIsNone(self.say('да'))

    def test_multi_turn_defaults_to_nine(self):
        self.say('У Анны день рождения 15 мая')
        self.assertIn('Когда',self.say('да')['text'])
        self.assertEqual(self.say('за день')['items'][0]['at'],'2027-05-14T09:00:00')

    def test_explicit_time_overrides_default_and_old_pending_gets_default(self):
        self.say('У Анны день рождения 15 мая')
        self.assertEqual(self.say('ежегодно за день в 19:30')['items'][0]['at'],'2027-05-14T19:30:00')
        self.say('У Ольги день рождения 20 июня')
        self.dialog.pending['chat:a']['time']=None; self.dialog.save()
        self.dialog=BirthdayDialog(self.root/'pending.json',self.rem)
        self.assertEqual(self.say('один раз в сам день')['items'][0]['at'],'2027-06-20T09:00:00')

    def test_invalid_explicit_time_is_not_replaced_with_default(self):
        self.say('У Анны день рождения 15 мая')
        self.assertIn('Уточните время',self.say('ежегодно в сам день в 25:00')['text'])
        self.assertEqual(self.rem.items,[])
        self.assertEqual(self.say('ежегодно в сам день в 18:00')['items'][0]['at'],'2027-05-15T18:00:00')

    def test_once_is_not_annual(self):
        self.say('15 мая у Анны ДР')
        r=self.say('Нет, один раз в сам день в 10:00')
        self.assertEqual(len(r['items']),1); self.assertIsNone(r['items'][0]['repeat'])

    def test_decline_creates_nothing(self):
        self.say('У Анны день рождения 15 мая'); self.say('не нужно')
        self.assertEqual(self.rem.items,[])
        self.assertIsNone(self.say('да'))

    def test_leap_day_requires_explicit_policy(self):
        self.say('У Пети день рождения 29 февраля')
        r=self.say('да, ежегодно в сам день в 9:00')
        self.assertIn('невисокосный',r['text']); self.assertEqual(self.rem.items,[])
        r=self.say('1 марта')
        self.assertEqual(r['items'][0]['repeat']['day'],29)
        self.assertEqual(r['items'][0]['repeat']['month'],2)
        self.assertEqual(r['items'][0]['at'],'2027-03-01T09:00:00')

    def test_pending_survives_restart_and_is_chat_scoped(self):
        self.say('У Анны день рождения 15 мая')
        self.dialog=BirthdayDialog(self.root/'pending.json',self.rem)
        self.assertIsNone(self.say('да',scope='chat:b'))
        self.assertIsNone(self.say('да',scope='remote:Telegram'))
        self.assertEqual(len(self.say('да, каждый год в сам день в 9')['items']),1)
        self.rem=Reminders(lambda item:None)
        self.assertEqual(self.rem.items[0]['repeat']['kind'],'yearly')

    def test_repeated_setup_does_not_duplicate(self):
        for _ in range(2):
            self.say('У Анны день рождения 15 мая')
            self.say('да ежегодно в сам день в 9')
        self.assertEqual(len(self.rem.items),1)

    def test_missing_date_is_requested(self):
        self.say('У Анны день рождения')
        self.assertIn('дату',self.say('да каждый год в сам день в 9')['text'])
        self.assertEqual(self.say('15 мая')['items'][0]['at'],'2027-05-15T09:00:00')

    def test_unrelated_request_is_not_consent(self):
        self.say('У Анны день рождения 15 мая')
        self.assertIsNone(self.say('Включи музыку'))
        self.assertEqual(self.rem.items,[])
        self.assertIsNone(self.say('да'))
        self.assertIsNone(candidate('Как поздравить Анну с днем рождения 15 мая?'))
        self.assertIsNone(candidate('Когда день рождения Анны?'))

    def test_old_birth_year_is_not_next_reminder_year(self):
        self.assertEqual(annual_date('15 мая 1980')['birth_year'],1980)
        self.say('У Анны день рождения 15 мая 1980')
        self.assertEqual(self.say('ежегодно в сам день в 9')['items'][0]['at'],'2027-05-15T09:00:00')

    def test_rule_descriptions_and_name_forms(self):
        self.assertIn('каждый год',describe_repeat(self.rule()))
        self.assertEqual(candidate('Напомни о дне рождения Анны 15 мая')['name'],'Анны')
        self.assertEqual(candidate('У Анны день рождения 15 мая, каждый год в 9:00 за неделю и в сам день')['name'],'Анны')

    def test_scheduler_catches_up_after_years_offline(self):
        due=[]; self.rem.on_fire=due.append
        self.rem.add(datetime(2020,5,15,9),'День рождения Анны',self.rule())
        class Clock(datetime):
            @classmethod
            def now(cls,*args):return NOW
        with patch('tools.datetime',Clock),patch('tools.time.sleep',side_effect=[None,StopIteration]):
            with self.assertRaises(StopIteration):self.rem._loop()
        self.assertEqual(len(due),1); self.assertTrue(due[0]['late'])
        self.assertEqual(self.rem.items[0]['at'],'2027-05-15T09:00:00')

    def test_explicit_this_year_does_not_silently_schedule_next_year(self):
        self.say('У Анны день рождения 15 мая')
        self.assertIn('уже прошёл',self.say('только в этом году в сам день в 9')['text'])
        self.assertEqual(self.rem.items,[])
        self.assertEqual(len(self.say('один раз в ближайший срок')['items']),1)

    def test_invalid_date_keeps_question_open(self):
        self.say('У Анны день рождения')
        self.assertIn('Такой даты нет',self.say('31 февраля')['text'])
        self.assertEqual(self.rem.items,[])
        self.say('15 мая')
        self.assertEqual(len(self.say('ежегодно в сам день в 9')['items']),1)


if __name__=='__main__':unittest.main()
