"""Validated, non-destructive voice settings and shared FFmpeg filters."""
import math

PRESETS = ('Neutral', 'Narration', 'Telephone', 'Echo')
RANGES = {
    'gain_db': (-24,24), 'bass_db': (-18,18), 'treble_db': (-18,18),
    'highpass_hz': (0,1000), 'lowpass_hz': (0,20000),
    'echo_delay_ms': (40,2000), 'echo_level': (0,1), 'echo_decay': (0,.95),
    'echo_repeats': (1,8), 'compressor_threshold_db': (-48,0),
    'compressor_ratio': (1,12), 'compressor_attack_ms': (1,100),
    'compressor_release_ms': (20,2000), 'compressor_makeup_db': (0,18),
}


def defaults():
    return {'enabled':True, 'gain_db':0., 'bass_db':0., 'treble_db':0.,
            'highpass_hz':0., 'lowpass_hz':0., 'echo_enabled':False,
            'echo_delay_ms':240., 'echo_level':.35, 'echo_decay':.45,
            'echo_repeats':3, 'compressor_enabled':False,
            'compressor_threshold_db':-18., 'compressor_ratio':3.,
            'compressor_attack_ms':10., 'compressor_release_ms':180.,
            'compressor_makeup_db':2.}


def settings(item):
    result=defaults();result.update(item.get('audio_effects',{}));return result


def validate(value):
    if not isinstance(value,dict):raise ValueError('Voice effects must contain valid settings.')
    if set(value)-set(defaults()):raise ValueError('Unsupported voice effect setting.')
    for key in ('enabled','echo_enabled','compressor_enabled'):
        if not isinstance(value.get(key,defaults()[key]),bool):
            raise ValueError(f'Voice effect {key.replace("_"," ")} must be on or off.')
    for key,(low,high) in RANGES.items():
        number=value.get(key,defaults()[key])
        if (isinstance(number,bool) or not isinstance(number,(int,float))
            or not math.isfinite(number) or not low<=number<=high
            or (key=='echo_repeats' and int(number)!=number)):
            raise ValueError(f'Voice effect {key.replace("_"," ")} must be between {low} and {high}.')
    high=value.get('highpass_hz',0);low=value.get('lowpass_hz',0)
    if high and low and low<=high:
        raise ValueError('The low-pass cutoff must be above the high-pass cutoff.')


def preset(name):
    if name not in PRESETS:raise ValueError('Choose Neutral, Narration, Telephone or Echo.')
    result=defaults()
    if name=='Narration':result.update(highpass_hz=80.,bass_db=1.,treble_db=2.,compressor_enabled=True)
    elif name=='Telephone':result.update(highpass_hz=300.,lowpass_hz=3400.)
    elif name=='Echo':result['echo_enabled']=True
    return result


def _fmt(number):return format(float(number),'.8f').rstrip('0').rstrip('.') or '0'


def filters(item):
    value=settings(item);validate(value)
    if not value['enabled']:return []
    result=[]
    for key,name in (('highpass_hz','highpass'),('lowpass_hz','lowpass')):
        if value[key]:result.append(f'{name}=f={_fmt(value[key])}')
    for key,name,freq in (('bass_db','bass',150),('treble_db','treble',4000)):
        if value[key]:result.append(f'{name}=g={_fmt(value[key])}:f={freq}')
    if value['compressor_enabled']:
        result.append('acompressor='+':'.join(f'{key}={_fmt(number)}' for key,number in (
            ('threshold',10**(value['compressor_threshold_db']/20)),
            ('ratio',value['compressor_ratio']),('attack',value['compressor_attack_ms']),
            ('release',value['compressor_release_ms']),('makeup',10**(value['compressor_makeup_db']/20)))))
    if value['gain_db']:result.append(f'volume={_fmt(value["gain_db"])}dB')
    if value['echo_enabled'] and value['echo_level']:
        delays=[];decays=[]
        for i in range(int(value['echo_repeats'])):
            level=value['echo_level']*value['echo_decay']**i
            if level<=0:break
            delays.append(_fmt(value['echo_delay_ms']*(i+1)));decays.append(_fmt(level))
        result.append('aecho=in_gain=1:out_gain=1:delays='+'|'.join(delays)+':decays='+'|'.join(decays))
    return result


def active(item):return bool(filters(item))


def summary(item):
    value=settings(item)
    if not value['enabled']:return 'Voice effects bypassed'
    names=[]
    if value['echo_enabled'] and value['echo_level']:names.append('Echo')
    if value['gain_db']:names.append(f'{value["gain_db"]:+g} dB gain')
    if any(value[key] for key in ('bass_db','treble_db','highpass_hz','lowpass_hz')):names.append('Tone')
    if value['compressor_enabled']:names.append('Compressor')
    return ' · '.join(names) or 'No voice effects'
