"""Native layer and caption tools attached to the main editor."""
import copy
import threading
from pathlib import Path
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk,Gdk,GLib,Pango,GdkPixbuf
from . import model,engine,ui_theme


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
        imports=row_box(False);imports.pack_start(action('Add image',lambda:self.import_visual('image')),True,True,0)
        imports.pack_start(action('Overlay video',lambda:self.import_visual('video')),True,True,0);panel.pack_start(imports,False,False,0)
        hint=caption('Drag a layer on the canvas to position it. Drag its timeline edges to set when it appears.')
        hint.get_style_context().add_class('dim-label');panel.pack_start(hint,False,False,0)
        self.layers_list=Gtk.ListBox();self.layers_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll=Gtk.ScrolledWindow();scroll.set_policy(Gtk.PolicyType.NEVER,Gtk.PolicyType.AUTOMATIC);scroll.add(self.layers_list)
        panel.pack_start(scroll,True,True,0);return panel

    def make_captions_panel(self):
        panel=row_box();panel.set_border_width(12)
        generate=action('Generate captions from speech',self.generate);generate.get_style_context().add_class('suggested-action');panel.pack_start(generate,False,False,0)
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

    def interval(self):
        duration=model.project_duration(self.owner.project);start=min(self.owner.playhead,max(0,duration-.04))
        return start,min(start+3,duration) if duration>0 else start+3

    def select(self,kind,index,edit=False):
        w=self.owner;w.select(kind,index,seek=False);w.media_stack.set_visible_child_name('Layers' if kind=='layers' else 'Captions')
        item=w.project[kind][index]
        if not item['start']<=w.playhead<item['end']:w.seek(item['start'])
        if edit:w.open_properties('Timing')

    def append(self,kind,item):
        w=self.owner;w.checkpoint();w.project[kind].append(item);w.selected=(kind,len(w.project[kind])-1)
        w.changed();w.seek(item['start']);w.media_stack.set_visible_child_name('Layers' if kind=='layers' else 'Captions')
        w.reveal_timeline_selection()
        w.open_properties('Timing')

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
        w=self.owner;item=w.selected_item();kind=w.selected[0];self.controls={};w.fields={};w.property_pages={}
        for child in w.inspector.get_children():w.inspector.remove(child)
        w.context_title.set_text(item.get('text','').replace('\n',' ')[:45] or Path(item.get('path','')).name)
        for button in (w.edit_button,w.apply_button,w.duplicate_button,w.remove_button):button.set_sensitive(True)
        w.picture_button.set_sensitive(kind=='layers');w.sound_button.set_sensitive(False);w.split_button.set_sensitive(kind=='layers')
        title=Gtk.Label(label='Caption' if kind=='captions' else 'Text' if item['kind']=='text' else 'Visual layer',xalign=0)
        title.get_style_context().add_class('section');w.inspector.pack_start(title,False,False,0)
        stack=Gtk.Stack();stack.set_hhomogeneous(False);stack.set_vhomogeneous(False);w.properties_stack=stack
        switch=Gtk.StackSwitcher();switch.set_stack(stack);w.inspector.pack_start(switch,False,False,0);w.inspector.pack_start(stack,True,True,0)
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
            if kind=='layers':self.style_controls(text_page,item)
            else:text_page.pack_start(caption('Caption appearance is shared. Use Caption style in the sidebar.'),False,False,0)
        if kind=='layers':
            picture=page('Picture')
            if item['kind'] in ('image','video'):
                self.fit=Gtk.ComboBoxText();self.fit.append('fit','Fit inside the layer');self.fit.append('fill','Fill and crop to the layer');self.fit.set_active_id(item.get('fit','fit'));picture.pack_start(self.fit,False,False,0)
            for key,title,value,low,high,step,mult in [('x','Horizontal (%)',item['x'],0,100,1,100),('y','Vertical (%)',item['y'],0,100,1,100),('width','Width (%)',item['width'],.1,200,1,100),('height','Height (%)',item['height'],.1,200,1,100),('rotation','Rotation (°)',item.get('rotation',0),-360,360,1,1),('opacity','Opacity (%)',item.get('opacity',1),0,100,1,100)]:
                self.spin(picture,key,title,value*mult,low,high,step,mult)
            ordering=row_box(False);ordering.pack_start(action('Send backward',lambda:self.reorder(-1)),True,True,0);ordering.pack_start(action('Bring forward',lambda:self.reorder(1)),True,True,0);picture.pack_start(ordering,False,False,0)
        w.inspector.show_all();stack.set_visible_child_name('Timing')

    def spin(self,parent,key,title,value,low,high,step=.1,mult=1):
        line=row_box(False);label=Gtk.Label(label=title,xalign=0);line.pack_start(label,True,True,0)
        spin=Gtk.SpinButton.new_with_range(low,high,step);spin.set_digits(2 if step<1 else 0);spin.set_width_chars(5);spin.set_value(value)
        line.pack_end(spin,False,False,0);parent.pack_start(line,False,False,0);self.owner.fields[key]=(spin,mult);return spin

    def style_controls(self,parent,style):
        from .visuals import rgba
        self.font=Gtk.FontButton();self.font.set_font_name(f'{style.get("font_family","Sans")} {style.get("font_size",48)}');parent.pack_start(self.font,False,False,0)
        self.bold=Gtk.CheckButton(label='Bold');self.bold.set_active(style.get('bold',False));self.italic=Gtk.CheckButton(label='Italic');self.italic.set_active(style.get('italic',False))
        row=row_box(False);row.pack_start(self.bold,True,True,0);row.pack_start(self.italic,True,True,0);parent.pack_start(row,False,False,0)
        self.colors={}
        for key,title,default in [('color','Text color','#ffffff'),('background','Background','#00000000'),('outline_color','Outline','#000000')]:
            line=row_box(False);line.pack_start(Gtk.Label(label=title,xalign=0),True,True,0)
            # GTK 3 does not parse #RRGGBBAA. Decode explicitly to retain alpha
            # when reopening/applying colors saved by the previous edit.
            color=Gdk.RGBA(*rgba(style.get(key,default)))
            picker=Gtk.ColorButton.new_with_rgba(color);picker.set_use_alpha(True);line.pack_end(picker,False,False,0);parent.pack_start(line,False,False,0);self.colors[key]=picker
        self.outline=Gtk.SpinButton.new_with_range(0,20,1);self.outline.set_value(style.get('outline_width',2))
        line=row_box(False);line.pack_start(Gtk.Label(label='Outline thickness',xalign=0),True,True,0);line.pack_end(self.outline,False,False,0);parent.pack_start(line,False,False,0)
        self.align=Gtk.ComboBoxText()
        for value in ('left','center','right'):self.align.append(value,value.capitalize())
        self.align.set_active_id(style.get('align','center'));parent.pack_start(self.align,False,False,0)

    def read_style(self,item):
        desc=Pango.FontDescription(self.font.get_font_name());item['font_family']=desc.get_family();item['font_size']=desc.get_size()/Pango.SCALE
        item['bold']=self.bold.get_active();item['italic']=self.italic.get_active();item['align']=self.align.get_active_id();item['outline_width']=self.outline.get_value()
        for key,widget in self.colors.items():
            rgba=widget.get_rgba();values=[round(v*255) for v in (rgba.red,rgba.green,rgba.blue,rgba.alpha)]
            item[key]='#'+''.join(f'{v:02x}' for v in values)

    def apply(self):
        w=self.owner;kind,index=w.selected;item=copy.deepcopy(w.project[kind][index])
        for key,(spin,mult) in w.fields.items():item[key]=spin.get_value()/mult
        if kind=='layers' and item['kind'] in ('image','video'):item['fit']=self.fit.get_active_id()
        if kind=='captions' or item['kind']=='text':
            buffer=self.text.get_buffer();item['text']=buffer.get_text(buffer.get_start_iter(),buffer.get_end_iter(),True)
            if kind=='layers':self.read_style(item)
        candidate=copy.deepcopy(w.project);candidate[kind][index]=item
        try:model.validate_project(candidate,check_files=False)
        except ValueError as e:w.error(str(e));return
        w.checkpoint();w.project=candidate;w.properties_popover.popdown()
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
        style=copy.deepcopy(w.project['caption_style']);self.style_controls(content,style)
        self.spin(content,'y','Vertical position (%)',style['y']*100,0,100,1,100)
        self.spin(content,'width','Caption width (%)',style['width']*100,5,100,1,100)
        dialog.show_all();response=dialog.run()
        if response==Gtk.ResponseType.OK:
            self.read_style(style)
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
        w.checkpoint();w.project['captions']=items;w.selected=('captions',0) if items else None;w.changed();w.media_stack.set_visible_child_name('Captions')
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

    def generate(self):
        from . import captions
        w=self.owner
        if w.busy:return
        if not w.project['clips']:w.set_status('Add videos before generating captions.');return
        dialog=Gtk.Dialog(title='Generate captions locally',transient_for=w,modal=True);dialog.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Generate',Gtk.ResponseType.OK)
        content=dialog.get_content_area();content.set_border_width(18);content.set_spacing(12)
        content.pack_start(caption('Speech recognition runs locally. The first use downloads a speech model and supporting software; later use works offline.'),False,False,0)
        source=Gtk.ComboBoxText();source.append('video','Speech in the original video clips');source.append('mix','Full mix · include soundtracks');source.set_active_id('video');content.pack_start(source,False,False,0)
        choice=Gtk.ComboBoxText();choice.append('tiny','Tiny · quick, lower accuracy');choice.append('small','Small · better accuracy, slower');choice.set_active_id('tiny');content.pack_start(choice,False,False,0)
        download=caption('')
        def model_status(*_):
            status=captions.runtime_status(model=choice.get_active_id())
            download.set_text('Model installed · recognition works offline.' if status['ready'] else f'First download: about {status["download_mb"]} MB for the model, plus speech libraries. No footage is uploaded.')
        choice.connect('changed',model_status);model_status();content.pack_start(download,False,False,0)
        language=Gtk.Entry();language.set_placeholder_text('Language code · leave empty to detect');language.set_tooltip_text('Examples: en, hi, ml, ta, ar. Empty detects the language.');content.pack_start(language,False,False,0)
        content.pack_start(caption('Review generated words and timings before export.'),False,False,0);dialog.show_all();response=dialog.run()
        settings=(source.get_active_id(),choice.get_active_id(),language.get_text().strip() or None);dialog.destroy()
        if response!=Gtk.ResponseType.OK:return
        self.generate_job(*settings)

    def generate_job(self,source='video',model_name='tiny',language=None,runtime_dir=None):
        from . import captions
        w=self.owner;snapshot=copy.deepcopy(w.project);w.canvas.pause();w.set_busy(True);w.cancel_event=threading.Event()
        if w.project_path:snapshot['_project_path']=w.project_path
        progress_dialog=Gtk.Dialog(title='Generating captions',transient_for=w,modal=True);progress_dialog.add_button('Cancel',Gtk.ResponseType.CANCEL)
        content=progress_dialog.get_content_area();content.set_border_width(18);info=caption('Preparing local speech recognition…');content.pack_start(info,False,False,0)
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
            if error:
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
            at=w.playhead;w.canvas.pause();w.preview_key=None;w.canvas.composited=False;w.show_selected_still();w.seek(at)
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
