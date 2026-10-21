"""Native layer and caption tools attached to the main editor."""
import copy
import re
import threading
from pathlib import Path
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk,Gdk,GLib,Pango,GdkPixbuf
from . import model,engine,ui_theme
from .effect_ui import AnimationEditor,SoundEditor,page_tabs


def row_box(vertical=True,spacing=8):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,spacing=spacing)


def action(title,callback):
    widget=Gtk.Button(label=title);widget.connect('clicked',lambda _:callback());return widget


def caption(text):
    widget=Gtk.Label(label=text,xalign=0);widget.set_line_wrap(True);widget.set_max_width_chars(27);return widget


class LayerTools:
    def __init__(self,owner):
        self.owner=owner;self.assets={};self.drag=None;self.controls={}
        self.layers_panel=self.make_layers_panel();self.captions_panel=self.make_captions_panel()

    def make_layers_panel(self):
        panel=row_box();panel.set_border_width(12)
        add=action('Add text',self.add_text);add.get_style_context().add_class('suggested-action');panel.pack_start(add,False,False,0)
        imports=row_box(False);imports.pack_start(action('Image / logo',lambda:self.import_visual('image')),True,True,0)
        imports.pack_start(action('Overlay video',lambda:self.import_visual('video')),True,True,0);panel.pack_start(imports,False,False,0)
        hint=caption('Drag a layer on the canvas to position it. Drag its timeline edges to set when it appears.')
        hint.get_style_context().add_class('dim-label');panel.pack_start(hint,False,False,0)
        self.overlay_controls=row_box()
        self.overlay_controls.set_no_show_all(True)
        self.overlay_controls.pack_start(caption('Selected overlay opacity'),False,False,0)
        self.opacity=Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,0,100,1)
        self.opacity.set_digits(0);self.opacity.set_value_pos(Gtk.PositionType.RIGHT)
        self.opacity.connect('format-value',lambda _,value:f'{value:.0f}%')
        self.opacity.get_accessible().set_name('Selected overlay opacity')
        self.opacity_dragging=False;self.opacity_checkpointed=False;self.syncing_opacity=False
        self.opacity.connect('button-press-event',self.opacity_begin)
        self.opacity.connect('button-release-event',self.opacity_end)
        self.opacity.connect('value-changed',self.opacity_changed)
        self.overlay_controls.pack_start(self.opacity,False,False,0)
        quick=row_box(False)
        quick.pack_start(action('Full video',self.overlay_full_video),True,True,0)
        quick.pack_start(action('Size / position',lambda:self.owner.open_properties('Picture')),True,True,0)
        self.overlay_controls.pack_start(quick,False,False,0)
        panel.pack_start(self.overlay_controls,False,False,0)
        self.layers_list=Gtk.ListBox();self.layers_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll=Gtk.ScrolledWindow();scroll.set_policy(Gtk.PolicyType.NEVER,Gtk.PolicyType.AUTOMATIC);scroll.add(self.layers_list)
        panel.pack_start(scroll,True,True,0);return panel

    def make_captions_panel(self):
        panel=row_box();panel.set_border_width(12)
        generation=row_box(False,4)
        self.generate_button=action('Generate captions',self.generate);self.generate_button.get_style_context().add_class('suggested-action')
        self.generate_button.set_tooltip_text('Generate editable captions from speech. Defaults: Small model and automatic language detection.')
        generation.pack_start(self.generate_button,True,True,0)
        self.caption_settings_button=ui_theme.icon_button('preferences-system-symbolic',self.configure_generation,'Caption settings',style='quiet')
        generation.pack_start(self.caption_settings_button,False,False,0);panel.pack_start(generation,False,False,0)
        imports=row_box(False);imports.pack_start(action('Import SRT',self.import_srt),True,True,0);imports.pack_start(action('Export SRT',self.export_srt),True,True,0)
        panel.pack_start(imports,False,False,0)
        editing=row_box(False);editing.pack_start(action('Add caption',self.add_caption),True,True,0);editing.pack_start(action('Caption style',self.edit_caption_style),True,True,0)
        panel.pack_start(editing,False,False,0)
        hint=caption('Select a caption to correct its words or timing. Speech recognition runs on your computer.')
        hint.get_style_context().add_class('dim-label');panel.pack_start(hint,False,False,0)
        self.captions_list=Gtk.ListBox();self.captions_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll=Gtk.ScrolledWindow();scroll.set_policy(Gtk.PolicyType.NEVER,Gtk.PolicyType.AUTOMATIC);scroll.add(self.captions_list)
        panel.pack_start(scroll,True,True,0);return panel

    def refresh(self):
        for kind,listing in [('layers',self.layers_list),('captions',self.captions_list)]:
            for child in listing.get_children():listing.remove(child)
            for index,item in enumerate(self.owner.project.get(kind,[])):
                content=row_box();content.set_border_width(7)
                text=item.get('text') or Path(item.get('path','')).name
                name=Gtk.Label(label=text.replace('\n',' '),xalign=0);name.set_ellipsize(Pango.EllipsizeMode.END);name.set_max_width_chars(24)
                content.pack_start(name,False,False,0)
                time=Gtk.Label(label=f'{item["start"]:.2f} – {item["end"]:.2f} s',xalign=0);time.get_style_context().add_class('dim-label');content.pack_start(time,False,False,0)
                widget=Gtk.Button();widget.add(content);widget.set_tooltip_text(text)
                widget.connect('clicked',lambda _,k=kind,i=index:self.select(k,i,edit=True))
                listing.add(widget)
            listing.show_all()
        self.update_overlay_controls()

    def update_overlay_controls(self):
        w=self.owner
        item=w.selected_item()
        visible=bool(w.selected and w.selected[0]=='layers' and item and item.get('kind') in ('image','video'))
        if visible:
            self.syncing_opacity=True;self.opacity.set_value(item.get('opacity',1)*100);self.syncing_opacity=False
            for child in self.overlay_controls.get_children():child.show_all()
            self.overlay_controls.show()
        else:self.overlay_controls.hide()

    def opacity_begin(self,*_):
        self.opacity_dragging=True;self.opacity_checkpointed=False;return False

    def opacity_end(self,*_):
        self.opacity_dragging=False;self.opacity_checkpointed=False;return False

    def opacity_changed(self,widget):
        w=self.owner
        if self.syncing_opacity or w.busy:return
        item=w.selected_item()
        if not item or not w.selected or w.selected[0]!='layers' or item.get('kind') not in ('image','video'):return
        value=widget.get_value()/100
        if abs(value-item.get('opacity',1))<.0001:return
        if not self.opacity_dragging or not self.opacity_checkpointed:
            w.checkpoint();self.opacity_checkpointed=True
        at=w.playhead;item['opacity']=value;w.changed();w.seek(at)

    def overlay_full_video(self):
        w=self.owner;item=w.selected_item();duration=model.project_duration(w.project)
        if w.busy or not duration or not item or not w.selected or w.selected[0]!='layers' or item.get('kind') not in ('image','video'):return
        if item['start']==0 and item['end']==duration:return
        at=w.playhead;w.checkpoint();item['start']=0;item['end']=duration;w.changed();w.seek(at)
        w.set_status('Overlay now covers the full video. Use the opacity slider to make it more subtle.')

    def interval(self):
        duration=model.project_duration(self.owner.project);start=min(self.owner.playhead,max(0,duration-.04))
        return start,min(start+3,duration) if duration>0 else start+3

    def select(self,kind,index,edit=False):
        w=self.owner;w.select(kind,index,seek=False);w.media_stack.set_visible_child_name('Layers' if kind=='layers' else 'Captions')
        item=w.project[kind][index]
        if not item['start']<=w.playhead<item['end']:w.seek(item['start'])
        if edit:w.open_properties('Text' if kind=='captions' or item.get('kind')=='text' else 'Picture')

    def append(self,kind,item):
        w=self.owner;w.checkpoint();w.project[kind].append(item);w.selected=(kind,len(w.project[kind])-1);w.selection={(kind,item['id'])}
        if item.get('kind') in ('image','video') and not any(a['path']==item['path'] and a['kind']==item['kind'] for a in w.project['assets']):
            asset=model.new_asset(item,item['kind'])
            if item['kind']=='video':asset['source_fps']=engine.probe(item['path'])['fps']
            w.project['assets'].append(asset)
        w.changed();w.seek(item['start']);w.media_stack.set_visible_child_name('Layers' if kind=='layers' else 'Captions')
        w.reveal_timeline_selection()
        w.open_properties('Text' if kind=='captions' or item.get('kind')=='text' else 'Picture')

    def add_text(self):
        if self.owner.busy:return
        start,end=self.interval();self.append('layers',model.new_text('Your title',start,end))

    def add_caption(self):
        if self.owner.busy:return
        start,end=self.interval();self.append('captions',model.new_caption('Your caption',start,end))

    def import_visual(self,kind):
        from .app import file_chooser
        w=self.owner
        if w.busy:return
        dialog=file_chooser(w,'Add image or logo' if kind=='image' else 'Add overlay video',Gtk.FileChooserAction.OPEN,'Add layer')
        filt=Gtk.FileFilter();filt.set_name('Images' if kind=='image' else 'Videos');filt.add_mime_type('image/*' if kind=='image' else 'video/*');dialog.add_filter(filt)
        path=dialog.get_filename() if dialog.run()==Gtk.ResponseType.ACCEPT else None;dialog.destroy()
        if not path:return
        try:
            start,end=self.interval()
            if kind=='image':
                image=GdkPixbuf.Pixbuf.new_from_file(path)
                metadata={'width':image.get_width(),'height':image.get_height()}
            else:metadata=engine.probe(path)
            if kind=='video':end=min(end,start+metadata['duration'])
            item=model.new_visual(path,kind,start,end,metadata)
            ratio=metadata.get('width',1)/max(1,metadata.get('height',1))
            item['height']=min(.8,item['width']*w.project['width']/w.project['height']/ratio)
            self.load_asset(item);self.append('layers',item)
        except Exception as e:w.error(str(e))

    def load_asset(self,item):
        if item.get('kind') not in ('image','video'):return
        path=item['path']
        key=item['id'] if item['kind']=='video' else path
        if key in self.assets:return
        w=self.owner
        def finish(target,error):
            if w.closed:return False
            if not error:
                try:self.assets[key]=GdkPixbuf.Pixbuf.new_from_file(target);w.canvas.queue_draw()
                except Exception:pass
            return False
        def work():
            try:
                if item['kind']=='image':target=path
                else:
                    target=str(w.temp/('layer-'+item['id']+'.png'));engine.thumbnail(path,target,at=item.get('in',0),width=640,height=640)
                error=None
            except Exception as e:target=None;error=e
            GLib.idle_add(finish,target,error)
        w.pool.submit(work)

    def show_properties(self):
        self.stop_sound_preview()
        w=self.owner;item=w.selected_item();kind=w.selected[0];self.controls={};w.fields={};w.property_pages={}
        for child in w.inspector.get_children():w.inspector.remove(child)
        w.context_title.set_text(item.get('text','').replace('\n',' ')[:45] or Path(item.get('path','')).name)
        for button in (w.edit_button,w.apply_button,w.duplicate_button,w.remove_button):button.set_sensitive(True)
        w.picture_button.set_sensitive(kind=='layers');w.sound_button.set_sensitive(True);w.split_button.set_sensitive(kind=='layers')
        title=Gtk.Label(label='Caption' if kind=='captions' else 'Text' if item['kind']=='text' else 'Visual layer',xalign=0)
        title.get_style_context().add_class('section');w.inspector.pack_start(title,False,False,0)
        stack=Gtk.Stack();stack.set_hhomogeneous(False);stack.set_vhomogeneous(False);w.properties_stack=stack
        tab_slot=row_box();w.inspector.pack_start(tab_slot,False,False,0);w.inspector.pack_start(stack,True,True,0)
        def page(name):
            box=row_box();box.set_border_width(12);stack.add_titled(box,name,name);w.property_pages[name]=len(w.property_pages);return box
        timing=page('Timing');self.spin(timing,'start','Appears at (s)',item.get('start',0),0,86400,.05);self.spin(timing,'end','Disappears at (s)',item.get('end',3),.04,86400,.05)
        if kind=='layers' and item['kind']=='video':
            self.spin(timing,'in','Source start (s)',item.get('in',0),0,max(0,item['duration']-.001),.05)
            self.spin(timing,'speed','Playback speed (×)',item.get('speed',1),.25,4,.05)
            timing.pack_start(caption('Overlay videos are silent. Their last frame holds if the layer lasts longer than the source.'),False,False,0)
        if kind=='captions' or item['kind']=='text':
            text_page=page('Text');self.text=Gtk.TextView();self.text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR);self.text.get_buffer().set_text(item['text'])
            scroll=Gtk.ScrolledWindow();scroll.set_size_request(320,110);scroll.add(self.text);text_page.pack_start(scroll,False,False,0)
            editor=self.style_controls(text_page,item if kind=='layers' else w.project['caption_style'])
            editor.set_sample(item['text'])
            self.text.get_buffer().connect('changed',lambda buffer:editor.set_sample(buffer.get_text(buffer.get_start_iter(),buffer.get_end_iter(),True)))
            if kind=='captions':text_page.pack_start(caption('Appearance applies to all captions. Words and timing change only for this caption.'),False,False,0)
        if kind=='layers':
            picture=page('Picture')
            if item['kind'] in ('image','video'):
                self.fit=Gtk.ComboBoxText();self.fit.append('fit','Fit inside the layer');self.fit.append('fill','Fill and crop to the layer');self.fit.set_active_id(item.get('fit','fit'));picture.pack_start(self.fit,False,False,0)
            for key,title,value,low,high,step,mult in [('x','Horizontal (%)',item['x'],0,100,1,100),('y','Vertical (%)',item['y'],0,100,1,100),('width','Width (%)',item['width'],.1,200,1,100),('height','Height (%)',item['height'],.1,200,1,100),('rotation','Rotation (°)',item.get('rotation',0),-360,360,1,1),('opacity','Opacity (%)',item.get('opacity',1),0,100,1,100)]:
                self.spin(picture,key,title,value*mult,low,high,step,mult)
            ordering=row_box(False);ordering.pack_start(action('Send backward',lambda:self.reorder(-1)),True,True,0);ordering.pack_start(action('Bring forward',lambda:self.reorder(1)),True,True,0);picture.pack_start(ordering,False,False,0)
        animate=page('Animate')
        self.animation_editor=AnimationEditor(item.get('animation'),text=kind=='captions' or item.get('kind')=='text',
                                             match_id=item.get('match_id','') if kind=='layers' and item.get('kind') in ('text','image') else None)
        animate.pack_start(self.animation_editor,False,False,0)
        self.all_caption_animation=None
        if kind=='captions':
            self.all_caption_animation=Gtk.CheckButton(label='Apply animation to all captions')
            animate.pack_start(self.all_caption_animation,False,False,0)
        sound=page('Sound');self.sound_editor=SoundEditor(w,item.get('sound_effect'));sound.pack_start(self.sound_editor,False,False,0)
        tab_slot.pack_start(page_tabs(stack),False,False,0)
        w.inspector.show_all();stack.set_visible_child_name('Timing')

    def stop_sound_preview(self):
        if hasattr(self,'sound_editor'):self.sound_editor.stop_preview()

    def spin(self,parent,key,title,value,low,high,step=.1,mult=1):
        line=row_box(False);label=Gtk.Label(label=title,xalign=0);line.pack_start(label,True,True,0)
        spin=Gtk.SpinButton.new_with_range(low,high,step);spin.set_digits(2 if step<1 else 0);spin.set_width_chars(5);spin.set_value(value)
        line.pack_end(spin,False,False,0);parent.pack_start(line,False,False,0);self.owner.fields[key]=(spin,mult);return spin

    def style_controls(self,parent,style):
        from .text_style_ui import TextStyleEditor
        self.style_editor=TextStyleEditor(parent,style)
        # Keep these aliases for the existing editor controls and native checks.
        for key in ('font','bold','italic','colors','outline','align'):
            setattr(self,key,getattr(self.style_editor,key))
        return self.style_editor

    def read_style(self,item):
        return self.style_editor.read_into(item)

    def apply(self):
        w=self.owner;kind,index=w.selected;item=copy.deepcopy(w.project[kind][index])
        for key,(spin,mult) in w.fields.items():item[key]=spin.get_value()/mult
        if kind=='layers' and item['kind'] in ('image','video'):item['fit']=self.fit.get_active_id()
        if kind=='captions' or item['kind']=='text':
            buffer=self.text.get_buffer();item['text']=buffer.get_text(buffer.get_start_iter(),buffer.get_end_iter(),True)
            if kind=='layers':self.read_style(item)
        item['animation']=self.animation_editor.read();item['sound_effect']=self.sound_editor.read()
        if self.animation_editor.match_id is not None:item['match_id']=self.animation_editor.match_id.get_text().strip()
        candidate=copy.deepcopy(w.project);candidate[kind][index]=item
        if kind=='captions' and self.all_caption_animation.get_active():
            candidate['caption_style']['animation']=copy.deepcopy(item['animation'])
            for cue in candidate['captions']:cue['animation']=copy.deepcopy(item['animation'])
        if kind=='captions':self.read_style(candidate['caption_style'])
        try:model.validate_project(candidate,check_files=False)
        except ValueError as e:w.error(str(e));return
        self.sound_editor.stop_preview();w.checkpoint();w.project=candidate;w.properties_popover.popdown()
        if item.get('kind')=='video':self.assets.pop(item['id'],None);self.load_asset(item)
        w.changed();w.seek(item['start'])

    def reorder(self,direction):
        w=self.owner
        if not w.selected or w.selected[0]!='layers':return
        index=w.selected[1];target=index+direction
        if not 0<=target<len(w.project['layers']):return
        w.checkpoint();layer=w.project['layers'].pop(index);w.project['layers'].insert(target,layer);w.selected=('layers',target);w.changed()

    def edit_caption_style(self):
        w=self.owner
        if w.busy:return
        saved_fields=w.fields;dialog=Gtk.Dialog(title='Caption appearance',transient_for=w,modal=True)
        dialog.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Apply',Gtk.ResponseType.OK)
        content=dialog.get_content_area();content.set_border_width(16);content.set_spacing(8)
        scroll=Gtk.ScrolledWindow();scroll.set_policy(Gtk.PolicyType.NEVER,Gtk.PolicyType.AUTOMATIC)
        scroll.set_max_content_height(460);scroll.set_propagate_natural_height(True)
        content.pack_start(scroll,True,True,0);body=row_box();body.set_border_width(6);scroll.add(body)
        style=copy.deepcopy(w.project['caption_style']);editor=self.style_controls(body,style)
        if w.project.get('captions'):editor.set_sample(w.project['captions'][0]['text'])
        self.spin(body,'y','Vertical position (%)',style['y']*100,0,100,1,100)
        self.spin(body,'width','Caption width (%)',style['width']*100,5,100,1,100)
        dialog.show_all();response=dialog.run()
        if response==Gtk.ResponseType.OK:
            editor.read_into(style)
            for key in ('y','width'):spin,mult=w.fields[key];style[key]=spin.get_value()/mult
            candidate=copy.deepcopy(w.project);candidate['caption_style']=style
            try:model.validate_project(candidate,check_files=False);w.checkpoint();w.project=candidate;w.changed()
            except ValueError as e:w.error(str(e))
        dialog.destroy();w.fields=saved_fields;w.show_inspector()

    def import_srt(self):
        from .app import file_chooser
        from . import captions
        w=self.owner
        if w.busy:return
        dialog=file_chooser(w,'Import subtitles',Gtk.FileChooserAction.OPEN,'Import SRT')
        filt=Gtk.FileFilter();filt.set_name('SubRip subtitles');filt.add_pattern('*.srt');filt.add_pattern('*.SRT');dialog.add_filter(filt)
        path=dialog.get_filename() if dialog.run()==Gtk.ResponseType.ACCEPT else None;dialog.destroy()
        if not path:return
        try:items=captions.load_srt(path)
        except Exception as e:w.error(str(e));return
        self.replace_captions(items)

    def replace_captions(self,items):
        w=self.owner
        if w.project['captions']:
            dialog=Gtk.MessageDialog(transient_for=w,modal=True,message_type=Gtk.MessageType.QUESTION,buttons=Gtk.ButtonsType.OK_CANCEL,text='Replace existing captions?')
            dialog.format_secondary_text('You can undo this replacement.');answer=dialog.run();dialog.destroy()
            if answer!=Gtk.ResponseType.OK:return False
        w.checkpoint();w.project['captions']=items;w.selected=('captions',0) if items else None;w.selection={('captions',items[0]['id'])} if items else set();w.changed();w.media_stack.set_visible_child_name('Captions')
        w.reveal_timeline_selection()
        w.set_status(f'Added {len(items)} editable captions.')
        return True

    def export_srt(self):
        from .app import file_chooser
        from . import captions
        w=self.owner
        if not w.project['captions']:w.set_status('Add or generate captions first.');return
        dialog=file_chooser(w,'Export subtitles',Gtk.FileChooserAction.SAVE,'Export SRT');dialog.set_current_name('Captions.srt');dialog.set_do_overwrite_confirmation(True)
        path=dialog.get_filename() if dialog.run()==Gtk.ResponseType.ACCEPT else None;dialog.destroy()
        if not path:return
        if not path.lower().endswith('.srt'):
            path+='.srt'
            if Path(path).exists() and not w.confirm_replace(path):return
        try:
            snapshot=copy.deepcopy(w.project)
            if w.project_path:snapshot['_project_path']=w.project_path
            captions.save_srt(snapshot,path);w.set_status(f'Subtitles exported: {path}')
        except Exception as e:w.error(str(e))

    def generation_settings(self):
        saved=self.owner.preferences.get('caption_generation',{})
        if not isinstance(saved,dict):saved={}
        language=saved.get('language')
        language=language.strip().lower() if isinstance(language,str) else None
        return {'source':saved.get('source') if saved.get('source') in ('video','mix') else 'video',
                'model_name':saved.get('model_name') if saved.get('model_name') in ('tiny','small') else 'small',
                'language':language if language and re.fullmatch(r'[a-z]{2,3}',language) else None}

    def configure_generation(self):
        from . import captions
        w=self.owner
        if w.busy:return
        settings=self.generation_settings()
        dialog=Gtk.Dialog(title='Caption settings',transient_for=w,modal=True);dialog.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Apply',Gtk.ResponseType.OK)
        content=dialog.get_content_area();content.set_border_width(18);content.set_spacing(12)
        content.pack_start(caption('Speech recognition runs locally. The first use downloads a speech model and supporting software; later use works offline.'),False,False,0)
        source=Gtk.ComboBoxText();source.append('video','Speech in the original video clips');source.append('mix','Full mix · include soundtracks');source.set_active_id(settings['source']);content.pack_start(source,False,False,0)
        choice=Gtk.ComboBoxText();choice.append('small','Small · better accuracy, slower');choice.append('tiny','Tiny · quick, lower accuracy');choice.set_active_id(settings['model_name']);content.pack_start(choice,False,False,0)
        download=caption('')
        def model_status(*_):
            status=captions.runtime_status(model=choice.get_active_id())
            download.set_text('Model installed · recognition works offline.' if status['ready'] else f'First download: about {status["download_mb"]} MB for the model, plus speech libraries. No footage is uploaded.')
        choice.connect('changed',model_status);model_status();content.pack_start(download,False,False,0)
        language=Gtk.Entry();language.set_placeholder_text('Language code · leave empty to detect');language.set_tooltip_text('Examples: en, hi, ml, ta, ar. Empty detects the language.');language.set_text(settings['language'] or '');content.pack_start(language,False,False,0)
        message=caption('Apply saves these settings for the next time you click Generate captions.');content.pack_start(message,False,False,0)
        dialog.show_all()
        while dialog.run()==Gtk.ResponseType.OK:
            value=language.get_text().strip().lower() or None
            if value and not re.fullmatch(r'[a-z]{2,3}',value):
                message.set_text('Use a language code such as en, hi or ml, or leave it empty to detect.');language.grab_focus();continue
            settings={'source':source.get_active_id(),'model_name':choice.get_active_id(),'language':value}
            try:w.preferences.set('caption_generation',settings)
            except OSError as error:message.set_text('Could not save caption settings: '+str(error));continue
            w.set_status('Caption settings saved. Click Generate captions to start.');break
        dialog.destroy()

    def generate(self):
        self.generate_job(**self.generation_settings())

    def generate_job(self,source='video',model_name='small',language=None,runtime_dir=None):
        from . import captions
        w=self.owner
        if w.busy:return
        if not w.project['clips']:w.set_status('Add videos before generating captions.');return
        snapshot=copy.deepcopy(w.project);w.canvas.pause();w.set_busy(True);w.cancel_event=threading.Event()
        if w.project_path:snapshot['_project_path']=w.project_path
        progress_dialog=Gtk.Dialog(title='Generating captions',transient_for=w,modal=True);progress_dialog.add_button('Cancel',Gtk.ResponseType.CANCEL)
        content=progress_dialog.get_content_area();content.set_border_width(18);info=caption('Preparing local speech recognition with '+model_name.title()+'…');content.pack_start(info,False,False,0)
        bar=Gtk.ProgressBar();content.pack_start(bar,False,False,12)
        progress_dialog.connect('response',lambda *_:w.cancel_event.set());progress_dialog.connect('delete-event',lambda *_:(w.cancel_event.set() or True));progress_dialog.show_all()
        cancel=w.cancel_event;audio=w.temp/'speech-input.wav';active={'value':True}
        def update(value,message):
            if not active['value']:return False
            info.set_text(message);bar.set_fraction(max(0,min(1,value)));return False
        def progress(value,message):GLib.idle_add(update,value,message)
        def finish(result,error):
            active['value']=False;progress_dialog.destroy();w.set_busy(False);w.cancel_event=None;audio.unlink(missing_ok=True)
            if w.closed:return False
            if error or cancel.is_set():
                if cancel.is_set():w.set_status('Caption generation cancelled. Existing captions are unchanged.')
                else:w.error(str(error))
            else:
                if self.replace_captions(result['captions']):
                    w.set_status(f'Generated {len(result["captions"])} captions · language {result.get("language","auto")}. Select a caption to correct it.' if result['captions'] else 'No speech was detected. Try the full mix or another language/model.')
            return False
        def work():
            try:
                captions.setup_transcription(runtime_dir=runtime_dir,model=model_name,progress=lambda v,m:progress(v*.35,m),cancel=cancel)
                engine.render_audio(snapshot,audio,source=source,progress=lambda v,m:progress(.35+v*.15,m),cancel=cancel)
                result=captions.generate_from_audio(audio,runtime_dir=runtime_dir,model=model_name,language=language,progress=lambda v,m:progress(.5+v*.5,m),cancel=cancel);error=None
            except Exception as e:result=None;error=e
            GLib.idle_add(finish,result,error)
        w.pool.submit(work)

    def canvas_press(self,event):
        from . import visuals
        w=self.owner
        if w.busy or event.button!=1:return False
        if w.canvas.composited:
            at=w.playhead;w.canvas.stop();w.preview_key=None;w.canvas.composited=False;w.show_selected_still();w.seek(at,source=True)
        rect=w.canvas.canvas_rect
        if not rect:return False
        for index in range(len(w.project.get('layers',[]))-1,-1,-1):
            item=w.project['layers'][index]
            if not item['start']<=w.playhead<item['end']:continue
            x,y,width,height=visuals.bounds(w.project,item,rect)
            import math
            angle=-math.radians(item.get('rotation',0));dx=event.x-x-width/2;dy=event.y-y-height/2
            px=dx*math.cos(angle)-dy*math.sin(angle);py=dx*math.sin(angle)+dy*math.cos(angle)
            if abs(px)<=width/2 and abs(py)<=height/2:
                w.canvas.pause();w.select('layers',index,seek=False);w.media_stack.set_visible_child_name('Layers')
                self.drag={'index':index,'x':event.x,'y':event.y,'start_x':item['x'],'start_y':item['y'],'width':rect[2],'height':rect[3],'saved':False}
                w.canvas.queue_draw();return True
        return False

    def canvas_motion(self,event):
        if not self.drag:return False
        w=self.owner;d=self.drag
        if not d['saved']:w.checkpoint();d['saved']=True
        item=w.project['layers'][d['index']];item['x']=max(0,min(1,d['start_x']+(event.x-d['x'])/d['width']));item['y']=max(0,min(1,d['start_y']+(event.y-d['y'])/d['height']))
        w.preview_key=None;w.canvas.composited=False;w.canvas.queue_draw();return True

    def canvas_release(self,event):
        if not self.drag:return False
        d=self.drag;self.drag=None
        if d['saved']:
            at=self.owner.playhead;self.owner.changed();self.owner.seek(at)
        return True
