"""Own endpoint and SMTP fallback. No real network/accounts or outbound email."""
import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
import egress
from reportdelivery import ReportDelivery,EMAIL,SUBJECT,validate_url
from mail import Mail,MailError
from health import HealthMixin


class Reports(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.server='';self.key='own-secret'
        self.s=SimpleNamespace(get=lambda group,key:self.server if key=='server_url' else self.key if key=='server_key' else None)
        self.mail=Mail(self.root/'mail.json')
        self.mail.data={'address':'sender@example.test','password':'test-only','smtp_host':'localhost'}
        self.smtp=Mock();self.mail._smtp=Mock(return_value=self.smtp)
        self.delivery=ReportDelivery(self.root,self.s,self.mail)
        self.body={'note':'Синтетическая ошибка','text':'Технический журнал с русским текстом','shot':'data:image/jpeg;base64,'+base64.b64encode(b'fixture-jpeg').decode()}
        self.addCleanup(patch.stopall)
        patch('egress.local_only',return_value=False).start()
        patch('egress.check_dewiar_host').start()
        patch.dict('os.environ',{},clear=False).start()
        import os
        self.env=patch.dict(os.environ,{'DEWIAR_REPORT_URL':''});self.env.start()

    def test_no_server_uses_mail_with_exact_subject_and_both_attachments(self):
        with patch('httpx.post') as post:
            r=self.delivery.send(self.body);post.assert_not_called()
        self.assertEqual(r['delivery'],'email')
        msg=self.smtp.send_message.call_args.args[0]
        self.assertEqual(str(msg['To']),EMAIL);self.assertEqual(str(msg['Subject']),SUBJECT)
        self.assertEqual([x.get_filename() for x in msg.iter_attachments()],['report.txt','screenshot.jpg'])
        self.assertIn('русским',list(msg.iter_attachments())[0].get_content())
        self.assertEqual(list(msg.iter_attachments())[1].get_payload(decode=True),b'fixture-jpeg')
        self.assertEqual(msg['Message-ID'],f"<{r['queue_id']}@example.test>")
        self.assertNotIn('payload',json.loads((self.delivery.root/(r['queue_id']+'.json')).read_text()))

    def test_successful_server_does_not_send_mail_and_never_receives_cloud_credentials(self):
        self.server='https://reports.example.test/api/reports'
        with patch('httpx.post',return_value=httpx.Response(200,json={'ok':True,'id':42,'secret':'private-report-status'})) as post:
            r=self.delivery.send(self.body)
        self.assertEqual(r['delivery'],'server');self.assertEqual(r['id'],42)
        self.mail._smtp.assert_not_called()
        args=post.call_args
        self.assertEqual(args.args[0],self.server)
        self.assertEqual(args.kwargs['headers']['Authorization'],'Bearer own-secret')
        self.assertEqual(args.kwargs['headers']['Idempotency-Key'],r['queue_id'])
        self.assertEqual(args.kwargs['json']['report_id'],r['queue_id'])
        self.assertNotIn('private-report-status',json.dumps(self.delivery.status()))

    def test_server_errors_fall_back_to_email(self):
        self.server='https://reports.example.test/api/reports'
        responses=[httpx.Response(503,text='unavailable'),httpx.Response(200,json={'ok':False}),httpx.Response(302,json={'ok':True,'id':1})]
        for response in responses:
            with patch('httpx.post',return_value=response):
                self.assertEqual(self.delivery.send(self.body)['delivery'],'email')
        with patch('httpx.post',side_effect=httpx.ConnectTimeout('test')):
            self.assertEqual(self.delivery.send(self.body)['delivery'],'email')

    def test_no_mail_persists_and_retry_after_setup_sends_original_once(self):
        self.mail.data={}
        r=self.delivery.send(self.body)
        self.assertEqual(r['delivery'],'queued');self.assertEqual(len(self.delivery.status()['pending']),1)
        stored=self.delivery.root/(r['queue_id']+'.json')
        self.assertEqual(stored.stat().st_mode & 0o777,0o600)
        self.assertEqual(json.loads(stored.read_text())['payload']['shot'],self.body['shot'])
        self.mail.data={'address':'sender@example.test','password':'test-only','smtp_host':'localhost'}
        fresh=ReportDelivery(self.root,self.s,self.mail)
        sent=fresh.retry(r['queue_id']);self.assertEqual(sent['delivery'],'email')
        self.assertEqual(fresh.retry(r['queue_id']),sent)
        self.assertEqual(self.smtp.send_message.call_count,1)
        self.assertEqual(fresh.status()['pending'],[])

    def test_smtp_failure_is_pending_not_sent_and_redacts_exception(self):
        self.smtp.send_message.side_effect=MailError('private smtp response / password=do-not-show')
        r=self.delivery.send(self.body)
        self.assertEqual(r['delivery'],'queued')
        self.assertNotIn('do-not-show',r['reason'])
        self.assertEqual(self.delivery.status()['pending'][0]['status'],'pending')

    def test_local_only_is_not_bypassed(self):
        self.server='https://reports.example.test/api/reports'
        self.mail.data['smtp_host']='smtp.example.test'
        with patch('egress.local_only',return_value=True),patch('httpx.post') as post:
            self.assertEqual(self.delivery.send(self.body)['delivery'],'queued');post.assert_not_called()
        self.mail._smtp.assert_not_called()
        self.mail.data['smtp_host']='127.0.0.1'
        with patch('egress.local_only',return_value=True),patch('httpx.post',side_effect=httpx.ConnectError('unavailable')):
            self.assertEqual(self.delivery.send(self.body)['delivery'],'queued')
        self.mail._smtp.assert_not_called()

    def test_vendor_disabled_still_allows_own_server(self):
        self.server='https://dewiar.pro/report'
        with patch('egress.check_dewiar_host',side_effect=egress.DewiarServicesDisabled('disabled')),patch('httpx.post') as post:
            self.assertEqual(self.delivery.send(self.body)['delivery'],'queued');post.assert_not_called()
        self.mail._smtp.assert_not_called()

    def test_invalid_url_and_report_id(self):
        for url in ('https://user:password@host.test/report','https://host.test/report?token=secret','file:///private/etc/passwd','http://remote.example.test/report'):
            with self.assertRaises(ValueError):validate_url(url)
        self.assertEqual(validate_url('http://127.0.0.1:1234/reports'),'http://127.0.0.1:1234/reports')
        with self.assertRaises(ValueError):self.delivery.retry('../outside')

    def test_screenshot_not_selected_is_not_attached(self):
        body=dict(self.body);body.pop('shot')
        self.delivery.send(body)
        self.assertEqual([p.get_filename() for p in self.smtp.send_message.call_args.args[0].iter_attachments()],['report.txt'])

    def test_confirmed_server_delivery_is_not_replayed_by_email_after_disk_failure(self):
        self.server='https://reports.example.test/reports'
        with patch('httpx.post',return_value=httpx.Response(200,json={'ok':True,'id':1})),patch.object(self.delivery,'_write',side_effect=[None,OSError('disk full')]):
            with self.assertRaises(OSError):self.delivery.send(self.body)
        self.mail._smtp.assert_not_called()

    def test_own_server_secrets_never_reach_original_vendor_status(self):
        records=[{'id':'email-fixture','queue_id':'a'*32,'s':'own-status-secret','status':'new'},
                 {'id':9,'s':'legacy-status-secret','status':'new'}]
        host=SimpleNamespace(_complaints=lambda:records,_cloud_base=lambda:'https://dewiar.pro/cloud.php',_complaints_save=Mock())
        with patch('egress.dewiar_services_enabled',return_value=True),patch('httpx.post',return_value=httpx.Response(200,json={'items':[]})) as post:
            self.assertEqual(HealthMixin.complaints_check(host),[])
            self.assertEqual(post.call_args.kwargs['json']['items'],[{'id':9,'s':'legacy-status-secret'}])


if __name__=='__main__':unittest.main()
