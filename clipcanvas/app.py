"""ClipCanvas: a small, local-first desktop video maker."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote, urlparse

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango
import cairo

from . import model, engine
from . import __version__
from .player import VideoCanvas

CSS = b'''
* { font-family: "Ubuntu", "DejaVu Sans", sans-serif; font-size: 13px; }
window { background: #f5f5fa; color: #252333; }
list, row, viewport, scrolledwindow { background: #ffffff; color: #252333; }
row:hover { background: #f4eefb; }
button { background: #ffffff; border: 1px solid #e1dfea; border-radius: 9px; box-shadow: none; padding: 8px 13px; color: #353044; }
button:hover { background: #f0eafb; border-color: #bfa5eb; }
button:disabled { color: #a4a0b0; }
button.primary { background: #7a39d8; border-color: #7a39d8; color: white; font-weight: bold; }
button.primary:hover { background: #682bc3; }
button.quiet { background: transparent; border-color: transparent; }
.topbar { background: linear-gradient(110deg, #03a9b7, #6f39da); padding: 12px 18px; }
.topbar label { color: white; }
.topbar button { background: alpha(white, .15); border-color: alpha(white,.3); color: white; }
.topbar button:hover { background: alpha(white,.25); }
.topbar button.primary { background: white; color: #6630bd; }
.topbar button.primary label { color: #6630bd; }
.topbar button.primary:disabled label { color: #aa96c7; }
.topbar entry { background: alpha(white,.13); border-color: transparent; color: white; border-radius: 8px; }
.brand { font-size: 20px; font-weight: bold; }
.heading { font-size: 22px; font-weight: bold; }
.hero { font-size: 34px; font-weight: bold; }
.section { font-weight: bold; font-size: 14px; }
.muted { color: #777082; }
.small { font-size: 11px; }
.panel { background: #ffffff; }
.card { background: white; border: 1px solid #e5e0ee; border-radius: 16px; padding: 22px; }
.asset { border-bottom: 1px solid #f0edf4; padding: 8px; }
.asset:selected { background: #eee4ff; }
.inspector { padding: 16px; }
entry, spinbutton, combobox button { border: 1px solid #e1dfea; border-radius: 7px; background: white; color: #252333; }
entry, spinbutton { padding: 5px; }
notebook header { background: #faf9fc; border-bottom: 1px solid #e6e1ee; }
notebook tab { padding: 9px; }
notebook tab:checked { border-bottom: 2px solid #7a39d8; }
scale trough { background: #e2dcea; border-radius: 6px; min-height: 4px; }
scale highlight { background: #9152e6; }
scale slider { background: white; border: 1px solid #b49bcc; min-width: 13px; min-height: 13px; }
.status { background: #eeebf4; padding: 7px 16px; }
progressbar progress { background: #7a39d8; }
'''

FORMATS = [
    ('YouTube video', 'Landscape · 16:9', 1920, 1080),
    ('Instagram Reel', 'Portrait · 9:16', 1080, 1920),
    ('Square video', 'Social post · 1:1', 1080, 1080),
    ('YouTube 4K', 'Landscape · 16:9', 3840, 2160),
    ('Vertical 4K', 'Portrait · 9:16', 2160, 3840),
]
TRANSITIONS = [('none', 'Cut · no transition'), ('fade', 'Cross dissolve'),
               ('wipeleft', 'Wipe left'), ('slideright', 'Slide right'), ('dissolve', 'Dissolve pixels')]

def media_filters(kind):
    """Keep WAV files visible even when their MIME type is not recognized."""
    primary = Gtk.FileFilter()
    primary.set_name('Video files' if kind == 'clips' else 'Audio files (WAV, MP3, FLAC, and more)')
    primary.add_mime_type('video/*' if kind == 'clips' else 'audio/*')
    filters = [primary]
    if kind == 'audio':
        wav = Gtk.FileFilter()
        wav.set_name('WAV audio (*.wav, *.wave)')
        # GTK patterns are case-sensitive; cover mixed-case extensions as well.
        from itertools import product
        for extension in ('wav', 'wave'):
            for letters in product(*[(c.lower(), c.upper()) for c in extension]):
                pattern = '*.' + ''.join(letters)
                primary.add_pattern(pattern)
                wav.add_pattern(pattern)
        filters.append(wav)
    all_files = Gtk.FileFilter()
    all_files.set_name('All files')
    all_files.add_pattern('*')
    return filters + [all_files]

def label(text, style=None, xalign=0):
    w = Gtk.Label(label=text, xalign=xalign)
    if style:
        w.get_style_context().add_class(style)
    return w

def button(text, callback, style=None, tooltip=None):
    w = Gtk.Button(label=text)
    w.connect('clicked', lambda _: callback())
    if style:
        w.get_style_context().add_class(style)
    if tooltip:
        w.set_tooltip_text(tooltip)
    return w

def box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=0):
    w = Gtk.Box(orientation=orientation, spacing=spacing)
    if margin:
        for side in ('top', 'bottom', 'start', 'end'):
            getattr(w, 'set_margin_' + side)(margin)
    return w

def scrolled(child):
    w = Gtk.ScrolledWindow()
    w.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    w.add(child)
    return w

def time_text(seconds):
    seconds = max(0, seconds)
    return f'{int(seconds // 60)}:{seconds % 60:04.1f}'

def rgb(cr, color, alpha=1):
    color = color.lstrip('#')
    cr.set_source_rgba(*(int(color[i:i+2], 16)/255 for i in (0, 2, 4)), alpha)

def rounded(cr, x, y, w, h, radius=8):
    radius = max(0, min(radius, w/2, h/2))
    cr.new_sub_path()
    for cx, cy, angle in [(x+w-radius,y+radius,-90), (x+w-radius,y+h-radius,0),
                          (x+radius,y+h-radius,90), (x+radius,y+radius,180)]:
        cr.arc(cx, cy, radius, math.radians(angle), math.radians(angle+90))
    cr.close_path()

def draw_text(cr, text, x, y, color='#51485f', size=12, bold=False):
    rgb(cr, color)
    cr.select_font_face('Ubuntu', cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size)
    cr.move_to(x, y)
    cr.show_text(text)

class FormatArt(Gtk.DrawingArea):
    def __init__(self, width, height):
        super().__init__()
        self.ratio = width/height
        self.set_size_request(180, 145)
        self.connect('draw', self.draw)

    def draw(self, widget, cr):
        a = self.get_allocation()
        w, h = (148, 148/self.ratio) if self.ratio >= 1 else (112*self.ratio, 112)
        x, y = (a.width-w)/2, (a.height-h)/2
        rounded(cr,x+5,y+6,w,h,9); rgb(cr,'#ded4ee'); cr.fill()
        rounded(cr,x,y,w,h,9)
        grad=cairo.LinearGradient(x,y,x+w,y+h)
        grad.add_color_stop_rgb(0,.02,.70,.73); grad.add_color_stop_rgb(1,.54,.22,.86)
        cr.set_source(grad); cr.fill()
        cr.arc(x+w*.75,y+h*.3,min(w,h)*.15,0,math.tau); rgb(cr,'#ffffff',.18); cr.fill()
        cr.move_to(x+w*.15,y+h*.83); cr.line_to(x+w*.45,y+h*.36); cr.line_to(x+w*.78,y+h*.83)
        cr.close_path(); rgb(cr,'#ffffff',.22); cr.fill()
        cr.move_to(x+w*.45,y+h*.36); cr.line_to(x+w*.45,y+h*.64); cr.line_to(x+w*.62,y+h*.50)
        cr.close_path(); rgb(cr,'#ffffff'); cr.fill()

class Timeline(Gtk.DrawingArea):
    def __init__(self, owner):
        super().__init__()
        self.owner=owner; self.scale=28.; self.hit=[]; self.drag=None
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect('draw',self.draw)
        self.connect('button-press-event',self.press)
        self.connect('button-release-event',self.release)
        self.connect('motion-notify-event',self.motion)
        self.set_tooltip_text('Click a clip or soundtrack to edit. Drag clips to reorder; drag soundtracks to change their start time. Click the ruler to seek a prepared preview.')

    def refresh(self):
        duration=model.project_duration(self.owner.project)
        self.set_size_request(max(800,int(duration*self.scale+170)),max(220,145+len(self.owner.project['audio'])*54))
        self.queue_draw()

    def draw(self, widget, cr):
        a=self.get_allocation(); p=self.owner.project; self.hit=[]
        rgb(cr,'#faf9fd'); cr.paint()
        duration=model.project_duration(p)
        step=1 if self.scale>65 else 5 if self.scale>15 else 10
        for sec in range(0, int(max(duration,(a.width-100)/self.scale))+step,step):
            x=100+sec*self.scale
            rgb(cr,'#e5e0ec'); cr.set_line_width(1); cr.move_to(x,30);cr.line_to(x,a.height);cr.stroke()
            draw_text(cr,f'{sec}s',x+3,21,size=10)
        draw_text(cr,'CLIPS',12,68,size=10,bold=True)
        if not p['clips']:
            rounded(cr,105,42,max(300,a.width-130),75); rgb(cr,'#eee9f6');cr.fill()
            draw_text(cr,'Import clips to start your story',128,85,'#857494',14)
        timings=model.timeline(p)
        for i,(clip,timing) in enumerate(zip(p['clips'],timings)):
            x=100+timing['start']*self.scale; y=40; w=max(12,timing['duration']*self.scale-3);h=79
            cr.save(); rounded(cr,x,y,w,h);cr.clip()
            pix=self.owner.thumbs.get(clip['path'])
            if pix:
                factor=h/pix.get_height(); pw=pix.get_width()*factor
                for tx in range(int(x),int(x+w)+int(pw)+1,max(1,int(pw))):
                    cr.save();cr.translate(tx,y);cr.scale(factor,factor);Gdk.cairo_set_source_pixbuf(cr,pix,0,0);cr.paint();cr.restore()
            else:
                rgb(cr,'#b5a0d4');cr.paint()
            rgb(cr,'#21172c',.70);cr.rectangle(x,y+53,w,26);cr.fill()
            draw_text(cr,f'{i+1}  {Path(clip["path"]).name}',x+8,y+71,'#ffffff',11,True)
            cr.restore()
            selected=self.owner.selected==('clips',i)
            rounded(cr,x,y,w,h);rgb(cr,'#8139dd' if selected else '#ffffff');cr.set_line_width(3 if selected else 1);cr.stroke()
            self.hit.append(('clips',i,x,y,w,h))
            if i<len(p['clips'])-1 and clip['transition']!='none':
                tx=100+(timing['start']+timing['duration'])*self.scale-12
                rounded(cr,tx,65,23,25,5);rgb(cr,'#8139dd');cr.fill();draw_text(cr,'↔',tx+4,82,'#ffffff',13)
        for i,audio in enumerate(p['audio']):
            y=139+i*54;x=100+audio['start']*self.scale
            visible=max(0,min(audio['out']-audio['in'],duration-audio['start']))
            w=max(12,visible*self.scale)
            draw_text(cr,f'AUDIO {i+1}',12,y+26,size=10,bold=True)
            cr.save();rounded(cr,x,y,w,42,7);cr.clip();rgb(cr,'#8150c6');cr.paint()
            values=self.owner.waves.get(audio['path'],[])
            if values:
                for xx in range(0,int(w),3):
                    fraction=(audio['in']+(xx/max(w,1))*visible)/max(.01,audio['duration'])
                    amp=values[min(len(values)-1,max(0,int(fraction*len(values))))]
                    hh=max(2,amp*28);rgb(cr,'#c5a7f0',.8);cr.rectangle(x+xx,y+21-hh/2,2,hh);cr.fill()
            rgb(cr,'#592899',.7);cr.rectangle(x,y,w,18);cr.fill();draw_text(cr,Path(audio['path']).name,x+7,y+13,'#ffffff',10)
            cr.restore()
            if self.owner.selected==('audio',i):
                rounded(cr,x,y,w,42,7);rgb(cr,'#3b156e');cr.set_line_width(3);cr.stroke()
            self.hit.append(('audio',i,x,y,w,42))
        px=100+self.owner.playhead*self.scale
        rgb(cr,'#2b2035');cr.set_line_width(2);cr.move_to(px,27);cr.line_to(px,a.height);cr.stroke()
        cr.move_to(px-5,27);cr.line_to(px+5,27);cr.line_to(px,34);cr.close_path();cr.fill()

    def press(self, w, event):
        if event.button!=1:return
        if event.y<34:
            self.owner.seek(max(0,(event.x-100)/self.scale));return True
        for kind,i,x,y,width,height in reversed(self.hit):
            if x<=event.x<=x+width and y<=event.y<=y+height:
                self.owner.select(kind,i)
                self.drag=(kind,i,event.x,self.owner.project[kind][i].get('start',0))
                return True

    def motion(self,w,event):
        if self.drag:
            self.get_window().set_cursor(Gdk.Cursor.new_from_name(self.get_display(),'grabbing'))

    def release(self,w,event):
        if self.get_window():self.get_window().set_cursor(None)
        if not self.drag:return
        kind,i,old_x,start=self.drag;self.drag=None
        if abs(event.x-old_x)<8:return
        self.owner.checkpoint()
        if kind=='audio':
            self.owner.project[kind][i]['start']=round(max(0,min(model.project_duration(self.owner.project)-.1,start+(event.x-old_x)/self.scale)),2)
        else:
            target=i
            for _,j,x,y,width,height in [h for h in self.hit if h[0]=='clips']:
                if event.x>x:target=j
            clips=self.owner.project['clips'];clip=clips.pop(i);clips.insert(target,clip);self.owner.selected=(kind,target)
        self.owner.changed()

class ClipCanvasWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title='ClipCanvas')
        self.set_wmclass('clipcanvas','ClipCanvas')
        self.set_default_size(1380,900);self.set_position(Gtk.WindowPosition.CENTER)
        self.project=model.new_project();self.project_path=None;self.saved=json.dumps(self.project,sort_keys=True)
        self.selected=None;self.history=[];self.future=[];self.thumbs={};self.waves={};self.playhead=0.;self.preview_key=None
        self.temp=Path(tempfile.mkdtemp(prefix='clipcanvas-'));self.pool=ThreadPoolExecutor(max_workers=2)
        self.closed=False;self.busy=False;self.cancel_event=None;self.name_lock=False
        self.connect('delete-event',self.close_request)
        self.connect('key-press-event',self.keypress)
        self.root=box(spacing=0);self.add(self.root)
        self.make_header()
        self.stack=Gtk.Stack();self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.root.pack_start(self.stack,True,True,0)
        self.make_welcome();self.make_editor()
        self.status=label('Local files. Your original videos stay untouched.','muted');self.status.get_style_context().add_class('status')
        self.root.pack_start(self.status,False,False,0)
        self.stack.set_visible_child_name('welcome')
        self.drag_dest_set(Gtk.DestDefaults.ALL,[Gtk.TargetEntry.new('text/uri-list',0,0)],Gdk.DragAction.COPY)
        self.connect('drag-data-received',self.drop_files)
        self.show_all();self.update_title()

    def make_header(self):
        self.header=box(Gtk.Orientation.HORIZONTAL,9);self.header.get_style_context().add_class('topbar')
        self.header.pack_start(label('◈  ClipCanvas','brand'),False,False,8)
        self.header.pack_start(button('New',self.new),False,False,0)
        self.header.pack_start(button('Open',self.open_project),False,False,0)
        self.header.pack_start(button('Save',self.save),False,False,0)
        self.name=Gtk.Entry();self.name.set_text(self.project['name']);self.name.set_max_length(100);self.name.set_width_chars(20)
        self.name.connect('changed',self.name_changed);self.header.pack_start(self.name,True,True,28)
        self.header.pack_start(button('Help',self.help),False,False,0)
        self.preview_button=button('▶  Preview',self.preview)
        self.header.pack_start(self.preview_button,False,False,0)
        self.export_button=button('Export video',self.export,'primary');self.header.pack_start(self.export_button,False,False,0)
        self.root.pack_start(self.header,False,False,0)

    def make_welcome(self):
        outer=box(margin=40);outer.set_valign(Gtk.Align.CENTER)
        outer.pack_start(label('Create something worth watching.','hero',.5),False,False,0)
        outer.pack_start(label('Choose a canvas. Add your clips. Make it yours.','muted',.5),False,False,6)
        cards=box(Gtk.Orientation.HORIZONTAL,16);cards.set_halign(Gtk.Align.CENTER)
        for title,description,width,height in FORMATS[:3]:
            b=Gtk.Button();b.get_style_context().add_class('card');b.set_size_request(260,265)
            content=box(spacing=8);content.pack_start(FormatArt(width,height),True,True,0)
            content.pack_start(label(title,'section',.5),False,False,0)
            content.pack_start(label(description,'muted',.5),False,False,0)
            content.pack_start(label(f'{width} × {height} px','small',.5),False,False,0)
            b.add(content);b.connect('clicked',lambda _,w=width,h=height:self.start_project(w,h));cards.pack_start(b,False,False,0)
        outer.pack_start(cards,False,False,26)
        outer.pack_start(label('No uploads. No account. Just your ideas, on your computer.','muted',.5),False,False,6)
        open_b=button('Open an existing project',self.open_project);open_b.set_halign(Gtk.Align.CENTER);outer.pack_start(open_b,False,False,10)
        self.stack.add_named(outer,'welcome')

    def make_editor(self):
        self.editor=box(spacing=0)
        body=Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        library=box(spacing=0);library.set_size_request(235,-1);library.get_style_context().add_class('panel')
        library_top=box(margin=14)
        library_top.pack_start(label('Your media','section'),False,False,0)
        library_top.pack_start(button('+  Import clips',lambda:self.import_dialog('clips'),'primary'),False,False,0)
        library_top.pack_start(button('♫  Add soundtracks',lambda:self.import_dialog('audio')),False,False,0)
        library_top.pack_start(label('WAV · MP3 · FLAC · AAC · OGG','small'),False,False,0)
        hint=label('Files are added directly from your computer.','muted');hint.set_line_wrap(True);hint.set_max_width_chars(26)
        library_top.pack_start(hint,False,False,4);library.pack_start(library_top,False,False,0)
        self.assets=Gtk.ListBox();self.assets.set_selection_mode(Gtk.SelectionMode.NONE)
        library.pack_start(scrolled(self.assets),True,True,0)
        body.pack1(library,False,False)
        central=Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        center=box(spacing=0)
        canvas_toolbar=box(Gtk.Orientation.HORIZONTAL,8,12)
        self.format_combo=Gtk.ComboBoxText()
        for title,desc,w,h in FORMATS:self.format_combo.append(f'{w}x{h}',f'{title}  ·  {w} × {h}')
        self.format_combo.set_active(0);self.format_combo.connect('changed',self.format_changed)
        canvas_toolbar.pack_start(self.format_combo,False,False,0)
        self.fps_combo=Gtk.ComboBoxText()
        for fps in ('24','25','30','50','60'):self.fps_combo.append(fps,f'{fps} fps')
        self.fps_combo.set_active_id('30');self.fps_combo.connect('changed',self.fps_changed)
        canvas_toolbar.pack_end(self.fps_combo,False,False,0)
        center.pack_start(canvas_toolbar,False,False,0)
        self.canvas=VideoCanvas();self.canvas.set_size_request(300,270)
        self.canvas.on_position=self.on_position;self.canvas.on_error=self.playback_error
        self.canvas.on_end=lambda:self.play_button.set_label('▶')
        center.pack_start(self.canvas,True,True,0)
        playback=box(Gtk.Orientation.HORIZONTAL,12,10);playback.set_halign(Gtk.Align.CENTER)
        self.play_button=button('▶',self.toggle_play);playback.pack_start(self.play_button,False,False,0)
        self.clock_label=label('0:00.0 / 0:00.0');playback.pack_start(self.clock_label,False,False,0)
        self.preview_label=label('Preview is prepared locally','muted');playback.pack_start(self.preview_label,False,False,10)
        center.pack_start(playback,False,False,0)
        central.pack1(center,True,False)
        inspector_panel=box(spacing=0);inspector_panel.get_style_context().add_class('panel')
        self.inspector=box();self.inspector.set_size_request(260,-1)
        self.inspector.get_style_context().add_class('panel');self.inspector.get_style_context().add_class('inspector')
        inspector_panel.pack_start(scrolled(self.inspector),True,True,0)
        self.apply_button=button('Apply changes',self.apply_fields,'primary')
        for side in ('start','end','top','bottom'):getattr(self.apply_button,'set_margin_'+side)(12)
        inspector_panel.pack_start(self.apply_button,False,False,0)
        central.pack2(inspector_panel,False,False);central.set_position(780)
        body.pack2(central,True,False);body.set_position(245)
        self.editor.pack_start(body,True,True,0)
        tlbar=box(Gtk.Orientation.HORIZONTAL,8,10)
        tlbar.pack_start(label('Your story','section'),False,False,7)
        self.undo_button=button('↶',self.undo,tooltip='Undo · Ctrl+Z');self.redo_button=button('↷',self.redo,tooltip='Redo · Ctrl+Shift+Z')
        tlbar.pack_start(self.undo_button,False,False,0);tlbar.pack_start(self.redo_button,False,False,0)
        tlbar.pack_start(button('Move left',lambda:self.move(-1)),False,False,0)
        tlbar.pack_start(button('Move right',lambda:self.move(1)),False,False,0)
        tlbar.pack_start(button('Duplicate',self.duplicate),False,False,0)
        tlbar.pack_start(button('Remove',self.remove),False,False,0)
        zoom=Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,6,100,1);zoom.set_value(28);zoom.set_draw_value(False);zoom.set_size_request(120,-1)
        zoom.connect('value-changed',lambda s:self.zoom(s.get_value()))
        tlbar.pack_end(zoom,False,False,0);tlbar.pack_end(label('Zoom','muted'),False,False,0)
        self.editor.pack_start(tlbar,False,False,0)
        self.timeline=Timeline(self);self.timeline.refresh()
        timeline_scroll=Gtk.ScrolledWindow();timeline_scroll.set_policy(Gtk.PolicyType.AUTOMATIC,Gtk.PolicyType.AUTOMATIC)
        timeline_scroll.set_size_request(-1,245);timeline_scroll.add(self.timeline)
        self.editor.pack_start(timeline_scroll,False,True,0)
        self.stack.add_named(self.editor,'editor');self.show_inspector()

    def start_project(self,width,height):
        self.canvas.set_still(None)
        self.project=model.new_project(width,height);self.project_path=None;self.saved=json.dumps(self.project,sort_keys=True)
        self.selected=None;self.history=[];self.future=[];self.preview_key=None;self.playhead=0
        self.sync_project_widgets();self.stack.set_visible_child_name('editor');self.refresh()

    def new(self):
        if self.busy or not self.confirm_discard():return
        self.canvas.stop();self.stack.set_visible_child_name('welcome')

    def name_changed(self,w):
        if self.name_lock:return
        self.project['name']=w.get_text().strip() or 'Untitled video';self.update_title()

    def sync_project_widgets(self):
        self.name_lock=True
        self.name.set_text(self.project.get('name','Untitled video'))
        key=f'{self.project["width"]}x{self.project["height"]}'
        if key not in [f'{x[2]}x{x[3]}' for x in FORMATS]:self.format_combo.append(key,key)
        self.format_combo.set_active_id(key)
        fps=str(self.project['fps'])
        if fps not in ('24','25','30','50','60'):self.fps_combo.append(fps,f'{fps} fps')
        self.fps_combo.set_active_id(fps);self.name_lock=False
        self.canvas.set_aspect(self.project['width'],self.project['height'])

    def format_changed(self,w):
        if self.name_lock or not w.get_active_id():return
        self.checkpoint();self.project['width'],self.project['height']=map(int,w.get_active_id().split('x'))
        self.canvas.set_aspect(self.project['width'],self.project['height']);self.changed()

    def fps_changed(self,w):
        if self.name_lock or not w.get_active_id():return
        self.checkpoint();self.project['fps']=float(w.get_active_id());self.changed()

    def checkpoint(self):
        self.history.append(copy.deepcopy(self.project));self.history=self.history[-60:];self.future=[]

    def changed(self):
        self.canvas.stop();self.preview_key=None;self.playhead=0;self.play_button.set_label('▶')
        self.preview_label.set_text('Preview needs updating');self.refresh();self.show_selected_still()

    def refresh(self):
        self.update_title();self.refresh_assets();self.show_inspector();self.timeline.refresh()
        self.clock_label.set_text(f'{time_text(self.playhead)} / {time_text(model.project_duration(self.project))}')
        self.undo_button.set_sensitive(bool(self.history));self.redo_button.set_sensitive(bool(self.future))

    def update_title(self):
        dirty=json.dumps(self.project,sort_keys=True)!=self.saved
        self.set_title(f'{"• " if dirty else ""}{self.project["name"]} — ClipCanvas')
        ready=bool(self.project['clips']) and not self.busy
        self.preview_button.set_sensitive(ready);self.export_button.set_sensitive(ready)

    def undo(self):
        if not self.history or self.busy:return
        self.future.append(copy.deepcopy(self.project));self.project=self.history.pop();self.selected=None
        self.sync_project_widgets();self.changed()

    def redo(self):
        if not self.future or self.busy:return
        self.history.append(copy.deepcopy(self.project));self.project=self.future.pop();self.selected=None
        self.sync_project_widgets();self.changed()

    def refresh_assets(self):
        for child in self.assets.get_children():self.assets.remove(child)
        for kind in ('clips','audio'):
            for i,item in enumerate(self.project[kind]):
                row=Gtk.ListBoxRow();row.get_style_context().add_class('asset')
                b=Gtk.Button();b.get_style_context().add_class('quiet');content=box(Gtk.Orientation.HORIZONTAL,8)
                if kind=='clips' and item['path'] in self.thumbs:
                    pix=self.thumbs[item['path']].scale_simple(54,34,GdkPixbuf.InterpType.BILINEAR)
                    content.pack_start(Gtk.Image.new_from_pixbuf(pix),False,False,0)
                else:content.pack_start(label('▣' if kind=='clips' else '♫','heading'),False,False,0)
                text=box(spacing=3);name=label(Path(item['path']).name);name.set_ellipsize(Pango.EllipsizeMode.MIDDLE);name.set_max_width_chars(17)
                text.pack_start(name,False,False,0);text.pack_start(label(f'{"Clip" if kind=="clips" else "Audio"} {i+1} · {time_text(item["out"]-item["in"])}','small'),False,False,0)
                content.pack_start(text,True,True,0);b.add(content);b.connect('clicked',lambda _,k=kind,n=i:self.select(k,n));row.add(b);self.assets.add(row)
        self.assets.show_all()

    def select(self,kind,index):
        self.selected=(kind,index);self.show_inspector();self.timeline.queue_draw()
        if kind=='clips':
            self.canvas.pause();self.play_button.set_label('▶')
            if self.preview_key:self.canvas.seek(model.timeline(self.project)[index]['start'])
            else:self.show_selected_still()

    def selected_item(self):
        if not self.selected:return None
        kind,i=self.selected
        if i>=len(self.project[kind]):self.selected=None;return None
        return self.project[kind][i]

    def show_selected_still(self):
        if self.preview_key:return
        item=self.selected_item()
        if self.selected and self.selected[0]=='audio':
            if self.canvas.frame is not None:return
            item=None
        if not item:item=next(iter(self.project['clips']),None)
        if item:
            pix=self.thumbs.get(item['path'])
            if pix:self.canvas.set_still(pix,item['fit'],item.get('position_x',.5),item.get('position_y',.5))
        else:self.canvas.set_still(None)

    def show_inspector(self):
        for child in self.inspector.get_children():self.inspector.remove(child)
        item=self.selected_item()
        self.apply_button.set_sensitive(item is not None)
        if not item:
            self.inspector.pack_start(label('Make it yours','section'),False,False,0)
            hint=label('Select a clip or soundtrack below to adjust its timing, volume, and style.','muted');hint.set_line_wrap(True);hint.set_max_width_chars(26)
            self.inspector.pack_start(hint,False,False,8);self.inspector.show_all();return
        kind,i=self.selected;self.fields={}
        self.inspector.pack_start(label(f'{"Clip" if kind=="clips" else "Soundtrack"} {i+1}','section'),False,False,0)
        title=label(Path(item['path']).name,'muted');title.set_ellipsize(Pango.EllipsizeMode.MIDDLE);title.set_max_width_chars(25)
        self.inspector.pack_start(title,False,False,0)
        def spin(key,title,lo,hi,step=0.1,mult=1,parent=None):
            row=box(Gtk.Orientation.HORIZONTAL,8)
            title_label=label(title,'small');title_label.set_line_wrap(True);title_label.set_max_width_chars(18)
            row.pack_start(title_label,True,True,0)
            w=Gtk.SpinButton.new_with_range(lo,max(hi,lo+.01),step);w.set_digits(2 if step<1 else 0);w.set_value(item.get(key,0)*mult)
            w.set_width_chars(5);w.set_max_width_chars(6)
            row.pack_start(w,False,False,0);(parent or self.inspector).pack_start(row,False,False,0);self.fields[key]=(w,mult)
        if kind=='clips':
            self.transition=Gtk.ComboBoxText()
            for key,title in TRANSITIONS:self.transition.append(key,title)
            self.transition.set_active_id(item['transition'])
            self.inspector.pack_start(label('Transition to next clip','small'),False,False,0);self.inspector.pack_start(self.transition,False,False,0)
            spin('transition_duration','Transition length (seconds)',.05,3,.05)
        spin('in','Source start (seconds)',0,item['duration'])
        spin('out','Source end (seconds)',.04,item['duration'])
        if kind=='audio':spin('start','Start in your video (seconds)',0,max(0,model.project_duration(self.project)-.04))
        spin('volume','Volume (%)',0,200,1,100)
        if kind=='clips':
            self.fit=Gtk.ComboBoxText();self.fit.append('fill','Fill canvas · crop');self.fit.append('fit','Fit whole video · borders');self.fit.set_active_id(item['fit'])
            self.inspector.pack_start(label('Canvas fit','small'),False,False,0);self.inspector.pack_start(self.fit,False,False,0)
            crop=Gtk.Expander(label='Adjust crop position');crop_box=box(spacing=8,margin=4);crop.add(crop_box)
            spin('position_x','Left ↔ right (%)',0,100,1,100,parent=crop_box)
            spin('position_y','Top ↔ bottom (%)',0,100,1,100,parent=crop_box)
            self.inspector.pack_start(crop,False,False,4)
        else:
            spin('fade_in','Fade in (seconds)',0,item['duration'])
            spin('fade_out','Fade out (seconds)',0,item['duration'])
        self.inspector.show_all()

    def apply_fields(self):
        original=self.selected_item()
        if not original:return
        item=copy.deepcopy(original)
        for key,(w,mult) in self.fields.items():item[key]=w.get_value()/mult
        if item['out']-item['in']<.04:
            self.error('The source end must be after the source start.');return
        if self.selected[0]=='clips':item['fit']=self.fit.get_active_id();item['transition']=self.transition.get_active_id()
        self.checkpoint();kind,i=self.selected;self.project[kind][i]=item;self.changed();self.set_status('Changes applied. Press Preview to see and hear the full video.')

    def move(self,amount):
        if not self.selected or self.selected[0]!='clips':return
        _,i=self.selected;j=i+amount
        if not 0<=j<len(self.project['clips']):return
        self.checkpoint();clips=self.project['clips'];clips[i],clips[j]=clips[j],clips[i];self.selected=('clips',j);self.changed()

    def duplicate(self):
        item=self.selected_item()
        if not item:return
        import uuid
        self.checkpoint();kind,i=self.selected;clone=copy.deepcopy(item);clone['id']=str(uuid.uuid4())
        self.project[kind].insert(i+1,clone);self.selected=(kind,i+1);self.changed()

    def remove(self):
        if not self.selected_item():return
        self.checkpoint();kind,i=self.selected;self.project[kind].pop(i);self.selected=None;self.changed()

    def zoom(self,value):
        self.timeline.scale=value;self.timeline.refresh()

    def import_dialog(self,kind):
        dialog=Gtk.FileChooserDialog(title='Import clips' if kind=='clips' else 'Add soundtracks',transient_for=self,action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Add files',Gtk.ResponseType.OK);dialog.set_select_multiple(True)
        for filt in media_filters(kind):dialog.add_filter(filt)
        paths=dialog.get_filenames() if dialog.run()==Gtk.ResponseType.OK else [];dialog.destroy()
        if paths:self.import_paths(paths,kind)

    def drop_files(self,w,context,x,y,data,info,timestamp):
        if self.busy:Gtk.drag_finish(context,False,False,timestamp);return
        paths=[unquote(urlparse(uri).path) for uri in data.get_uris() if urlparse(uri).scheme=='file']
        Gtk.drag_finish(context,bool(paths),False,timestamp)
        if paths:self.import_paths(paths,None)

    def import_paths(self,paths,kind=None):
        if self.busy:return
        self.set_busy(True);self.set_status('Reading local media…')
        def work():
            items=[];errors=[]
            for path in paths:
                try:
                    metadata=engine.probe(path)
                    if metadata['duration']<.04:raise ValueError('Choose a file at least 0.04 seconds long.')
                    actual=kind or ('clips' if metadata['has_video'] else 'audio')
                    if actual=='clips' and not metadata['has_video']:raise ValueError('This file has no video track.')
                    if actual=='audio' and not metadata['has_audio']:raise ValueError('This file has no audio track.')
                    item=model.new_clip(path,metadata) if actual=='clips' else model.new_audio(path,metadata)
                    items.append((actual,item))
                except Exception as e:errors.append(f'{Path(path).name}: {e}')
            GLib.idle_add(self.finish_import,items,errors)
        self.pool.submit(work)

    def finish_import(self,items,errors):
        if self.closed:return False
        self.set_busy(False)
        if items:
            self.checkpoint()
            for kind,item in items:
                self.project[kind].append(item);self.selected=(kind,len(self.project[kind])-1);self.load_asset(item,kind)
            self.stack.set_visible_child_name('editor');self.changed()
            self.set_status(f'Added {len(items)} local file(s). Drag clips to reorder; select one to adjust it.')
        if errors:self.error('\n'.join(errors))
        return False

    def load_asset(self,item,kind):
        path=item['path']
        if (kind=='clips' and path in self.thumbs) or (kind=='audio' and path in self.waves):return
        def work():
            try:
                if kind=='clips':
                    target=self.temp/(hashlib.sha256(path.encode()).hexdigest()+'.jpg')
                    engine.thumbnail(path,str(target),at=item['in'],width=640,height=360)
                    GLib.idle_add(self.asset_ready,path,kind,str(target))
                else:GLib.idle_add(self.asset_ready,path,kind,engine.waveform(path,max_points=600))
            except Exception:pass
        self.pool.submit(work)

    def asset_ready(self,path,kind,data):
        if self.closed:return False
        try:
            if kind=='clips':self.thumbs[path]=GdkPixbuf.Pixbuf.new_from_file(data)
            else:self.waves[path]=data
            self.refresh_assets();self.timeline.queue_draw();self.show_selected_still()
        except Exception:pass
        return False

    def on_position(self,seconds,duration):
        self.playhead=seconds;self.clock_label.set_text(f'{time_text(seconds)} / {time_text(model.project_duration(self.project))}');self.timeline.queue_draw()

    def playback_error(self,message):
        self.play_button.set_label('▶');self.set_status(message)

    def toggle_play(self):
        if self.busy or not self.project['clips']:return
        if not self.preview_key:self.preview();return
        if self.canvas.playing:self.canvas.pause();self.play_button.set_label('▶')
        else:
            if self.playhead>=model.project_duration(self.project)-.1:self.canvas.seek(0)
            self.canvas.play();self.play_button.set_label('Ⅱ')

    def seek(self,seconds):
        if not self.preview_key:self.set_status('Press Preview once to prepare playback with transitions and soundtracks.');return
        self.canvas.seek(min(seconds,max(0,model.project_duration(self.project)-.05)))

    def preview(self):
        if not self.project['clips'] or self.busy:return
        key=hashlib.sha256(json.dumps(self.project,sort_keys=True).encode()).hexdigest()
        target=self.temp/(key+'.mp4')
        if target.exists():self.preview_done(str(target),key);return
        self.render_job(str(target),True,lambda path:self.preview_done(path,key))

    def preview_done(self,path,key):
        self.preview_key=key;self.canvas.load(path,autoplay=True);self.play_button.set_label('Ⅱ')
        self.preview_label.set_text('Preview · export uses original files');self.set_status('Preview ready. Click the timeline ruler to seek.')

    def export(self):
        if self.busy or not self.project['clips']:return
        dialog=Gtk.Dialog(title='Export your video',transient_for=self,modal=True)
        dialog.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Choose save location',Gtk.ResponseType.OK)
        content=dialog.get_content_area();content.set_spacing(12);content.set_border_width(22)
        content.pack_start(label(f'{self.project["width"]} × {self.project["height"]} · {self.project["fps"]:g} fps · MP4','section'),False,False,0)
        quality=Gtk.ComboBoxText()
        for key,text in [('high','High quality · recommended'),('master','Maximum quality · larger file'),('smaller','Smaller file')]:quality.append(key,text)
        quality.set_active_id('high');content.pack_start(quality,False,False,0)
        hint=label('Export uses the original media. MP4 compression can cause\nsmall quality changes; no watermark is added.','muted');content.pack_start(hint,False,False,0)
        dialog.show_all();response=dialog.run();q=quality.get_active_id();dialog.destroy()
        if response!=Gtk.ResponseType.OK:return
        save=Gtk.FileChooserDialog(title='Save finished video',transient_for=self,action=Gtk.FileChooserAction.SAVE)
        save.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Export',Gtk.ResponseType.OK);save.set_do_overwrite_confirmation(True)
        safe_name=''.join(c for c in self.project['name'] if c.isalnum() or c in ' -_').strip() or 'My video'
        save.set_current_name(safe_name+'.mp4')
        path=save.get_filename() if save.run()==Gtk.ResponseType.OK else None;save.destroy()
        if not path:return
        if not path.lower().endswith('.mp4'):
            path+='.mp4'
            if Path(path).exists() and not self.confirm_replace(path):return
        self.render_job(path,False,self.export_done,quality=q)

    def render_job(self,path,preview,done,quality='high'):
        try:model.validate_project(self.project)
        except Exception as e:self.error(str(e));return
        self.canvas.pause();self.play_button.set_label('▶');self.set_busy(True);snapshot=copy.deepcopy(self.project)
        if self.project_path:snapshot['_project_path']=str(Path(self.project_path).resolve())
        self.cancel_event=threading.Event()
        dialog=Gtk.Dialog(title='Preparing preview' if preview else 'Exporting video',transient_for=self,modal=True)
        dialog.set_default_size(480,-1);dialog.add_button('Cancel',Gtk.ResponseType.CANCEL)
        content=dialog.get_content_area();content.set_border_width(22);content.set_spacing(12)
        content.pack_start(label('Bringing your story together','section'),False,False,0)
        info=label('Preparing clips…','muted');info.set_line_wrap(True);info.set_max_width_chars(60);content.pack_start(info,False,False,0)
        bar=Gtk.ProgressBar();bar.set_show_text(True);content.pack_start(bar,False,False,0)
        content.pack_start(label('This happens entirely on your computer.','small'),False,False,0)
        def cancel(*args):
            self.cancel_event.set();info.set_text('Cancelling…');dialog.set_response_sensitive(Gtk.ResponseType.CANCEL,False)
            return True
        dialog.connect('response',cancel);dialog.connect('delete-event',cancel);dialog.show_all()
        def progress_update(value,message):
            if not self.closed and self.busy:bar.set_fraction(min(1,max(0,value)));bar.set_text(f'{int(value*100)}%');info.set_text(message)
            return False
        def progress(value,message):GLib.idle_add(progress_update,value,message)
        def finish(error):
            dialog.destroy();self.set_busy(False);self.cancel_event=None
            if error:
                if isinstance(error,engine.RenderCancelled):self.set_status('Cancelled. Your original files are unchanged.')
                else:self.error(str(error))
            else:done(path)
            return False
        def work():
            try:engine.render(snapshot,path,preview=preview,progress=progress,cancel=self.cancel_event,quality=quality);error=None
            except Exception as e:error=e
            GLib.idle_add(finish,error)
        self.pool.submit(work)

    def export_done(self,path):
        self.set_status(f'Video exported: {path}')
        dialog=Gtk.MessageDialog(transient_for=self,modal=True,message_type=Gtk.MessageType.INFO,buttons=Gtk.ButtonsType.NONE,text='Your video is ready')
        dialog.format_secondary_text(path);dialog.add_button('Close',Gtk.ResponseType.CLOSE);dialog.add_button('Open video',Gtk.ResponseType.OK)
        response=dialog.run();dialog.destroy()
        if response==Gtk.ResponseType.OK:Gtk.show_uri_on_window(self,Path(path).as_uri(),Gdk.CURRENT_TIME)

    def set_busy(self,busy):
        self.busy=busy;self.editor.set_sensitive(not busy);self.header.set_sensitive(not busy);self.update_title()

    def set_status(self,text):self.status.set_text(text)

    def save(self):
        if self.busy:return False
        path=self.project_path
        if not path:
            dialog=Gtk.FileChooserDialog(title='Save project',transient_for=self,action=Gtk.FileChooserAction.SAVE)
            dialog.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Save',Gtk.ResponseType.OK);dialog.set_do_overwrite_confirmation(True);dialog.set_current_name('My story.clipcanvas')
            path=dialog.get_filename() if dialog.run()==Gtk.ResponseType.OK else None;dialog.destroy()
            if not path:return False
            if not path.endswith('.clipcanvas'):
                path+='.clipcanvas'
                if Path(path).exists() and not self.confirm_replace(path):return False
        try:
            model.save_project(self.project,path);self.project_path=path;self.saved=json.dumps(self.project,sort_keys=True);self.update_title()
            self.set_status('Project saved. Keep the original media files in their current folders.');return True
        except Exception as e:self.error(str(e));return False

    def open_project(self,path=None):
        if self.busy or not self.confirm_discard():return
        if not path:
            dialog=Gtk.FileChooserDialog(title='Open project',transient_for=self,action=Gtk.FileChooserAction.OPEN)
            dialog.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Open',Gtk.ResponseType.OK)
            filt=Gtk.FileFilter();filt.set_name('ClipCanvas projects');filt.add_pattern('*.clipcanvas');filt.add_pattern('*.json');dialog.add_filter(filt)
            path=dialog.get_filename() if dialog.run()==Gtk.ResponseType.OK else None;dialog.destroy()
        if not path:return
        try:project=model.load_project(path)
        except Exception as e:self.error(str(e));return
        self.canvas.set_still(None);self.project=project;self.project_path=path;self.saved=json.dumps(project,sort_keys=True)
        self.history=[];self.future=[];self.selected=None;self.preview_key=None;self.playhead=0
        self.sync_project_widgets();self.stack.set_visible_child_name('editor');self.refresh()
        for kind in ('clips','audio'):
            for item in project[kind]:self.load_asset(item,kind)
        self.show_selected_still()
        self.set_status('Project opened. Press Preview to prepare playback.')

    def confirm_discard(self):
        if json.dumps(self.project,sort_keys=True)==self.saved:return True
        dialog=Gtk.MessageDialog(transient_for=self,modal=True,message_type=Gtk.MessageType.QUESTION,buttons=Gtk.ButtonsType.NONE,text='Save your project first?')
        dialog.format_secondary_text('Your unsaved changes will be lost if you discard them.')
        dialog.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Discard',Gtk.ResponseType.REJECT,'Save',Gtk.ResponseType.ACCEPT)
        response=dialog.run();dialog.destroy()
        return self.save() if response==Gtk.ResponseType.ACCEPT else response==Gtk.ResponseType.REJECT

    def confirm_replace(self,path):
        dialog=Gtk.MessageDialog(transient_for=self,modal=True,message_type=Gtk.MessageType.QUESTION,buttons=Gtk.ButtonsType.NONE,text='Replace the existing file?')
        dialog.format_secondary_text(path);dialog.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Replace',Gtk.ResponseType.ACCEPT)
        response=dialog.run();dialog.destroy();return response==Gtk.ResponseType.ACCEPT

    def error(self,message):
        dialog=Gtk.MessageDialog(transient_for=self,modal=True,message_type=Gtk.MessageType.ERROR,buttons=Gtk.ButtonsType.CLOSE,text='Something needs attention')
        dialog.format_secondary_text(str(message)[-3500:]);dialog.run();dialog.destroy()

    def help(self):
        dialog=Gtk.MessageDialog(transient_for=self,modal=True,message_type=Gtk.MessageType.INFO,buttons=Gtk.ButtonsType.CLOSE,text='A simple way to make your video')
        dialog.format_secondary_text('1. Choose your canvas size at the top.\n2. Import clips; drag them in the story strip to reorder.\n3. Select a clip, choose its transition, and Apply changes.\n4. Add soundtracks, including WAV files. Drag them to set timing, or enter exact times.\n5. Preview your complete mix, then Export video.\n\nCtrl+S Save · Ctrl+O Open · Ctrl+Z Undo · Ctrl+Shift+Z Redo\n\nProjects reference your originals: keep those files in place.\nPreview is lower resolution; export reads the original media.\nSoundtracks stop at the end of the video.\n\nClipCanvas '+__version__+' · Native, local, and free.')
        dialog.run();dialog.destroy()

    def keypress(self,w,event):
        if self.busy:return False
        ctrl=event.state&Gdk.ModifierType.CONTROL_MASK;shift=event.state&Gdk.ModifierType.SHIFT_MASK
        name=Gdk.keyval_name(event.keyval).lower()
        if ctrl:
            if name=='s':self.save();return True
            if name=='o':self.open_project();return True
            if name=='z':self.redo() if shift else self.undo();return True
        focus=self.get_focus()
        if name=='space' and not isinstance(focus,(Gtk.Entry,Gtk.SpinButton)):
            self.toggle_play();return True
        return False

    def close_request(self,*args):
        if self.busy:self.set_status('Finish or cancel the current operation before closing.');return True
        if not self.confirm_discard():return True
        self.cleanup();Gtk.main_quit();return False

    def cleanup(self):
        self.closed=True;self.canvas.stop();self.pool.shutdown(wait=False,cancel_futures=True)
        # Workers may still be finishing thumbnails; remove their temp directory after they exit.
        def clean():self.pool.shutdown(wait=True);shutil.rmtree(self.temp,ignore_errors=True)
        threading.Thread(target=clean,daemon=True).start()

def main():
    provider=Gtk.CssProvider();provider.load_from_data(CSS)
    if not Gdk.Screen.get_default():
        print('ClipCanvas needs an Ubuntu desktop session.',file=sys.stderr);return 1
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(),provider,Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    GLib.set_prgname('clipcanvas');GLib.set_application_name('ClipCanvas')
    window=ClipCanvasWindow()
    if len(sys.argv)>1 and Path(sys.argv[1]).is_file():window.open_project(sys.argv[1])
    Gtk.main();return 0

if __name__=='__main__':raise SystemExit(main())
