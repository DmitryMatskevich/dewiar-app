"""Benchmark vocal separation with an already cached HTDemucs safetensors.

Run in the separate separation runtime (torch/torchaudio versions must match).
No downloads, Engine imports, settings changes, or voice-model access.
"""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import time


def separate(audio, output):
    import torch
    import torchaudio
    import soundfile as sf
    from safetensors import safe_open
    from safetensors.torch import load_file
    from demucs.htdemucs import HTDemucs
    from demucs.apply import apply_model
    files=list((Path.home()/'.cache/huggingface/hub/models--adefossez--HTDemucs').glob('snapshots/*/955717e8.safetensors'))
    if not files:
        raise FileNotFoundError('Cached HTDemucs not found; downloads disabled')
    weights=files[0]
    with safe_open(weights,framework='pt') as handle:
        metadata=handle.metadata()
    if metadata.get('klass')!='demucs.htdemucs.HTDemucs':
        raise ValueError('Unexpected cached model class')
    kwargs=json.loads(metadata['kwargs'])
    segment=kwargs.get('segment')
    if isinstance(segment,dict) and segment.get('_type')=='fraction':
        kwargs['segment']=Fraction(segment['numerator'],segment['denominator'])
    model=HTDemucs(**kwargs)
    model.load_state_dict(load_file(str(weights)),strict=True)
    model.eval()
    samples,sr=sf.read(audio,dtype='float32',always_2d=True)
    mix=torch.from_numpy(samples.T)
    if mix.shape[0]==1:mix=mix.repeat(2,1)
    elif mix.shape[0]!=2:mix=mix.mean(0,keepdim=True).repeat(2,1)
    if sr!=model.samplerate:
        mix=torchaudio.functional.resample(mix,sr,model.samplerate)
    reference=mix.mean(0)
    mean,std=reference.mean(),reference.std().clamp_min(1e-8)
    with torch.inference_mode():
        sources=apply_model(model,((mix-mean)/std)[None],device='cpu',shifts=0,
                            split=True,overlap=.25,num_workers=0,progress=True)[0]
    sources=sources*std+mean
    vocals=sources[model.sources.index('vocals')]
    output.mkdir(parents=True,exist_ok=True)
    sf.write(output/'vocals.wav',vocals.T.numpy(),model.samplerate,subtype='FLOAT')
    sf.write(output/'accompaniment.wav',(mix-vocals).T.numpy(),model.samplerate,subtype='FLOAT')
    return {'model':'HTDemucs-955717e8','weights':str(weights),'sample_rate':model.samplerate,
            'samples':mix.shape[-1],'channels':2,'downloads':False,'device':'cpu'}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('audio',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();start=time.monotonic()
    result=separate(args.audio,args.output);result['seconds']=round(time.monotonic()-start,2)
    (args.output/'separation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)
