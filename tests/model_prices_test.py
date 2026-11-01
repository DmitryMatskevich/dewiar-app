"""Prices are public data, not an inference or billing operation."""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
import model_prices as P

NOW=dt.datetime(2026,11,2,2,tzinfo=dt.timezone.utc)  # Monday, DeepSeek peak
RAW={'id':'anthropic/claude-haiku-5.5','name':'Haiku','pricing':{'prompt':'0.0000001','completion':'0.0000005','input_cache_read':'0.00000001',
    'overrides':[{'min_prompt_tokens':100000,'prompt':'0.0000005','completion':'0.0000025'}]},'context_length':1000000}

class Prices(unittest.TestCase):
    def test_per_token_conversion_zero_and_unknown(self):
        self.assertEqual(P.rate('0.0000001'),.1)
        self.assertEqual(P.rate('0'),0)
        for value in ['NaN','inf','-1',None,'bad']:self.assertIsNone(P.rate(value))

    def test_filters_batch_and_unknown_provider(self):
        data=[RAW,dict(RAW,id=RAW['id']+':batch'),dict(RAW,id='other/test')]
        self.assertEqual(len(P.normalize(data)),1)
        with self.assertRaises(ValueError):P.normalize([])

    def test_long_context_and_calculation(self):
        row=P.normalize([RAW])[0]
        short=P.estimate(row,10000,1000,NOW)
        self.assertAlmostEqual(short['cost'],.0015)
        long=P.estimate(row,120000,1000,NOW)
        self.assertEqual(long['input'],.5);self.assertEqual(long['output'],2.5)
        self.assertAlmostEqual(long['cost'],.0625)

    def test_peak_and_weekend(self):
        rule={'utc_days':['monday'],'utc_start':100,'utc_end':400}
        self.assertTrue(P.matches(rule,0,NOW))
        self.assertFalse(P.matches(rule,0,NOW+dt.timedelta(days=5)))
        self.assertTrue(P.matches({'utc_start':1000,'utc_end':0},0,NOW.replace(hour=23)))
        self.assertIsNone(P.matches({'future_condition':'new'},0,NOW))

    def test_unknown_conditions_never_present_false_estimate(self):
        row=P.normalize([dict(RAW,pricing={'prompt':'0','completion':'0','overrides':[{'unknown':123}]})])[0]
        self.assertIsNone(P.estimate(row,1,1,NOW)['cost'])

    def test_refresh_cache_network_failure_and_local_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            response=httpx.Response(200,json={'data':[RAW]},request=httpx.Request('GET',P.URL))
            with patch('egress.local_only',return_value=False),patch.object(P.httpx,'get',return_value=response) as get:
                data=P.get_prices(tmp,True,now=NOW)
                get.assert_called_once_with(P.URL,timeout=15,follow_redirects=False)
                self.assertEqual(data['checked_at'],NOW.isoformat())
            with patch('egress.local_only',return_value=False),patch.object(P.httpx,'get',side_effect=httpx.ConnectError('offline')):
                stale=P.get_prices(tmp,True,now=NOW+dt.timedelta(days=1))
                self.assertEqual(stale['checked_at'],data['checked_at']);self.assertTrue(stale['warning'])
            with patch('egress.local_only',return_value=True),patch.object(P.httpx,'get') as get:
                self.assertTrue(P.get_prices(tmp,True)['warning']);get.assert_not_called()
            with self.assertRaises(ValueError):P.get_prices(tmp,input_tokens=-1)
            with self.assertRaises(ValueError):P.get_prices(tmp,input_tokens=True)

if __name__=='__main__':unittest.main()
