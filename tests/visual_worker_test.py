"""Verify the actual frozen OCR subprocess without starting the engine or models."""
import io,json,subprocess,sys
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
bundle=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).resolve().parents[1]/'dist/Dewiar.app'
image=Image.new('RGB',(1600,600),'white')
ImageDraw.Draw(image).text((100,100),'Сохранить проект',font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',48),fill='black')
stream=io.BytesIO();image.save(stream,'PNG')
result=subprocess.run([str(bundle/'Contents/MacOS/Dewiar'),'--ocr-worker',json.dumps(['ru-RU','en-US']),'details'],input=stream.getvalue(),capture_output=True,timeout=30)
assert result.returncode==0,'Frozen OCR worker failed'
reply=json.loads(result.stdout);assert reply.get('ok'),reply.get('error')
assert 'Сохранить' in reply['result']['text']
words=[word for line in reply['result']['lines'] for word in line['words']]
assert len(words)>=2 and all(len(word['box'])==4 for word in words)
print('PASS: frozen OCR worker; Russian text; actual word geometry; no main engine started')
