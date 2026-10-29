"""Real core: two planners debate, developer edits isolated code, two reviewers inspect real tests."""
import asyncio
import json
import re
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'core'))
from exam import Core

PLAN = '# План\n**Готово, когда:** add исправлен и настоящие тесты проходят.\n- [ ] Исправить add\n- [ ] Запустить тесты'
FINAL_PLAN = PLAN+'\n- [ ] Проверить граничные случаи'


class Model(BaseHTTPRequestHandler):
    seen = []
    approved = set()
    dev_step = 0
    reviews = []
    bad_order = False
    judgments = []
    def log_message(self,*args): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        model = body['model']; type(self).seen.append(model)
        system = next((m['content'] for m in body['messages'] if m['role']=='system'),'')
        user = next((m['content'] for m in reversed(body['messages']) if m['role']=='user'),'')
        if 'Ты оценщик завершённости' in system:
            h = re.search(r'plan_hash: ([a-f0-9]{64})',user)[1]
            type(self).judgments.append(model)
            veto = 'ОЦЕНЩИК_ПРОТИВ' in user
            delta = {'content':json.dumps({'verdict':'continue' if veto else 'complete','plan_hash':h,
                'unresolved_issues':['Нет критерия проверки отката'] if veto else [],'reason':'Нужна проверка отката' if veto else 'Замечания закрыты; критерии и тесты заданы'})}
        elif model in ('planner-a','planner-b'):
            h = re.search(r'plan_hash: ([a-f0-9]{64})',user)[1]
            if 'БЕЗ_СОГЛАСИЯ' in user:
                reply = {'verdict':'revise','plan_hash':h,'plan':PLAN+f'\nНовая версия {len(type(self).seen)} от {model}', 'blocking_issues':[], 'notes':'Есть разногласия'}
            elif 'ТЕКУЩИЙ ПЛАН:\n(нет)' in user:
                reply = {'verdict':'revise','plan_hash':h,'plan':PLAN,'blocking_issues':[],'notes':'Нужен план исправления и тестов'}
            elif model=='planner-b' and 'Проверить граничные случаи' not in user.split('ЗАМЕЧАНИЯ УЧАСТНИКОВ')[0]:
                reply = {'verdict':'revise','plan_hash':h,'plan':FINAL_PLAN,'blocking_issues':[],'notes':'Добавить проверку нуля и отрицательных чисел'}
                type(self).approved.clear()
            else:
                type(self).approved.add(model)
                reply = {'verdict':'approve','plan_hash':h,'blocking_issues':[],'notes':'План и проверки согласованы'}
            delta = {'content':json.dumps(reply,ensure_ascii=False)}
        elif model in ('tester','reviewer'):
            h = re.search(r'"plan_hash":"([a-f0-9]{64})"',system)[1]
            type(self).reviews.append((model,user))
            delta = {'content':json.dumps({'verdict':'approve','plan_hash':h,'blocking_issues':[],'notes':'Переданные факты тестов и diff соответствуют цели'},ensure_ascii=False)}
        elif model == 'developer' and 'В ФОНЕ' in system:
            if type(self).approved != {'planner-a','planner-b'} or not type(self).judgments: type(self).bad_order = True
            steps = [
                ('code_read',{'path':'calc.py'}),
                ('code_edit',{'path':'calc.py','old':'return a - b','new':'return a + b'}),
                ('code_run',{'command':'python3 -m unittest discover -q'}),
                ('update_plan',{'plan':FINAL_PLAN.replace('[ ]','[x]')}),
                ('finish',{'summary':'Исправил add. Проверены сложение, ноль и отрицательные числа. Тесты проходят.'}),
            ]
            i = type(self).dev_step; type(self).dev_step += 1
            name,args = steps[min(i,len(steps)-1)]
            delta = {'tool_calls':[{'index':0,'id':f'step{i}','type':'function','function':{'name':name,'arguments':json.dumps(args,ensure_ascii=False)}}]}
        else:
            delta = {'content':'{}'}
        self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.end_headers()
        self.wfile.write(('data: '+json.dumps({'choices':[{'delta':delta}]},ensure_ascii=False)+'\n\ndata: [DONE]\n\n').encode())


async def main():
    server = ThreadingHTTPServer(('127.0.0.1',0),Model)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='dewiar-council-') as folder:
            root=Path(folder); project=root/'project'; project.mkdir()
            (project/'calc.py').write_text('def add(a, b):\n    return a - b\n')
            (project/'test_calc.py').write_text('import unittest\nfrom calc import add\nclass Tests(unittest.TestCase):\n def test_values(self):\n  self.assertEqual(add(2, 3), 5)\n  self.assertEqual(add(-1, 0), -1)\n')
            subprocess.run([sys.executable,'-m','venv','--without-pip','--system-site-packages',str(project/'.venv')],check=True,capture_output=True)
            profile={'kind':'openai','name':'Local council test','model':'developer','base_url':f'http://127.0.0.1:{server.server_port}/v1','tools_mode':'native'}
            core=Core(profile,root/'core'); original=subprocess.Popen
            def spawn(command,*args,**kwargs):
                if '--bundle' in sys.argv and str(ROOT/'core/main.py') in command:
                    command=[str(ROOT/'dist/Dewiar.app/Contents/MacOS/Dewiar'),'--core','--server']
                return original(command,*args,**kwargs)
            try:
                with patch('subprocess.Popen',side_effect=spawn): core.start()
                await core.connect()
                profiles={name:dict(profile,model=name,name=name) for name in ('planner-a','planner-b','developer','tester','reviewer','outsider')}
                await core.call('settings_save',patch={'llm':{'active':'outsider','profiles':profiles},'tasks':{'auto_run':True,'learn':False},'team':{'route':False}})
                team={'enabled':True,'planners':['planner-a','planner-b'],'developer':'developer','tester':'tester','reviewer':'reviewer','max_rounds':3,'budget_seconds':120}
                task=await core.call('task_create',title='Council calculator',goal='Исправить add, проверить ноль и отрицательные числа настоящими тестами.',project=str(project),team=team)
                for _ in range(240):
                    data=await core.call('task_get',id=task['id'])
                    if data['status'] in ('done','failed','paused','stopped'): break
                    await asyncio.sleep(.2)
                assert data['status']=='done', (data['status'],data.get('error'),data['journal'][-5000:])
                assert data['council']['status']=='agreed' and len(data['council']['votes'])==2
                assert data['council']['calls']==4 and not Model.bad_order
                assert data['council']['judge']['id']=='outsider' and data['council']['judge_calls']==1
                assert Model.judgments==['outsider'], 'Default evaluator must be the active model'
                assert data['council']['evaluation']['verdict']=='complete'
                assert {r[0] for r in Model.reviews}=={'tester','reviewer'}
                assert all('passed' in text and 'return a + b' in text and 'self.assertEqual' in text for _,text in Model.reviews)
                assert 'return a - b' in (project/'calc.py').read_text(), 'Original project was modified without acceptance'
                assert '+    return a + b' in data['diff']
                assert 'Добавить проверку' in data['discussion'] and 'approve' in data['team_review']
                steps_before=Model.dev_step
                blocked=await core.call('task_create',title='No consensus',goal='БЕЗ_СОГЛАСИЯ: проверить остановку при разных планах',project=str(project),team=dict(team,max_rounds=1))
                for _ in range(100):
                    stopped=await core.call('task_get',id=blocked['id'])
                    if stopped['status'] in ('paused','failed','done','stopped'): break
                    await asyncio.sleep(.1)
                assert stopped['status']=='paused' and stopped['council']['status']=='no_consensus'
                assert stopped['steps']==0 and Model.dev_step==steps_before, 'Developer ran before consensus'
                assert stopped['council']['calls']==2
                veto=await core.call('task_create',title='Evaluator veto',goal='ОЦЕНЩИК_ПРОТИВ: исправить add',project=str(project),team=dict(team,judge='reviewer'))
                for _ in range(100):
                    rejected=await core.call('task_get',id=veto['id'])
                    if rejected['status'] in ('paused','failed','done','stopped'): break
                    await asyncio.sleep(.1)
                assert rejected['status']=='paused' and rejected['council']['status']=='no_consensus'
                assert rejected['steps']==0 and Model.dev_step==steps_before, 'Evaluator veto allowed development'
                assert rejected['council']['judge']['id']=='reviewer' and rejected['council']['judge_calls']>=1
                assert Model.judgments[-1]=='reviewer' and 'Нет критерия проверки отката' in rejected['discussion']
                print('OK: real core council, active/selected evaluator, veto blocks development, pinned developer, real tests and reviews, original preserved')
            finally:
                await core.stop()
    finally:
        server.shutdown(); server.server_close()


if __name__=='__main__': asyncio.run(main())
