"""Native voice effects editor with cancellable isolated A/B audition."""
import copy
import threading
import uuid
from pathlib import Path
import gi
gi.require_version('Gtk','3.0');gi.require_version('Gst','1.0')
from gi.repository import Gtk, GLib, Gst, Pango
from . import audio_effects, engine, model


class VoiceEffectsDialog(Gtk.Dialog):
    def __init__(self, owner, item, audible_length):
        super().__init__(title='Voice effects',transient_for=owner,modal=True)
        self.owner=owner;self.item=copy.deepcopy(item);self.audible_length=audible_length
        self.controls={};self.loading=True;self.closed=False;self.serial=0;self.cancel_event=None
        self.samples=[];self.audition_path=None;self.audition_error=None
        self.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Apply effects',Gtk.ResponseType.OK)
        self.set_default_size(470,-1)
        body=self.get_content_area();body.set_border_width(16);body.set_spacing(10)
        name=Gtk.Label(label=Path(item['path']).name,xalign=0);name.set_ellipsize(Pango.EllipsizeMode.END)
        name.get_style_context().add_class('section');body.pack_start(name,False,False,0)
        presets=Gtk.Box(spacing=8);presets.pack_start(Gtk.Label(label='Starting preset',xalign=0),False,False,0)
        self.presets=Gtk.ComboBoxText()
        self.presets.append('custom','Custom settings')
        for key in audio_effects.PRESETS:self.presets.append(key,key)
        presets.pack_start(self.presets,True,True,0);body.pack_start(presets,False,False,0)
        self.enabled=Gtk.CheckButton(label='Enable voice effects');body.pack_start(self.enabled,False,False,0)
        self.pages=Gtk.Notebook();body.pack_start(self.pages,True,True,0)
        def page(title):
            content=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10);content.set_border_width(12)
            self.pages.append_page(content,Gtk.Label(label=title));return content
        amplify=page('Amplify');self.number(amplify,'gain_db','Gain (dB)',-24,24,.5)
        self.hint(amplify,'Positive gain makes the voice louder; negative gain makes it quieter. The final mix has peak limiting. This does not automatically normalize loudness.')
        tone=page('Tone')
        self.number(tone,'bass_db','Bass (dB)',-18,18,.5)
        self.number(tone,'treble_db','Treble (dB)',-18,18,.5)
        self.number(tone,'highpass_hz','Remove low rumble (Hz)',0,1000,10)
        self.number(tone,'lowpass_hz','Low-pass cutoff (Hz)',0,20000,100)
        self.hint(tone,'Set a cutoff to 0 to turn that filter off. Narration is a starting point for speech; Telephone narrows the frequency range.')
        echo=page('Echo');self.toggle(echo,'echo_enabled','Add echo')
        self.number(echo,'echo_delay_ms','Time between echoes (ms)',40,2000,10)
        self.number(echo,'echo_level','First echo level (%)',0,100,1,100)
        self.number(echo,'echo_decay','Repeat decay (%)',0,95,1,100)
        self.number(echo,'echo_repeats','Number of echoes',1,8,1)
        self.hint(echo,'Each repeat is quieter by the decay factor. Echo timing follows playback speed and stops at this track’s end or the video end.')
        compressor=page('Compressor');self.toggle(compressor,'compressor_enabled','Smooth voice levels')
        for key,title,low,high,step in (
            ('compressor_threshold_db','Threshold (dB)',-48,0,1),
            ('compressor_ratio','Ratio (:1)',1,12,.5),
            ('compressor_attack_ms','Attack (ms)',1,100,1),
            ('compressor_release_ms','Release (ms)',20,2000,10),
            ('compressor_makeup_db','Make-up gain (dB)',0,18,.5)):
            self.number(compressor,key,title,low,high,step)
        self.hint(compressor,'Reduces louder passages above the threshold. Make-up gain raises the processed level. Audition to find a suitable balance.')
        audition=Gtk.Box(spacing=8)
        self.listen=Gtk.Button(label='Listen with effects');self.compare=Gtk.Button(label='Without effects');self.stop=Gtk.Button(label='Stop')
        for button,callback in ((self.listen,lambda:self.audition(True)),(self.compare,lambda:self.audition(False)),(self.stop,self.stop_audition)):
            button.connect('clicked',lambda _,fn=callback:fn());audition.pack_start(button,True,True,0)
        body.pack_start(audition,False,False,0)
        self.status=Gtk.Label(label='Listen to the first 12 seconds of this track. Original files stay unchanged.',xalign=0)
        self.status.set_line_wrap(True);self.status.set_max_width_chars(50);body.pack_start(self.status,False,False,0)
        self.player=Gst.ElementFactory.make('playbin',None)
        self.player.set_property('video-sink',Gst.ElementFactory.make('fakesink',None))
        self.bus=self.player.get_bus();self.bus.add_signal_watch();self.bus_handler=self.bus.connect('message',self.on_message)
        initial=audio_effects.settings(item)
        if 'audio_effects' not in item:
            remembered=owner.preferences.get('last_voice_effects')
            try:
                if remembered is not None:audio_effects.validate(remembered);initial={**initial,**remembered}
            except ValueError:pass
        self.load(initial);self.presets.set_active_id('custom');self.loading=False
        self.presets.connect('changed',self.choose_preset);self.enabled.connect('toggled',self.changed)
        for widget,_ in self.controls.values():widget.connect('toggled' if isinstance(widget,Gtk.CheckButton) else 'value-changed',self.changed)
        self.connect('response',lambda *_:self.stop_audition());self.connect('destroy',lambda *_:self.cleanup())
        self.show_all()

    @staticmethod
    def hint(parent,text):
        label=Gtk.Label(label=text,xalign=0);label.set_line_wrap(True);label.set_max_width_chars(46)
        label.get_style_context().add_class('dim-label');parent.pack_start(label,False,False,0)

    def number(self,parent,key,title,low,high,step,mult=1):
        line=Gtk.Box(spacing=10);line.pack_start(Gtk.Label(label=title,xalign=0),True,True,0)
        spin=Gtk.SpinButton.new_with_range(low,high,step);spin.set_width_chars(6);spin.set_digits(1 if step<1 else 0)
        line.pack_end(spin,False,False,0);parent.pack_start(line,False,False,0);self.controls[key]=(spin,mult)

    def toggle(self,parent,key,title):
        button=Gtk.CheckButton(label=title);parent.pack_start(button,False,False,0);self.controls[key]=(button,1)

    def load(self,value):
        self.loading=True;self.enabled.set_active(value['enabled'])
        for key,(widget,mult) in self.controls.items():
            widget.set_active(value[key]) if isinstance(widget,Gtk.CheckButton) else widget.set_value(value[key]*mult)
        self.loading=False

    def read(self):
        value=audio_effects.defaults();value['enabled']=self.enabled.get_active()
        for key,(widget,mult) in self.controls.items():
            value[key]=widget.get_active() if isinstance(widget,Gtk.CheckButton) else widget.get_value()/mult
        value['echo_repeats']=int(value['echo_repeats']);audio_effects.validate(value);return value

    def choose_preset(self,widget):
        if self.loading or widget.get_active_id() not in audio_effects.PRESETS:return
        self.stop_audition();self.load(audio_effects.preset(widget.get_active_id()))
        self.status.set_text('Preset loaded. Adjust the controls, listen, then apply.')

    def changed(self,*_):
        if self.loading:return
        self.stop_audition();self.presets.set_active_id('custom')
        self.status.set_text('Settings changed. Listen again to hear the updated voice.')

    def stop_audition(self):
        self.serial+=1
        if self.cancel_event:self.cancel_event.set();self.cancel_event=None
        self.player.set_state(Gst.State.NULL)
        self.listen.set_sensitive(True);self.compare.set_sensitive(True)

    def audition(self,with_effects=True):
        try:value=self.read()
        except ValueError as error:self.status.set_text(str(error));return
        self.stop_audition();self.audition_error=None;self.audition_path=None
        item=copy.deepcopy(self.item);item['audio_effects']=value
        cancel=threading.Event();self.cancel_event=cancel;serial=self.serial
        output=self.owner.temp/('voice-audition-'+uuid.uuid4().hex+'.wav');self.samples.append(output)
        self.listen.set_sensitive(False);self.compare.set_sensitive(False);self.status.set_text('Preparing a short local voice preview…')
        def finish(error):
            if self.closed or serial!=self.serial:
                output.unlink(missing_ok=True);return False
            self.cancel_event=None;self.listen.set_sensitive(True);self.compare.set_sensitive(True)
            if error:
                self.audition_error=str(error);self.status.set_text(str(error));return False
            self.audition_path=output;self.player.set_property('uri',output.as_uri());self.player.set_state(Gst.State.PLAYING)
            self.status.set_text('Playing this track '+('with voice effects.' if with_effects else 'without voice effects.'))
            return False
        def work():
            try:engine.render_track_sample(item,output,with_effects,self.audible_length,cancel=cancel);error=None
            except Exception as error_value:error=error_value
            GLib.idle_add(finish,error)
        self.owner.pool.submit(work)

    def on_message(self,bus,message):
        if self.closed:return
        if message.type==Gst.MessageType.EOS:
            self.player.set_state(Gst.State.NULL);self.status.set_text('Finished. Compare without effects or apply these settings.')
        elif message.type==Gst.MessageType.ERROR:
            error,_=message.parse_error();self.audition_error=str(error)
            self.player.set_state(Gst.State.NULL);self.status.set_text('Could not play the audition: '+str(error))

    def cleanup(self):
        if self.closed:return
        self.stop_audition();self.closed=True;self.bus.disconnect(self.bus_handler);self.bus.remove_signal_watch()
        for path in self.samples:path.unlink(missing_ok=True)


def edit_voice_effects(owner,page='Echo'):
    item=owner.selected_item()
    if owner.busy or not item or owner.selected[0] not in ('clips','audio'):return
    if not engine.probe(item['path'])['has_audio']:
        owner.set_status('This clip has no audio.');return
    kind,index=owner.selected;source_id=item.get('id');at=owner.playhead
    length=model.clip_duration(item)
    if kind=='audio':length=max(0,min(length,model.project_duration(owner.project)-item.get('start',0)))
    owner.canvas.pause();owner.update_play_button(False)
    dialog=VoiceEffectsDialog(owner,item,length);owner.voice_dialog=dialog
    dialog.pages.set_current_page(('Amplify','Tone','Echo','Compressor').index(page))
    while dialog.run()==Gtk.ResponseType.OK:
        try:
            value=dialog.read();candidate=copy.deepcopy(owner.project)
            if candidate[kind][index].get('id')!=source_id:break
            candidate[kind][index]['audio_effects']=value;model.validate_project(candidate,check_files=False)
        except ValueError as error:dialog.status.set_text(str(error));continue
        try:owner.preferences.set('last_voice_effects',value)
        except OSError as error:owner.set_status('Could not remember voice settings: '+str(error))
        owner.checkpoint();owner.project=candidate;owner.changed();owner.seek(at)
        owner.set_status('Voice effects applied. Press Preview to hear them in the full mix.');break
    dialog.destroy();owner.voice_dialog=None
