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
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango, PangoCairo
import cairo

from . import model, engine, storage, track_layout, audio_effects
from . import __version__
from .player import VideoCanvas

from . import ui_theme
from .layer_ui import LayerTools

# Kept as an import for third-party launchers and the native integration checks.
CSS = ui_theme.CSS

FORMATS = [
    ('YouTube video', 'Landscape · 16:9', 1920, 1080),
    ('Instagram Reel', 'Portrait · 9:16', 1080, 1920),
    ('Square video', 'Social post · 1:1', 1080, 1080),
    ('YouTube 4K', 'Landscape · 16:9', 3840, 2160),
    ('Vertical 4K', 'Portrait · 9:16', 2160, 3840),
]
TRANSITIONS = [('none', 'Cut · no transition'), ('fade', 'Fade · cross dissolve'),
               ('wipeleft', 'Wipe left'), ('slideright', 'Slide right'), ('dissolve', 'Dissolve pixels')]

def file_chooser(parent,title,action,accept):
    """Use the desktop file chooser and its native ACCEPT response."""
    return Gtk.FileChooserNative.new(title,parent,action,accept,'Cancel')

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
    font=Pango.FontDescription(Gtk.Settings.get_default().get_property('gtk-font-name'))
    font.set_absolute_size(size*Pango.SCALE)
    if bold:font.set_weight(Pango.Weight.BOLD)
    layout=PangoCairo.create_layout(cr);layout.set_font_description(font);layout.set_text(text,-1)
    cr.move_to(x,y-size);PangoCairo.show_layout(cr,layout)

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
        self.owner=owner; self.scale=28.; self.hit=[]; self.join_hits=[];self.fade_hits=[];self.add_hit=None;self.drag=None;self.drag_scroll_timer=0
        self.connect('destroy',lambda _:self.stop_drag_scroll())
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect('draw',self.draw)
        self.connect('button-press-event',self.press)
        self.connect('button-release-event',self.release)
        self.connect('motion-notify-event',self.motion)
        self.set_has_tooltip(True);self.connect('query-tooltip',self.query_tooltip)
        self.connect('style-updated',lambda _:self.queue_draw())

    def refresh(self):
        duration=max([model.project_duration(self.owner.project)]+[a['start']+model.clip_duration(a) for a in self.owner.project['audio']])
        visuals=self.owner.project.get('layers',[]);captions=self.owner.project.get('captions',[])
        duration=max([duration]+[i['end'] for i in visuals+captions])
        height=max(y+h+18 for _,_,_,y,h in track_layout.rows(self.owner.project))
        self.set_size_request(max(800,int(duration*self.scale+170)),max(220,height))
        self.queue_draw()

    def draw(self, widget, cr):
        a=self.get_allocation(); p=self.owner.project; self.hit=[];self.join_hits=[];self.fade_hits=[];self.add_hit=None
        colors=ui_theme.palette(self)
        rgb(cr,colors['background']); cr.paint()
        duration=model.project_duration(p)
        positions={(kind,index):(y,h) for _,kind,index,y,h in track_layout.rows(p)}
        video_y,video_h=positions[('clips',None)]
        step=1 if self.scale>65 else 5 if self.scale>15 else 10
        for sec in range(0, int(max(duration,(a.width-100)/self.scale))+step,step):
            x=100+sec*self.scale
            rgb(cr,colors['border']); cr.set_line_width(1); cr.move_to(x,30);cr.line_to(x,a.height);cr.stroke()
            draw_text(cr,f'{sec}s',x+3,21,colors['muted'],size=10)
        draw_text(cr,'VIDEO',12,video_y+28,colors['muted'],size=10,bold=True)
        if not p['clips']:
            rounded(cr,105,video_y+2,max(300,a.width-130),75); rgb(cr,colors['surface']);cr.fill()
            draw_text(cr,'Import clips to start your story',128,video_y+45,colors['muted'],14)
        timings=model.timeline(p)
        moving=self.drag and self.drag.get('active') and self.drag.get('kind')=='clips' and not self.drag.get('edge')
        order=list(range(len(p['clips'])))
        if moving:
            index=order.pop(self.drag['index']);order.insert(self.drag['target'],index)
            candidate={**p,'clips':[p['clips'][i] for i in order]}
            display_timings=model.timeline(candidate)
        else:display_timings=timings
        for slot,(i,timing) in enumerate(zip(order,display_timings)):
            clip=p['clips'][i]
            x=100+timing['start']*self.scale; y=video_y; w=max(12,timing['duration']*self.scale-3);h=video_h
            if moving and i==self.drag['index']:
                rounded(cr,x,y,w,h);rgb(cr,colors['accent'],.12);cr.fill_preserve()
                cr.set_dash([5,4]);rgb(cr,colors['accent']);cr.set_line_width(2);cr.stroke();cr.set_dash([])
                rgb(cr,colors['accent']);cr.set_line_width(3);cr.move_to(x,y-5);cr.line_to(x,y+h+5);cr.stroke()
                draw_text(cr,'Drop here',x+8,y-7,colors['accent'],11,True)
            else:self.paint_clip(cr,clip,i,x,y,w,h,self.owner.selected==('clips',i))
            self.hit.append(('clips',i,x,y,w,h))
        for i,audio in enumerate(p['audio']):
            y,_=positions[('audio',i)];x=100+audio['start']*self.scale
            if self.drag and self.drag.get('active') and self.drag.get('kind')=='audio' and self.drag['index']==i and not self.drag['edge']:x=100+max(0,audio['start']+self.drag['delta'])*self.scale
            visible=model.clip_duration(audio)
            w=max(12,visible*self.scale)
            draw_text(cr,f'AUDIO {i+1}',12,y+26,colors['muted'],size=10,bold=True)
            cr.save();rounded(cr,x,y,w,42,7);cr.clip();rgb(cr,'#8150c6');cr.paint()
            values=self.owner.waves.get(audio['path'],[])
            if values:
                for xx in range(0,int(w),3):
                    fraction=(audio['in']+(xx/max(w,1))*visible*audio.get('speed',1))/max(.01,audio['duration'])
                    amp=values[min(len(values)-1,max(0,int(fraction*len(values))))]
                    hh=max(2,amp*28);rgb(cr,'#c5a7f0',.8);cr.rectangle(x+xx,y+21-hh/2,2,hh);cr.fill()
            rgb(cr,'#592899',.7);cr.rectangle(x,y,w,18);cr.fill();draw_text(cr,('FX · ' if audio_effects.active(audio) else '')+Path(audio['path']).name,x+7,y+13,'#ffffff',10)
            audible=max(0,min(visible,duration-audio['start']))
            fade_end=x+audible*self.scale
            for key,edge in [('fade_in','in'),('fade_out','out')]:
                value=audio.get(key,0)
                if self.drag and self.drag['mode']=='fade' and self.drag['index']==i and self.drag['edge']==edge:
                    value=self.drag.get('preview',self.drag['value'])
                fade=min(value,audible)*self.scale
                if fade>0:
                    cr.move_to(x,y+42) if edge=='in' else cr.move_to(fade_end-fade,y)
                    cr.line_to(x+fade,y) if edge=='in' else cr.line_to(fade_end,y+42)
                    cr.line_to(x,y) if edge=='in' else cr.line_to(fade_end,y)
                    cr.close_path();rgb(cr,'#ffffff',.22);cr.fill()
                if self.owner.selected==('audio',i) and audible>0:
                    hx=x+fade if edge=='in' else fade_end-fade
                    cr.arc(hx,y+4,4,0,math.tau);rgb(cr,'#ffffff');cr.fill()
                    self.fade_hits.append((i,edge,hx,y+4,7))
            cutoff=max(x,100+duration*self.scale)
            if cutoff<x+w:
                rgb(cr,colors['background'],.7);cr.rectangle(cutoff,y,x+w-cutoff,42);cr.fill()
            cr.restore()
            if self.owner.selected==('audio',i):
                rounded(cr,x,y,w,42,7);rgb(cr,colors['accent']);cr.set_line_width(3);cr.stroke()
            self.hit.append(('audio',i,x,y,w,42))
            if self.owner.selected==('audio',i) and w >= 24:
                for edge in (x+3,x+w-5):
                    rgb(cr,'#ffffff');cr.rectangle(edge,y+10,2,22);cr.fill()
        for group,items in [('layers',p.get('layers',[])),('captions',p.get('captions',[]))]:
            for index,item in enumerate(items):
                y,h=positions[(group,index if group=='layers' else None)];x=100+item['start']*self.scale;w=max(12,(item['end']-item['start'])*self.scale)
                if self.drag and self.drag.get('active') and self.drag.get('kind')==group and self.drag['index']==index and not self.drag['edge']:x=100+max(0,item['start']+self.drag['delta'])*self.scale
                if group=='layers' or index==0:draw_text(cr,f'LAYER {index+1}' if group=='layers' else 'CAPTIONS',12,y+22,colors['muted'],10,True)
                cr.save();rounded(cr,x,y,w,h,6);cr.clip();rgb(cr,'#267aab' if group=='layers' else '#33864b');cr.paint()
                title=item.get('text') or Path(item.get('path','')).name
                draw_text(cr,title.replace('\n',' '),x+7,y+22,'#ffffff',11);cr.restore()
                if self.owner.selected==(group,index):
                    rounded(cr,x,y,w,h,6);rgb(cr,colors['accent']);cr.set_line_width(3);cr.stroke()
                    if w>=24:
                        for edge in (x+3,x+w-5):rgb(cr,'#ffffff');cr.rectangle(edge,y+8,2,18);cr.fill()
                self.hit.append((group,index,x,y,w,h))
        # Paint joins after every thumbnail so their hit targets remain visible
        # even when transition overlaps make the clip rectangles overlap.
        for i,row in enumerate([] if moving else timings[:-1]):
            cx=100+(row['start']+row['duration']-row['overlap']/2)*self.scale;cy=video_y+40
            cr.new_sub_path()
            cr.arc(cx,cy,14,0,math.tau);rgb(cr,colors['surface']);cr.fill_preserve()
            rgb(cr,colors['accent'] if p['clips'][i]['transition']!='none' else colors['border']);cr.set_line_width(1.5);cr.stroke()
            rgb(cr,colors['text']);cr.set_line_width(1.4)
            for sign in (-1,1):
                cr.move_to(cx,cy);cr.line_to(cx+sign*6,cy-4);cr.line_to(cx+sign*6,cy+4);cr.close_path();cr.stroke()
            self.join_hits.append((i,cx,cy,15))
        if p['clips'] and not moving:
            end=100+duration*self.scale
            rgb(cr,colors['border']);cr.set_dash([4,4]);cr.move_to(end,30);cr.line_to(end,a.height);cr.stroke();cr.set_dash([])
            draw_text(cr,'Video ends',end+6,36,colors['muted'],10)
            x=end+12;y=video_y+15;size=44
            rounded(cr,x,y,size,size,9);rgb(cr,colors['surface']);cr.fill_preserve();rgb(cr,colors['border']);cr.set_line_width(1);cr.stroke()
            rgb(cr,colors['text']);cr.set_line_width(2);cr.move_to(x+12,y+22);cr.line_to(x+32,y+22);cr.move_to(x+22,y+12);cr.line_to(x+22,y+32);cr.stroke()
            self.add_hit=(x,y,size,size)
        px=100+self.owner.playhead*self.scale
        rgb(cr,colors['text']);cr.set_line_width(2);cr.move_to(px,27);cr.line_to(px,a.height);cr.stroke()
        cr.move_to(px-5,27);cr.line_to(px+5,27);cr.line_to(px,34);cr.close_path();cr.fill()
        if moving:
            d=self.drag;clip=p['clips'][d['index']]
            x=max(100,d['pointer_x']-d['grab_x']);y=video_y-7+max(-10,min(10,d['pointer_y']-d['y']))
            w=max(12,model.clip_duration(clip)*self.scale-3)
            cr.save();rounded(cr,x+4,y+6,w,video_h);rgb(cr,'#000000',.3);cr.fill()
            cr.push_group();self.paint_clip(cr,clip,d['index'],x,y,w,video_h,True);cr.pop_group_to_source();cr.paint_with_alpha(.9);cr.restore()
        if self.drag and self.drag.get('mode')=='edit' and self.drag.get('edge'):
            gx=self.drag['boundary']+self.drag.get('delta',0)*self.scale
            rgb(cr,colors['accent']);cr.set_line_width(2);cr.move_to(gx,28);cr.line_to(gx,a.height);cr.stroke()

    def paint_clip(self,cr,clip,index,x,y,width,height,selected=False):
        colors=ui_theme.palette(self)
        cr.save();rounded(cr,x,y,width,height);cr.clip()
        pix=self.owner.thumbs.get(clip.get('path'))
        if model.is_card(clip):rgb(cr,clip['color']);cr.paint()
        elif pix:
            factor=height/pix.get_height();pw=pix.get_width()*factor
            for tx in range(int(x),int(x+width)+int(pw)+1,max(1,int(pw))):
                cr.save();cr.translate(tx,y);cr.scale(factor,factor);Gdk.cairo_set_source_pixbuf(cr,pix,0,0);cr.paint();cr.restore()
        else:rgb(cr,'#b5a0d4');cr.paint()
        rgb(cr,'#21172c',.70);cr.rectangle(x,y+height-27,width,27);cr.fill()
        draw_text(cr,f'{index+1}  '+('FX · ' if audio_effects.active(clip) else '')+model.item_name(clip),x+8,y+height-9,'#ffffff',11,True)
        cr.restore();rounded(cr,x,y,width,height);rgb(cr,colors['accent'] if selected else '#ffffff');cr.set_line_width(3 if selected else 1);cr.stroke()
        if selected and width>=24:
            for edge in (x+3,x+width-5):rgb(cr,'#ffffff');cr.rectangle(edge,y+25,2,27);cr.fill()

    def reorder_target(self,x):
        d=self.drag
        return sum(x>100+(row['start']+row['duration']/2)*self.scale for i,row in enumerate(model.timeline(self.owner.project)) if i!=d['index'])

    def stop_drag_scroll(self):
        if self.drag_scroll_timer:GLib.source_remove(self.drag_scroll_timer);self.drag_scroll_timer=0

    def drag_scroll(self):
        if not self.drag or not self.drag.get('active'):
            self.drag_scroll_timer=0;return False
        d=self.drag;adjustment=self.owner.timeline_scroll.get_hadjustment();old=adjustment.get_value()
        viewport_x=d['pointer_x']-old;page=adjustment.get_page_size()
        step=-18 if viewport_x<40 else 18 if viewport_x>page-40 else 0
        adjustment.set_value(max(0,min(adjustment.get_upper()-page,old+step)))
        change=adjustment.get_value()-old
        if change:
            d['pointer_x']+=change;d['delta']=self.drag_delta(d['pointer_x'])
            if d['kind']=='clips' and not d['edge']:d['target']=self.reorder_target(d['pointer_x'])
            self.queue_draw()
        return True

    def query_tooltip(self,widget,x,y,keyboard,tooltip):
        if self.add_hit:
            ax,ay,width,height=self.add_hit
            if ax<=x<=ax+width and ay<=y<=ay+height:tooltip.set_text('Add videos');return True
        for index,cx,cy,r in self.join_hits:
            if abs(x-cx)<=r and abs(y-cy)<=r:
                effect=self.owner.project['clips'][index]['transition']
                tooltip.set_text('Add transition' if effect=='none' else 'Change transition');return True
        for index,edge,cx,cy,r in self.fade_hits:
            if abs(x-cx)<=r and abs(y-cy)<=r:
                tooltip.set_text('Drag to adjust audio fade '+edge);return True
        if y>=27 and abs(x-(100+self.owner.playhead*self.scale))<=6:
            tooltip.set_text('Drag the playhead to preview a time');return True
        return False

    def press(self, w, event):
        if self.owner.busy:return
        if event.button==3:
            for kind,index,x,y,width,height in reversed(self.hit):
                if x<=event.x<=x+width and y<=event.y<=y+height:
                    self.owner.context_menu(kind,index,event);return True
            return
        if event.button!=1:return
        if self.add_hit:
            x,y,width,height=self.add_hit
            if x<=event.x<=x+width and y<=event.y<=y+height:self.owner.import_dialog('clips');return True
        for index,cx,cy,r in self.join_hits:
            if abs(event.x-cx)<=r and abs(event.y-cy)<=r:
                rect=Gdk.Rectangle();rect.x=int(cx-r);rect.y=int(cy-r);rect.width=rect.height=2*r
                self.owner.show_transition(index,rect);return True
        for index,edge,cx,cy,r in self.fade_hits:
            if abs(event.x-cx)<=r and abs(event.y-cy)<=r:
                self.owner.select('audio',index,seek=False)
                self.drag={'mode':'fade','index':index,'edge':edge,'x':event.x,
                           'value':self.owner.project['audio'][index].get('fade_'+edge,0)}
                return True
        if event.y<34 or (event.y>=27 and abs(event.x-(100+self.owner.playhead*self.scale))<=6):
            self.drag={'mode':'seek'}
            self.owner.begin_scrub()
            self.owner.seek(max(0,(event.x-100)/self.scale));return True
        hits=list(reversed(self.hit))
        # A selected outgoing edge can overlap the next clip. Keep its trim
        # handle reachable without changing ordinary body-selection order.
        for hit in list(hits):
            kind,i,x,y,width,height=hit
            if self.owner.selected==(kind,i) and width>=24 and y<=event.y<=y+height and (x<=event.x<x+8 or x+width-8<event.x<=x+width):
                hits.remove(hit);hits.insert(0,hit);break
        for kind,i,x,y,width,height in hits:
            if x<=event.x<=x+width and y<=event.y<=y+height:
                self.owner.select(kind,i)
                edge='left' if width>=24 and event.x-x<8 else 'right' if width>=24 and x+width-event.x<8 else None
                self.drag={'mode':'edit','kind':kind,'index':i,'x':event.x,'y':event.y,'edge':edge,
                           'grab_x':event.x-x,'pointer_x':event.x,'pointer_y':event.y,'target':i,'active':False,
                           'boundary':x if edge=='left' else x+width+(3 if kind=='clips' else 0),
                           'start':self.owner.project[kind][i].get('start',0),'delta':0}
                return True

    def motion(self,w,event):
        if self.drag:
            if self.drag['mode']=='seek':
                self.owner.seek(max(0,(event.x-100)/self.scale));return True
            if self.drag['mode']=='fade':
                value=self.fade_value(event.x);self.drag['preview']=value;self.queue_draw()
                self.owner.set_status(f'Audio fade {self.drag["edge"]}: {value:.2f} s');return True
            self.drag['delta']=self.drag_delta(event.x)
            self.drag.update(pointer_x=event.x,pointer_y=event.y)
            if abs(event.x-self.drag['x'])>=4:self.drag['active']=True
            if self.drag['kind']=='clips' and not self.drag['edge']:self.drag['target']=self.reorder_target(event.x)
            if self.drag['active'] and not self.drag_scroll_timer:self.drag_scroll_timer=GLib.timeout_add(50,self.drag_scroll)
            self.get_window().set_cursor(Gdk.Cursor.new_from_name(self.get_display(),'grabbing'))
            self.queue_draw()
        elif self.get_window():
            near=event.y>=27 and abs(event.x-(100+self.owner.playhead*self.scale))<=6
            self.get_window().set_cursor(Gdk.Cursor.new_from_name(self.get_display(),'ew-resize') if near else None)

    def fade_value(self,x):
        d=self.drag;item=self.owner.project['audio'][d['index']]
        sign=1 if d['edge']=='in' else -1
        audible=max(0,min(model.clip_duration(item),model.project_duration(self.owner.project)-item['start']))
        return max(0,min(audible,d['value']+sign*(x-d['x'])/self.scale))

    def drag_delta(self,x):
        d=self.drag
        delta=(x-d['x'])/self.scale
        if d['edge']:at=(d['boundary']-100)/self.scale
        elif d['kind'] in ('audio','layers','captions'):at=d['start']
        else:return delta
        value=at+delta
        if self.owner.snap_check.get_active():
            marks=[0,self.owner.playhead]
            for row in model.timeline(self.owner.project):marks.extend((row['start'],row['start']+row['duration']))
            for i,item in enumerate(self.owner.project['audio']):
                if d['kind']=='audio' and i==d['index']:continue
                marks.extend((item['start'],item['start']+model.clip_duration(item)))
            for kind in ('layers','captions'):
                for i,item in enumerate(self.owner.project.get(kind,[])):
                    if d['kind']==kind and i==d['index']:continue
                    marks.extend((item['start'],item['end']))
            marks=[m for m in marks if abs(m-at)>1e-6]
            if marks:
                nearest=min(marks,key=lambda m:abs(m-value))
                if abs(nearest-value)*self.scale<=8:value=nearest
        return round(value*self.owner.project['fps'])/self.owner.project['fps']-at

    def release(self,w,event):
        self.stop_drag_scroll()
        if self.get_window():self.get_window().set_cursor(None)
        if not self.drag:return
        d=self.drag
        if d['mode']=='seek':
            self.drag=None
            self.owner.end_scrub(max(0,(event.x-100)/self.scale));return True
        if d['mode']=='fade':
            value=self.fade_value(event.x);self.drag=None
            self.owner.checkpoint();self.owner.project['audio'][d['index']]['fade_'+d['edge']]=round(value,2)
            self.owner.changed();return True
        release_x=event.x
        if d['kind']=='clips' and not d['edge']:d['target']=self.reorder_target(release_x)
        delta=self.drag_delta(release_x);self.drag=None;self.queue_draw()
        if abs(event.x-d['x'])<3:return
        kind,i=d['kind'],d['index']
        if kind in ('layers','captions'):
            item=self.owner.project[kind][i];candidate=copy.deepcopy(self.owner.project);edited=candidate[kind][i]
            if d['edge']=='left':
                low=max(0,item['start']-item.get('in',0)/item.get('speed',1)) if item.get('kind')=='video' else 0
                high=min(item['end']-.04,item['start']+(item['duration']-item.get('in',0)-.001)/item.get('speed',1)) if item.get('kind')=='video' else item['end']-.04
                edited['start']=max(low,min(high,item['start']+delta))
                if item.get('kind')=='video':edited['in']=item.get('in',0)+(edited['start']-item['start'])*item.get('speed',1)
            elif d['edge']=='right':edited['end']=max(item['start']+.04,item['end']+delta)
            else:
                edited['start']=max(0,item['start']+delta);edited['end']=edited['start']+item['end']-item['start']
            try:model.validate_project(candidate,check_files=False)
            except ValueError as e:self.owner.set_status(str(e));return True
            self.owner.checkpoint();self.owner.project=candidate;self.owner.changed();return True
        if d['edge']:
            try:project=model.trim_item(self.owner.project,kind,i,d['edge'],delta)
            except ValueError as e:self.owner.set_status(str(e));return
            if project==self.owner.project:return
            self.owner.checkpoint();self.owner.project=project
        elif kind=='audio':
            self.owner.checkpoint()
            self.owner.project[kind][i]['start']=max(0,min(max(0,model.project_duration(self.owner.project)-.04),d['start']+delta))
        else:
            target=d['target']
            if target==i:return True
            self.owner.checkpoint()
            clips=self.owner.project['clips'];clip=clips.pop(i);clips.insert(target,clip);self.owner.selected=(kind,target)
        self.owner.changed()

class ClipCanvasWindow(Gtk.Window):
    def __init__(self,recovery_dir=None):
        super().__init__(title='ClipCanvas')
        self.set_wmclass('clipcanvas','ClipCanvas')
        self.set_default_size(1380,900);self.set_position(Gtk.WindowPosition.CENTER)
        self.get_style_context().add_class('clipcanvas')
        self.project=model.new_project();self.project_path=None;self.saved=json.dumps(self.project,sort_keys=True)
        self.selected=None;self.history=[];self.future=[];self.thumbs={};self.waves={};self.playhead=0.;self.preview_key=None
        self.media_metadata={};self.mute_volumes={}
        self.temp=Path(tempfile.mkdtemp(prefix='clipcanvas-'));self.pool=ThreadPoolExecutor(max_workers=2)
        self.frame_pool=ThreadPoolExecutor(max_workers=1)
        self._drag_scrubbing=False;self._scrub_settling=False;self._scrub_resume=False;self._position_target=None
        self._drag_frame_busy=False;self._drag_frame_cancel=None;self._drag_frame_pending=None;self._drag_frame_serial=0
        self.closed=False;self.busy=False;self.cancel_event=None;self.name_lock=False
        self.recovery=storage.Recovery(recovery_dir);self.autosave_timer=0;self.scrub_timer=0;self.scrub_serial=0;self.scrub_active=False
        self.connect('delete-event',self.close_request)
        self.connect('key-press-event',self.keypress)
        self.root=box(spacing=0);self.add(self.root)
        self.make_header()
        self.stack=Gtk.Stack();self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.root.pack_start(self.stack,True,True,0)
        self.make_welcome();self.make_editor()
        self.status=label('Local files. Your originals stay untouched.','dim-label');self.status.get_style_context().add_class('status')
        self.status.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.root.pack_start(self.status,False,False,0)
        self.stack.set_visible_child_name('welcome')
        self.drag_dest_set(Gtk.DestDefaults.ALL,[Gtk.TargetEntry.new('text/uri-list',0,0)],Gdk.DragAction.COPY)
        self.connect('drag-data-received',self.drop_files)
        self.show_all();self.properties_popover.hide();self.update_title()
        self.autosave_timer=GLib.timeout_add_seconds(30,self.autosave)
        if self.recovery.available():self.set_status('An unsaved project is available in Project → Recover unsaved project.')

    def make_header(self):
        self.header=Gtk.HeaderBar();self.header.set_show_close_button(True)
        self.header.set_title('ClipCanvas');self.set_titlebar(self.header)
        files=box(Gtk.Orientation.HORIZONTAL,0);files.get_style_context().add_class('linked')
        for icon,title,callback in [('document-new-symbolic','New project',self.new),
                                    ('document-open-symbolic','Open project',self.open_project),
                                    ('document-save-symbolic','Save project',self.save)]:
            files.pack_start(ui_theme.icon_button(icon,callback,title),False,False,0)
        self.header.pack_start(files)
        self.name=Gtk.Entry();self.name.set_text(self.project['name']);self.name.set_max_length(100)
        self.name.set_width_chars(24);self.name.set_tooltip_text('Project name')
        self.name.get_style_context().add_class('project-name')
        self.name.connect('changed',self.name_changed);self.header.set_custom_title(self.name)
        menu=Gtk.Menu()
        for title,callback in [('New project',self.new),('Open project…',self.open_project),('Save project',self.save),
                               ('Save portable copy…',self.collect_media),('Relink missing media…',self.relink_media),
                               ('Recover unsaved project…',self.recover_project),
                               ('Render smooth preview…',self.render_preview),('Help',self.help)]:
            entry=Gtk.MenuItem(label=title);entry.connect('activate',lambda _,fn=callback:fn());menu.append(entry)
        menu.show_all();project_menu=Gtk.MenuButton();project_menu.set_image(ui_theme.icon('open-menu-symbolic'))
        project_menu.set_tooltip_text('Project menu');project_menu.set_popup(menu)
        self.header.pack_end(project_menu)
        self.export_button=ui_theme.icon_button('document-send-symbolic',self.export,'Export finished video',label='Export',style='suggested-action')
        self.header.pack_end(self.export_button)
        self.preview_button=ui_theme.icon_button('media-playback-start-symbolic',self.preview,'Play your video immediately',label='Preview')
        self.header.pack_end(self.preview_button)

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
        self.workspace=Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self.editor.pack_start(self.workspace,True,True,0)
        self.body=Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        library=box(spacing=0);self.library=library;library.set_size_request(260,-1);library.get_style_context().add_class('library')
        self.layer_tools=LayerTools(self)
        self.media_stack=Gtk.Stack();self.media_stack.set_hhomogeneous(False);self.media_stack.set_vhomogeneous(False)
        switcher=Gtk.StackSwitcher();switcher.set_stack(self.media_stack);switcher.set_halign(Gtk.Align.CENTER)
        library.pack_start(switcher,False,False,8)
        media_page=box(spacing=0)
        library_top=box(margin=16,spacing=10)
        library_top.pack_start(label('Your media','section'),False,False,0)
        library_top.pack_start(ui_theme.icon_button('document-open-symbolic',lambda:self.import_dialog('clips'),'Add local videos',label='Add videos',style='suggested-action'),False,False,0)
        library_top.pack_start(ui_theme.icon_button('audio-x-generic-symbolic',lambda:self.import_dialog('audio'),'Add WAV, MP3, FLAC and other soundtracks',label='Add audio'),False,False,0)
        hint=label('Local files · no uploads','dim-label');library_top.pack_start(hint,False,False,0)
        media_page.pack_start(library_top,False,False,0)
        self.media_search=Gtk.SearchEntry();self.media_search.set_placeholder_text('Search your media')
        self.media_search.set_margin_start(16);self.media_search.set_margin_end(16)
        self.media_search.connect('search-changed',lambda _:self.refresh_assets())
        media_page.pack_start(self.media_search,False,False,8)
        self.media_tabs=Gtk.ComboBoxText()
        for key,title in [('all','All media'),('clips','Videos'),('audio','Audio')]:self.media_tabs.append(key,title)
        self.media_tabs.set_active_id('all');self.media_tabs.set_margin_start(16);self.media_tabs.set_margin_end(16)
        self.media_tabs.connect('changed',lambda _:self.refresh_assets())
        media_page.pack_start(self.media_tabs,False,False,4)
        self.assets=Gtk.ListBox();self.assets.set_selection_mode(Gtk.SelectionMode.NONE)
        media_page.pack_start(scrolled(self.assets),True,True,0)
        self.media_stack.add_titled(media_page,'Media','Media')
        self.media_stack.add_titled(self.layer_tools.layers_panel,'Layers','Layers')
        self.media_stack.add_titled(self.layer_tools.captions_panel,'Captions','Captions')
        library.pack_start(self.media_stack,True,True,0)
        self.body.pack1(library,False,False)
        center=box(spacing=0)
        self.canvas_toolbar=box(Gtk.Orientation.HORIZONTAL,10,12)
        self.canvas_toolbar.get_style_context().add_class('editor-toolbar')
        self.canvas_toolbar.pack_start(ui_theme.icon_button('folder-symbolic',self.toggle_library,'Show or hide your media'),False,False,0)
        self.canvas_toolbar.pack_start(label('Canvas','dim-label'),False,False,0)
        self.format_combo=Gtk.ComboBoxText()
        for title,desc,w,h in FORMATS:
            ratio='16:9' if w>h else '9:16' if h>w else '1:1'
            self.format_combo.append(f'{w}x{h}',f'{ratio} · {title} · {w} × {h}')
        self.format_combo.set_active(0);self.format_combo.connect('changed',self.format_changed)
        self.canvas_toolbar.pack_start(self.format_combo,False,False,0)
        center.pack_start(self.canvas_toolbar,False,False,0)
        self.context_toolbar=box(Gtk.Orientation.HORIZONTAL,8,10)
        self.context_title=label('Select a clip to edit','dim-label');self.context_title.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.context_title.set_max_width_chars(28);self.context_toolbar.pack_start(self.context_title,True,True,0)
        self.edit_button=ui_theme.icon_button('document-properties-symbolic',lambda:self.open_properties('Timing'),'Trim and playback speed',label='Edit')
        self.picture_button=ui_theme.icon_button('image-x-generic-symbolic',lambda:self.open_properties('Picture'),'Crop, rotate, flip and picture fades',label='Picture')
        self.sound_button=ui_theme.icon_button('audio-volume-high-symbolic',lambda:self.open_properties('Sound'),'Volume and audio fade in/out',label='Audio')
        for item in (self.edit_button,self.picture_button,self.sound_button):self.context_toolbar.pack_start(item,False,False,0)
        center.pack_start(self.context_toolbar,False,False,0)
        self.canvas=VideoCanvas();self.canvas.set_size_request(320,200)
        self.canvas.on_position=self.on_position;self.canvas.on_error=self.playback_error
        self.canvas.on_end=lambda:self.update_play_button(False)
        self.canvas.on_overlay_draw=self.draw_layers
        self.view_lock=False
        self.preview_zoom_combo=Gtk.ComboBoxText()
        for factor,title in [('1','Fit'),('.25','25%'),('.5','50%'),('.75','75%'),('1.25','125%'),('1.5','150%'),('2','200%'),('3','300%'),('4','400%')]:
            self.preview_zoom_combo.append(factor,title)
        self.preview_zoom_combo.set_active_id('1')
        self.preview_zoom_combo.set_tooltip_text('Preview magnification relative to Fit. Ctrl+wheel to zoom; Shift+drag to pan. Export is unchanged.')
        self.preview_zoom_combo.connect('changed',self.preview_zoom_changed)
        self.canvas.on_view_changed=self.preview_view_changed
        view_controls=box(Gtk.Orientation.HORIZONTAL,0);view_controls.get_style_context().add_class('linked')
        self.preview_zoom_out=ui_theme.icon_button('zoom-out-symbolic',lambda:self.canvas.zoom_step(-1),'Zoom preview out')
        self.preview_zoom_in=ui_theme.icon_button('zoom-in-symbolic',lambda:self.canvas.zoom_step(1),'Zoom preview in')
        for control in (self.preview_zoom_out,self.preview_zoom_combo,self.preview_zoom_in):view_controls.pack_start(control,False,False,0)
        self.canvas_toolbar.pack_end(view_controls,False,False,0)
        self.preview_quality=Gtk.ComboBoxText()
        for edge,title in [('0','Full resolution'),('1280','HD preview'),('640','Fast preview')]:self.preview_quality.append(edge,title)
        self.preview_quality.set_active_id('0')
        self.preview_quality.set_tooltip_text('Preview resolution only. Full uses the project canvas; HD caps the longest edge at 1280, Fast at 640. Export always uses originals.')
        self.preview_quality.connect('changed',self.preview_quality_changed)
        self.canvas_toolbar.pack_end(self.preview_quality,False,False,8)
        self.canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK|Gdk.EventMask.BUTTON_RELEASE_MASK|Gdk.EventMask.POINTER_MOTION_MASK)
        self.canvas.connect('button-press-event',lambda _,event:self.layer_tools.canvas_press(event))
        self.canvas.connect('motion-notify-event',lambda _,event:self.layer_tools.canvas_motion(event))
        self.canvas.connect('button-release-event',lambda _,event:self.layer_tools.canvas_release(event))
        center.pack_start(self.canvas,True,True,0)
        playback=box(Gtk.Orientation.HORIZONTAL,12,12);playback.set_halign(Gtk.Align.CENTER)
        self.play_button=ui_theme.icon_button('media-playback-start-symbolic',self.toggle_play,'Play / pause · Space',style='circular')
        playback.pack_start(self.play_button,False,False,0)
        self.clock_label=label('0:00.0 / 0:00.0');playback.pack_start(self.clock_label,False,False,0)
        self.preview_label=label('Press play to preview','dim-label')
        self.preview_label.set_tooltip_text('Preview resolution is selected above the canvas. Exports always use original media.')
        self.preview_label.set_ellipsize(Pango.EllipsizeMode.END);self.preview_label.set_max_width_chars(28)
        playback.pack_start(self.preview_label,False,False,8)
        center.pack_start(playback,False,False,0)
        self.body.pack2(center,True,False);self.body.set_position(280)
        self.workspace.pack1(self.body,True,False)
        timeline_area=box(spacing=0)
        # Properties appear on demand. No permanent panel consumes preview space.
        self.properties_popover=Gtk.Popover.new(self.edit_button)
        self.properties_popover.set_position(Gtk.PositionType.BOTTOM)
        properties=box(spacing=8,margin=12)
        self.inspector=box(spacing=8);self.inspector.set_size_request(350,-1)
        self.properties_scroll=Gtk.ScrolledWindow()
        self.properties_scroll.set_policy(Gtk.PolicyType.NEVER,Gtk.PolicyType.AUTOMATIC)
        self.properties_scroll.set_propagate_natural_height(True)
        self.properties_scroll.set_max_content_height(530)
        self.properties_scroll.add(self.inspector)
        properties.pack_start(self.properties_scroll,True,True,0)
        self.apply_button=button('Apply changes',self.apply_fields,'suggested-action')
        properties.pack_start(self.apply_button,False,False,0);self.properties_popover.add(properties)
        tlbar=box(Gtk.Orientation.HORIZONTAL,6,10);tlbar.get_style_context().add_class('timeline-toolbar')
        tlbar.pack_start(label('Timeline','section'),False,False,4)
        self.undo_button=ui_theme.icon_button('edit-undo-symbolic',self.undo,'Undo · Ctrl+Z')
        self.redo_button=ui_theme.icon_button('edit-redo-symbolic',self.redo,'Redo · Ctrl+Shift+Z')
        for item in (self.undo_button,self.redo_button):tlbar.pack_start(item,False,False,0)
        self.split_button=ui_theme.icon_button('edit-cut-symbolic',self.split_selected,'Split selected item at playhead · S',label='Split')
        tlbar.pack_start(self.split_button,False,False,0)
        self.duplicate_button=ui_theme.icon_button('edit-copy-symbolic',self.duplicate,'Duplicate selected item')
        self.remove_button=ui_theme.icon_button('edit-delete-symbolic',self.remove,'Remove selected item')
        for item in (self.duplicate_button,self.remove_button):tlbar.pack_start(item,False,False,0)
        self.row_up_button=ui_theme.icon_button('go-up-symbolic',lambda:self.move_timeline_row(-1),'Move selected timeline row up',label='Up')
        self.row_down_button=ui_theme.icon_button('go-down-symbolic',lambda:self.move_timeline_row(1),'Move selected timeline row down',label='Down')
        for item in (self.row_up_button,self.row_down_button):tlbar.pack_start(item,False,False,0)
        self.card_button=ui_theme.icon_button('list-add-symbolic',self.add_card,'Insert a color card after the selected clip, or at the playhead',label='Card')
        tlbar.pack_start(self.card_button,False,False,0)
        self.snap_check=Gtk.CheckButton(label='Snap');self.snap_check.set_active(True);tlbar.pack_start(self.snap_check,False,False,8)
        self.timeline_hint=label('Right-click clips for more actions','dim-label');self.timeline_hint.set_ellipsize(Pango.EllipsizeMode.END);tlbar.pack_start(self.timeline_hint,True,True,8)
        self.zoom_control=Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,6,140,1)
        self.zoom_control.set_value(28);self.zoom_control.set_draw_value(False);self.zoom_control.set_size_request(120,-1)
        self.zoom_control.connect('value-changed',lambda s:self.zoom(s.get_value()))
        tlbar.pack_end(self.zoom_control,False,False,0);tlbar.pack_end(label('Zoom','dim-label'),False,False,0)
        timeline_area.pack_start(tlbar,False,False,0)
        self.timeline=Timeline(self);self.timeline.refresh()
        self.timeline_scroll=Gtk.ScrolledWindow();self.timeline_scroll.set_policy(Gtk.PolicyType.AUTOMATIC,Gtk.PolicyType.AUTOMATIC)
        self.timeline_scroll.set_size_request(-1,180);self.timeline_scroll.add(self.timeline)
        timeline_area.pack_start(self.timeline_scroll,True,True,0)
        self.workspace.pack2(timeline_area,False,False);self.workspace.set_position(600)
        self.stack.add_named(self.editor,'editor');self.show_inspector()

    def draw_layers(self,cr,rect):
        from . import visuals
        if not self.canvas.composited:
            selected=self.selected_item() if self.selected and self.selected[0]=='layers' else None
            visuals.draw(cr,self.project,self.playhead,rect,selected=selected,assets=self.layer_tools.assets)

    def preview_quality_changed(self,combo):
        edge=int(combo.get_active_id())
        if self.canvas.preview_edge==edge:return
        at=self.playhead;playing=self.canvas.playing
        self.cancel_scrub();self.canvas.stop();self.canvas.preview_edge=edge;self.preview_key=None
        if self.project['clips']:
            if playing:
                self.canvas.load_project(self.project,at,autoplay=True);self.update_play_button(True)
            else:self.begin_scrub();self.end_scrub(at)
        self.set_status('Preview: '+combo.get_active_text()+'. Export resolution is unchanged.')

    def add_card(self,index=None):
        if self.busy:return
        if index is None:
            index=self.selected[1]+1 if self.selected and self.selected[0]=='clips' else next((i for i,row in enumerate(model.timeline(self.project)) if row['start']>=self.playhead),len(self.project['clips']))
        dialog=Gtk.Dialog(title='Insert color card',transient_for=self,modal=True)
        dialog.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Insert card',Gtk.ResponseType.OK)
        content=dialog.get_content_area();content.set_border_width(20);content.set_spacing(12)
        content.pack_start(label('A solid-color scene between your clips. Add text or a logo using Layers.'),False,False,0)
        self.new_card_color=Gtk.ColorButton();color=Gdk.RGBA();color.parse('#ffffff');self.new_card_color.set_rgba(color)
        self.new_card_color.set_title('Card color');content.pack_start(self.new_card_color,False,False,0)
        content.pack_start(label('Duration (seconds)'),False,False,0)
        self.new_card_duration=Gtk.SpinButton.new_with_range(.04,3600,.1);self.new_card_duration.set_digits(2);self.new_card_duration.set_value(3)
        content.pack_start(self.new_card_duration,False,False,0)
        dialog.show_all();response=dialog.run()
        color=self.color_hex(self.new_card_color);duration=self.new_card_duration.get_value();dialog.destroy()
        if response!=Gtk.ResponseType.OK:return
        candidate=copy.deepcopy(self.project);candidate['clips'].insert(index,model.new_card(color,duration))
        try:model.validate_project(candidate,check_files=False)
        except ValueError as error:self.error(str(error));return
        self.checkpoint();self.project=candidate;self.selected=('clips',index);self.changed()
        self.playhead=model.timeline(self.project)[index]['start'];self.cancel_scrub();self.show_selected_still()
        self.set_status('Color card inserted. Edit its color/duration, drag to move, or add text in Layers.')

    @staticmethod
    def color_hex(control):
        color=control.get_rgba()
        return '#'+''.join(f'{round(channel*255):02x}' for channel in (color.red,color.green,color.blue))

    def show_card_properties(self,item):
        self.inspector.pack_start(label('Color card','section'),False,False,0)
        self.card_color=Gtk.ColorButton();color=Gdk.RGBA();color.parse(item['color']);self.card_color.set_rgba(color);self.card_color.set_title('Card color')
        self.inspector.pack_start(self.card_color,False,False,0)
        self.inspector.pack_start(label('Duration (seconds)'),False,False,0)
        self.card_duration=Gtk.SpinButton.new_with_range(.04,model.MAX_TIMELINE_SECONDS,.1);self.card_duration.set_digits(2);self.card_duration.set_value(model.clip_duration(item))
        self.inspector.pack_start(self.card_duration,False,False,0)
        self.inspector.pack_start(label('Transition to next scene'),False,False,0)
        self.card_transition=Gtk.ComboBoxText()
        for key,title in TRANSITIONS:self.card_transition.append(key,title)
        self.card_transition.set_active_id(item['transition']);self.inspector.pack_start(self.card_transition,False,False,0)
        self.card_transition_duration=Gtk.SpinButton.new_with_range(.05,10,.05);self.card_transition_duration.set_digits(2);self.card_transition_duration.set_value(item['transition_duration'])
        self.inspector.pack_start(self.card_transition_duration,False,False,0)
        for control in (self.card_transition,self.card_transition_duration):control.set_sensitive(self.selected[1]+1<len(self.project['clips']))
        self.inspector.show_all()

    def apply_card_fields(self):
        candidate=copy.deepcopy(self.project);item=candidate['clips'][self.selected[1]]
        item.update(color=self.color_hex(self.card_color),duration=self.card_duration.get_value(),out=self.card_duration.get_value(),**{'in':0.0})
        item.update(transition=self.card_transition.get_active_id(),transition_duration=self.card_transition_duration.get_value())
        try:model.validate_project(candidate,check_files=False)
        except ValueError as error:self.error(str(error));return
        self.checkpoint();self.project=candidate;self.properties_popover.popdown();self.changed()

    def preview_zoom_changed(self,combo):
        if not self.view_lock and combo.get_active_id():
            factor=float(combo.get_active_id())
            self.canvas.set_preview_zoom(factor,reset_pan=factor==1)

    def preview_view_changed(self,factor):
        self.view_lock=True
        key={.25:'.25',.5:'.5',.75:'.75'}.get(factor,f'{factor:g}')
        self.preview_zoom_combo.set_active_id(key)
        self.preview_zoom_out.set_sensitive(factor>.25)
        self.preview_zoom_in.set_sensitive(factor<4)
        self.view_lock=False

    def update_play_button(self,playing):
        self.play_button.set_image(ui_theme.icon('media-playback-pause-symbolic' if playing else 'media-playback-start-symbolic'))

    def toggle_library(self):
        if self.library.get_visible():self.library.hide()
        else:self.library.show();self.body.set_position(280)

    def start_project(self,width,height):
        self.recovery.clear()
        self.cancel_scrub()
        self.canvas.set_still(None)
        self.canvas.reset_view()
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
        self.name_lock=False
        self.canvas.set_aspect(self.project['width'],self.project['height'])

    def format_changed(self,w):
        if self.name_lock or not w.get_active_id():return
        self.checkpoint();self.project['width'],self.project['height']=map(int,w.get_active_id().split('x'))
        self.canvas.set_aspect(self.project['width'],self.project['height']);self.changed()

    def checkpoint(self):
        self.history.append(copy.deepcopy(self.project));self.history=self.history[-60:];self.future=[]

    def changed(self):
        self.cancel_scrub()
        self.adapt_frame_rate()
        self.canvas.stop();self.canvas.composited=False;self.preview_key=None;self.playhead=0;self.update_play_button(False)
        self.preview_label.set_text('Press play to preview');self.refresh();self.show_selected_still()

    def adapt_frame_rate(self):
        for clip in self.project['clips']:
            metadata=self.media_metadata.get(clip.get('path'))
            if metadata:clip['source_fps']=metadata['fps']
        model.adapt_frame_rate(self.project)

    def refresh(self):
        self.update_title();self.refresh_assets();self.layer_tools.refresh();self.show_inspector();self.timeline.refresh()
        self.canvas.hide_placeholder=bool(self.project.get('layers'))
        self.canvas.queue_draw()
        self.clock_label.set_text(f'{time_text(self.playhead)} / {time_text(model.project_duration(self.project))}')
        self.undo_button.set_sensitive(bool(self.history));self.redo_button.set_sensitive(bool(self.future))
        self.update_row_buttons()

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
        query=self.media_search.get_text().casefold();category=self.media_tabs.get_active_id()
        for kind in ('clips','audio'):
            if category not in ('all',kind):continue
            for i,item in enumerate(self.project[kind]):
                if model.is_card(item):continue
                if query and query not in Path(item['path']).name.casefold():continue
                row=Gtk.ListBoxRow();row.get_style_context().add_class('asset')
                b=Gtk.Button();b.get_style_context().add_class('quiet');content=box(Gtk.Orientation.HORIZONTAL,8)
                if kind=='clips' and item['path'] in self.thumbs:
                    pix=self.thumbs[item['path']].scale_simple(54,34,GdkPixbuf.InterpType.BILINEAR)
                    content.pack_start(Gtk.Image.new_from_pixbuf(pix),False,False,0)
                else:content.pack_start(ui_theme.icon('video-x-generic-symbolic' if kind=='clips' else 'audio-x-generic-symbolic',Gtk.IconSize.DIALOG),False,False,0)
                text=box(spacing=3);name=label(Path(item['path']).name);name.set_ellipsize(Pango.EllipsizeMode.MIDDLE);name.set_max_width_chars(17)
                text.pack_start(name,False,False,0);text.pack_start(label(f'{"Clip" if kind=="clips" else "Audio"} {i+1} · {time_text(model.clip_duration(item))}','small'),False,False,0)
                content.pack_start(text,True,True,0);b.add(content);b.set_tooltip_text(item['path'])
                b.connect('clicked',lambda _,k=kind,n=i:self.select(k,n));row.add(b);self.assets.add(row)
        self.assets.show_all()

    def select(self,kind,index,seek=True):
        self.selected=(kind,index);self.show_inspector();self.timeline.queue_draw();self.canvas.queue_draw()
        self.layer_tools.update_overlay_controls()
        self.update_row_buttons()
        if kind in ('layers','captions'):self.media_stack.set_visible_child_name('Layers' if kind=='layers' else 'Captions')
        self.reveal_timeline_selection()
        if kind in ('layers','captions') and seek:self.seek(self.project[kind][index]['start'])
        if kind=='clips' and seek:
            self.canvas.pause();self.update_play_button(False)
            if self.preview_key or self.canvas.live:self.seek(model.timeline(self.project)[index]['start'])
            else:
                self.cancel_scrub();self.playhead=model.timeline(self.project)[index]['start'];self.show_selected_still()
                self.clock_label.set_text(f'{time_text(self.playhead)} / {time_text(model.project_duration(self.project))}')

    def selected_row_key(self):
        item=self.selected_item()
        return track_layout.key_for(self.selected[0],item) if item and self.selected else None

    def update_row_buttons(self):
        key=self.selected_row_key()
        for direction,button in ((-1,self.row_up_button),(1,self.row_down_button)):
            button.set_sensitive(not self.busy and track_layout.moved_order(self.project,key,direction) is not None)

    def move_timeline_row(self,direction):
        if self.busy:return
        order=track_layout.moved_order(self.project,self.selected_row_key(),direction)
        if order is None:return
        self.checkpoint();self.project['timeline_order']=order
        self.refresh();self.reveal_timeline_selection()
        self.set_status('Timeline row moved. Playback timing and visual stacking are unchanged.')

    def reveal_timeline_selection(self):
        selected=self.selected
        def reveal():
            if self.closed or self.selected!=selected:return False
            row=next((r for r in track_layout.rows(self.project) if r[0]==self.selected_row_key()),None)
            if row:
                vertical=self.timeline_scroll.get_vadjustment();vertical.clamp_page(row[3]-5,row[3]+row[4]+5)
            hit=next((h for h in self.timeline.hit if h[:2]==selected),None)
            if hit:
                _,_,x,y,width,height=hit
                horizontal=self.timeline_scroll.get_hadjustment()
                horizontal.clamp_page(x-5,x+min(width,horizontal.get_page_size()-10)+5)
            return False
        GLib.idle_add(reveal)

    def selected_item(self):
        if not self.selected:return None
        kind,i=self.selected
        if i>=len(self.project[kind]):self.selected=None;return None
        return self.project[kind][i]

    def show_selected_still(self):
        if self.preview_key or self.canvas.live or self.scrub_timer or self.scrub_active:return
        item=self.selected_item()
        if self.selected and self.selected[0] in ('audio','layers','captions'):
            item=None
        if not item:item=next(iter(self.project['clips']),None)
        if item and self.project['clips']:
            self.begin_scrub();self.end_scrub(self.playhead)
        else:self.canvas.set_still(None)

    def display_still(self,pix,item):
        rotation={90:GdkPixbuf.PixbufRotation.CLOCKWISE,180:GdkPixbuf.PixbufRotation.UPSIDEDOWN,
                  270:GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE}.get(item.get('rotation',0))
        if rotation is not None:pix=pix.rotate_simple(rotation)
        if item.get('flip_x'):pix=pix.flip(True)
        if item.get('flip_y'):pix=pix.flip(False)
        self.canvas.set_still(pix,item['fit'],item.get('position_x',.5),item.get('position_y',.5))

    def show_inspector(self):
        old_page=getattr(self,'properties_page','Timing')
        for child in self.inspector.get_children():self.inspector.remove(child)
        item=self.selected_item();self.fields={};self.property_pages={}
        if item and self.selected[0] in ('layers','captions'):
            self.layer_tools.show_properties();return
        ready=item is not None
        for widget in (self.apply_button,self.edit_button,self.sound_button,self.split_button,self.duplicate_button,self.remove_button):widget.set_sensitive(ready)
        self.picture_button.set_sensitive(ready and self.selected[0]=='clips')
        if not item:
            self.context_title.set_text('Select a clip to edit')
            self.properties_popover.popdown();return
        kind,i=self.selected
        self.context_title.set_text(model.item_name(item))
        if model.is_card(item):
            self.sound_button.set_sensitive(False)
            self.show_card_properties(item);return
        heading=box(Gtk.Orientation.HORIZONTAL,8)
        title=label(f'{"Video" if kind=="clips" else "Audio"} {i+1}','section')
        heading.pack_start(title,True,True,0)
        heading.pack_end(ui_theme.icon_button('window-close-symbolic',self.properties_popover.popdown,'Close properties'),False,False,0)
        self.inspector.pack_start(heading,False,False,0)
        pages=Gtk.Stack();pages.set_hhomogeneous(False);pages.set_vhomogeneous(False)
        self.properties_stack=pages
        switcher=Gtk.StackSwitcher();switcher.set_stack(pages);switcher.set_halign(Gtk.Align.CENTER)
        self.inspector.pack_start(switcher,False,False,0);self.inspector.pack_start(pages,True,True,0)
        def page(name):
            content=box(spacing=12,margin=16)
            pages.add_titled(content,name,name);self.property_pages[name]=len(self.property_pages)
            return content
        def hint(parent,text):
            caption=label(text,'dim-label');caption.set_line_wrap(True);caption.set_max_width_chars(30)
            parent.pack_start(caption,False,False,4);return caption
        def spin(parent,key,title,lo,hi,step=.1,mult=1,default=0):
            row=box(Gtk.Orientation.HORIZONTAL,12)
            caption=label(title);caption.set_line_wrap(True);caption.set_max_width_chars(20)
            row.pack_start(caption,True,True,0)
            control=Gtk.SpinButton.new_with_range(lo,max(hi,lo+.01),step)
            control.set_digits(2 if step<1 else 0);control.set_width_chars(5)
            control.set_value(item.get(key,default)*mult)
            row.pack_start(control,False,False,0);parent.pack_start(row,False,False,0)
            self.fields[key]=(control,mult)
        timing=page('Timing')
        spin(timing,'in','Source start (s)',0,item['duration'])
        spin(timing,'out','Source end (s)',.04,item['duration'])
        if kind=='audio':spin(timing,'start','Start in video (s)',0,max(0,model.project_duration(self.project)-.04))
        spin(timing,'speed','Playback speed (×)',.25,4,.05,default=1)
        hint(timing,'Drag edges to trim. Speed preserves audio pitch.')
        if kind=='clips':
            picture=page('Picture')
            self.fit=Gtk.ComboBoxText();self.fit.append('fill','Fill canvas · crop edges');self.fit.append('fit','Fit whole picture · borders')
            self.fit.set_active_id(item['fit']);picture.pack_start(self.fit,False,False,0)
            self.rotation=Gtk.ComboBoxText()
            for value in (0,90,180,270):self.rotation.append(str(value),f'Rotate {value}° clockwise')
            self.rotation.set_active_id(str(item.get('rotation',0)));picture.pack_start(self.rotation,False,False,0)
            self.flip_x=Gtk.CheckButton(label='Flip horizontally');self.flip_x.set_active(item.get('flip_x',False))
            self.flip_y=Gtk.CheckButton(label='Flip vertically');self.flip_y.set_active(item.get('flip_y',False))
            picture.pack_start(self.flip_x,False,False,0);picture.pack_start(self.flip_y,False,False,0)
            crop=Gtk.Expander(label='Crop position');crop_box=box(spacing=10,margin=8);crop.add(crop_box)
            spin(crop_box,'position_x','Horizontal (%)',0,100,1,100,default=.5)
            spin(crop_box,'position_y','Vertical (%)',0,100,1,100,default=.5)
            picture.pack_start(crop,False,False,0)
            spin(picture,'video_fade_in','Fade picture in (s)',0,model.clip_duration(item))
            spin(picture,'video_fade_out','Fade picture out (s)',0,model.clip_duration(item))
        sound=page('Sound')
        spin(sound,'volume','Volume (%)',0,200,1,100,default=1)
        spin(sound,'fade_in','Fade audio in (s)',0,model.clip_duration(item))
        spin(sound,'fade_out','Fade audio out (s)',0,model.clip_duration(item))
        hint(sound,'Fades soften the beginning and end of the sound.')
        hint(sound,audio_effects.summary(item))
        sound.pack_start(button('Voice effects…',self.edit_voice_effects),False,False,0)
        if kind=='clips':
            self.extract_button=ui_theme.icon_button('audio-x-generic-symbolic',self.extract_audio,'Separate this clip’s audio into its own track',label='Extract audio')
            self.extract_button.set_sensitive(self.extraction_available(item))
            sound.pack_start(self.extract_button,False,False,4)
            transition=page('Transition')
            self.transition=Gtk.ComboBoxText()
            for key,title in TRANSITIONS:self.transition.append(key,title)
            self.transition.set_active_id(item['transition']);transition.pack_start(self.transition,False,False,0)
            spin(transition,'transition_duration','Duration (s)',.05,3,.05)
            if i+1>=len(self.project['clips']):
                self.transition.set_sensitive(False)
                self.fields['transition_duration'][0].set_sensitive(False)
                hint(transition,'Add another clip to create a transition.')
            else:
                overlap_hint=hint(transition,'')
                def update_overlap(*args):
                    candidate=copy.deepcopy(self.project)
                    edited=candidate['clips'][i]
                    for key in ('in','out','speed','transition_duration'):edited[key]=self.fields[key][0].get_value()
                    edited['transition']=self.transition.get_active_id()
                    actual=model.timeline(candidate)[i]['overlap']
                    overlap_hint.set_text(f'Actual overlap: {actual:.2f} s. Limited to half of either adjacent clip.')
                self.transition.connect('changed',update_overlap)
                for key in ('in','out','speed','transition_duration'):self.fields[key][0].connect('value-changed',update_overlap)
                update_overlap()
        self.inspector.show_all()
        if old_page in self.property_pages:pages.set_visible_child_name(old_page)

    def open_properties(self,page='Timing',anchor=None,rectangle=None):
        if not self.selected_item() or self.busy:return
        self.properties_scroll.set_max_content_height(max(240,self.get_allocated_height()-310))
        self.properties_page=page
        self.properties_popover.set_relative_to(anchor or self.edit_button)
        if rectangle:self.properties_popover.set_pointing_to(rectangle)
        else:
            allocation=(anchor or self.edit_button).get_allocation()
            rect=Gdk.Rectangle();rect.x=0;rect.y=0;rect.width=allocation.width;rect.height=allocation.height
            self.properties_popover.set_pointing_to(rect)
        if page in self.property_pages:self.properties_stack.set_visible_child_name(page)
        self.properties_popover.show_all();self.properties_popover.popup()

    def show_transition(self,index,rectangle=None):
        if not 0<=index<len(self.project['clips'])-1:return
        self.select('clips',index,seek=False)
        self.open_properties('Transition',self.timeline,rectangle)

    def context_menu(self,kind,index,event=None):
        if self.busy:return
        self.select(kind,index,seek=False)
        menu=Gtk.Menu()
        def action(title,callback,enabled=True):
            item=Gtk.MenuItem(label=title);item.set_sensitive(enabled)
            item.connect('activate',lambda _:callback());menu.append(item)
        action('Edit card…' if model.is_card(self.selected_item()) else 'Edit…',lambda:self.open_properties('Timing'))
        card=model.is_card(self.selected_item())
        if kind in ('clips','audio') and not card:
            action('Volume and fades…',lambda:self.open_properties('Sound'))
            action('Voice effects…',self.edit_voice_effects)
        if kind=='layers':
            action('Bring forward',lambda:self.layer_tools.reorder(1),index+1<len(self.project['layers']))
            action('Send backward',lambda:self.layer_tools.reorder(-1),index>0)
        if kind=='clips':
            action('Insert color card before…',lambda:self.add_card(index))
            action('Insert color card after…',lambda:self.add_card(index+1))
            if not card:action('Unmute' if self.selected_item()['volume']==0 else 'Mute',self.toggle_mute)
            if not card:action('Extract audio',self.extract_audio,self.extraction_available(self.selected_item()))
            action('Transition to next clip…',lambda:self.open_properties('Transition'),index+1<len(self.project['clips']))
            action('Move left',lambda:self.move(-1),index>0)
            action('Move right',lambda:self.move(1),index+1<len(self.project['clips']))
        menu.append(Gtk.SeparatorMenuItem())
        action('Split at playhead',self.split_selected,kind!='captions')
        action('Duplicate',self.duplicate)
        action('Delete',self.remove)
        menu.show_all();self.active_context_menu=menu
        if isinstance(event,(Gdk.Event,Gdk.EventButton)):menu.popup_at_pointer(event)
        else:menu.popup_at_widget(self.timeline,Gdk.Gravity.NORTH_WEST,Gdk.Gravity.SOUTH_WEST,None)
        return menu

    def edit_voice_effects(self):
        if self.selected_item() and model.is_card(self.selected_item()):return
        from .audio_effects_ui import edit_voice_effects
        edit_voice_effects(self)

    def extraction_available(self,item):
        return (not model.is_card(item) and self.media_metadata.get(item['path'],{}).get('has_audio') is not False
                and not any(a.get('source_clip_id')==item['id'] for a in self.project['audio']))

    def toggle_mute(self):
        item=self.selected_item()
        if not item or self.busy:return
        self.checkpoint()
        if item['volume']>0:
            self.mute_volumes[item['id']]=item['volume'];item['volume']=0
        else:item['volume']=self.mute_volumes.get(item['id'],1)
        self.changed()

    def extract_audio(self):
        if self.busy or not self.selected or self.selected[0]!='clips':return
        index=self.selected[1];snapshot=copy.deepcopy(self.project)
        self.properties_popover.popdown();self.canvas.pause();self.set_busy(True);self.set_status('Separating audio from the original clip…')
        def finish(project,error):
            if self.closed:return False
            self.set_busy(False)
            if error:self.error(str(error));return False
            self.checkpoint();self.project=project;self.selected=('audio',len(project['audio'])-1)
            self.changed();self.load_asset(project['audio'][-1],'audio')
            self.set_status('Audio extracted into its own track. The video’s sound is muted to avoid doubling.');return False
        def work():
            try:project=model.detach_audio(snapshot,index);error=None
            except Exception as e:project=None;error=e
            GLib.idle_add(finish,project,error)
        self.pool.submit(work)

    def apply_fields(self):
        original=self.selected_item()
        if not original:return
        if model.is_card(original):self.apply_card_fields();return
        if self.selected[0] in ('layers','captions'):self.layer_tools.apply();return
        item=copy.deepcopy(original)
        for key,(w,mult) in self.fields.items():item[key]=w.get_value()/mult
        if item['out']-item['in']<.04:
            self.error('The source end must be after the source start.');return
        if self.selected[0]=='clips':
            item['fit']=self.fit.get_active_id();item['transition']=self.transition.get_active_id()
            item['rotation']=int(self.rotation.get_active_id());item['flip_x']=self.flip_x.get_active();item['flip_y']=self.flip_y.get_active()
        candidate=copy.deepcopy(self.project);kind,i=self.selected;candidate[kind][i]=item
        try:model.validate_project(candidate,check_files=False)
        except ValueError as e:self.error(str(e));return
        self.checkpoint();self.project=candidate;self.properties_popover.popdown();self.changed();self.set_status('Changes applied. Press Preview to see and hear the full video.')

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

    def split_selected(self):
        if self.busy or not self.selected_item():return
        kind,i=self.selected;at=self.playhead
        if kind in ('layers','captions'):
            item=self.selected_item()
            if not item['start']+.04<at<item['end']-.04:self.set_status('Place the playhead inside the selected layer.');return
            import uuid
            self.checkpoint();clone=copy.deepcopy(item);clone['id']=uuid.uuid4().hex;clone['start']=at;item['end']=at
            if item.get('kind')=='video':clone['in']=item.get('in',0)+(at-item['start'])*item.get('speed',1)
            self.project[kind].insert(i+1,clone);self.selected=(kind,i+1);self.changed();self.seek(at);return
        try:project=model.split_item(self.project,kind,i,at)
        except ValueError as e:self.set_status(str(e));return
        self.checkpoint();self.project=project;self.selected=(kind,i+1);self.changed()
        self.playhead=at;self.refresh();self.set_status('Split at the playhead. Each part can be edited independently.')

    def zoom(self,value):
        self.timeline.scale=value;self.timeline.refresh()

    def import_dialog(self,kind):
        dialog=file_chooser(self,'Import clips' if kind=='clips' else 'Add soundtracks',Gtk.FileChooserAction.OPEN,'Add files')
        dialog.set_select_multiple(True)
        for filt in media_filters(kind):dialog.add_filter(filt)
        paths=dialog.get_filenames() if dialog.run()==Gtk.ResponseType.ACCEPT else [];dialog.destroy()
        if paths:self.import_paths(paths,kind)

    def drop_files(self,w,context,x,y,data,info,timestamp):
        if self.busy:Gtk.drag_finish(context,False,False,timestamp);return
        paths=[unquote(urlparse(uri).path) for uri in data.get_uris() if urlparse(uri).scheme=='file']
        Gtk.drag_finish(context,bool(paths),False,timestamp)
        if paths:self.import_paths(paths,None)

    def import_paths(self,paths,kind=None):
        if self.busy:return
        self.set_busy(True);self.set_status('Reading local media…')
        layer_start,layer_end=self.layer_tools.interval()
        def work():
            items=[];errors=[]
            for path in paths:
                try:
                    if kind is None and Path(path).suffix.lower() in ('.png','.jpg','.jpeg','.webp','.svg','.bmp','.tiff','.tif'):
                        pix=GdkPixbuf.Pixbuf.new_from_file(path)
                        metadata={'width':pix.get_width(),'height':pix.get_height()}
                        item=model.new_visual(path,start=layer_start,end=layer_end)
                        item['height']=min(.8,item['width']*self.project['width']/self.project['height']*pix.get_height()/pix.get_width())
                        items.append(('layers',item,metadata));continue
                    metadata=engine.probe(path)
                    if metadata['duration']<.04:raise ValueError('Choose a file at least 0.04 seconds long.')
                    actual=kind or ('clips' if metadata['has_video'] else 'audio')
                    if actual=='clips' and not metadata['has_video']:raise ValueError('This file has no video track.')
                    if actual=='audio' and not metadata['has_audio']:raise ValueError('This file has no audio track.')
                    item=model.new_clip(path,metadata) if actual=='clips' else model.new_audio(path,metadata)
                    items.append((actual,item,metadata))
                except Exception as e:errors.append(f'{Path(path).name}: {e}')
            GLib.idle_add(self.finish_import,items,errors)
        self.pool.submit(work)

    def finish_import(self,items,errors):
        if self.closed:return False
        self.set_busy(False)
        if items:
            self.checkpoint()
            for kind,item,metadata in items:
                self.media_metadata[item['path']]=metadata
                self.project[kind].append(item);self.selected=(kind,len(self.project[kind])-1)
                if kind=='layers':self.layer_tools.load_asset(item)
                else:self.load_asset(item,kind)
            self.stack.set_visible_child_name('editor');self.changed()
            self.set_status(f'Added {len(items)} local file(s). Drag clips to reorder; select one to adjust it.')
        if errors:self.error('\n'.join(errors))
        return False

    def load_asset(self,item,kind):
        if model.is_card(item):return
        path=item['path']
        if kind=='clips' and path in self.thumbs and path in self.media_metadata:
            self.adapt_frame_rate();return
        if kind=='audio' and path in self.waves:return
        def work():
            try:
                if kind=='clips':
                    if path not in self.media_metadata:GLib.idle_add(self.media_info_ready,path,engine.probe(path))
                    target=self.temp/(hashlib.sha256(path.encode()).hexdigest()+'.jpg')
                    engine.thumbnail(path,str(target),at=item['in'],width=640,height=360)
                    GLib.idle_add(self.asset_ready,path,kind,str(target))
                else:GLib.idle_add(self.asset_ready,path,kind,engine.waveform(path,max_points=600))
            except Exception:pass
        self.pool.submit(work)

    def media_info_ready(self,path,metadata):
        if self.closed:return False
        self.media_metadata[path]=metadata
        old_fps=self.project['fps'];self.adapt_frame_rate()
        if self.project['fps']!=old_fps:
            self.changed()
        item=self.selected_item()
        if item and self.selected[0]=='clips' and item.get('path')==path and hasattr(self,'extract_button'):
            self.extract_button.set_sensitive(self.extraction_available(item))
        return False

    def asset_ready(self,path,kind,data):
        if self.closed:return False
        try:
            if kind=='clips':self.thumbs[path]=GdkPixbuf.Pixbuf.new_from_file(data)
            else:self.waves[path]=data
            self.refresh_assets();self.timeline.queue_draw();self.show_selected_still()
        except Exception:pass
        return False

    def on_position(self,seconds,duration):
        if self._drag_scrubbing or self._scrub_settling or self.scrub_timer:return
        if self._position_target is not None:
            if abs(seconds-self._position_target)>.15 and GLib.get_monotonic_time()<self._position_target_until:return
            self._position_target=None
        self.playhead=seconds;self.clock_label.set_text(f'{time_text(seconds)} / {time_text(model.project_duration(self.project))}');self.timeline.queue_draw();self.canvas.queue_draw()

    def playback_error(self,message):
        self.update_play_button(False);self.set_status(message)

    def toggle_play(self):
        if self.busy or not self.project['clips']:return
        if not self.preview_key and not self.canvas.live:self.preview();return
        if self.canvas.playing:self.canvas.pause();self.update_play_button(False)
        else:
            if self.playhead>=model.project_duration(self.project)-.1:self.canvas.seek(0)
            self.canvas.play();self.update_play_button(True)

    def seek(self,seconds,source=False):
        if self.busy:return
        # A new seek supersedes a paused-frame request, including one started
        # by selection or a quality change. Its callback must not stop playback.
        if self._scrub_settling and not self._drag_scrubbing:self.cancel_scrub()
        seconds=max(0,min(seconds,max(0,model.project_duration(self.project)-1/self.project['fps'])))
        self.playhead=seconds;self.clock_label.set_text(f'{time_text(seconds)} / {time_text(model.project_duration(self.project))}');self.timeline.queue_draw();self.canvas.queue_draw()
        self._position_target=seconds
        self._position_target_until=GLib.get_monotonic_time()+1000000
        if self._drag_scrubbing and not self.preview_key:
            self._drag_frame_serial+=1
            self._drag_frame_pending=(self._drag_frame_serial,seconds)
            self.start_drag_frame();return
        if self.preview_key:self.canvas.seek(seconds);return
        self.scrub_serial+=1
        self.scrub_active=True
        if self.scrub_timer:GLib.source_remove(self.scrub_timer)
        self.scrub_timer=GLib.timeout_add(80,self.source_scrub_frame if source else self.scrub_frame,self.scrub_serial,seconds)

    def cancel_scrub(self):
        self.scrub_serial+=1;self.scrub_active=False
        self._drag_scrubbing=False;self._scrub_settling=False;self._position_target=None;self._drag_frame_pending=None
        if self._drag_frame_cancel:self._drag_frame_cancel.set()
        if self.scrub_timer:GLib.source_remove(self.scrub_timer);self.scrub_timer=0

    def begin_scrub(self):
        playing=self.canvas.playing
        self.cancel_scrub();self._drag_scrubbing=True;self.scrub_active=True;self._scrub_resume=playing
        self.canvas.pause();self.update_play_button(False)

    def end_scrub(self,seconds):
        resume=self._scrub_resume
        if not resume and not self.preview_key:
            self.seek(seconds)
            self._drag_scrubbing=False;self._scrub_settling=True
            return
        self.cancel_scrub()
        self.seek(seconds)
        if resume:
            if self.preview_key:self.canvas.play();self.update_play_button(True)
            else:self.preview()

    def start_drag_frame(self):
        if self.closed or not (self._drag_scrubbing or self._scrub_settling) or self._drag_frame_busy or not self._drag_frame_pending:return
        serial,seconds=self._drag_frame_pending;self._drag_frame_pending=None
        self._drag_frame_busy=True;cancel=threading.Event();self._drag_frame_cancel=cancel
        snapshot=copy.deepcopy(self.project);duration=model.project_duration(snapshot);preview_edge=self.canvas.preview_edge
        def finish(picture,error):
            self._drag_frame_busy=False;self._drag_frame_cancel=None
            if self.closed:return False
            current=serial==self._drag_frame_serial
            if not cancel.is_set() and (self._drag_scrubbing or (self._scrub_settling and current)):
                if picture:
                    self.canvas.set_composed_frame(picture,duration)
                    self.preview_label.set_text('Scrubbing' if self._drag_scrubbing else 'Paused preview')
                elif error and not isinstance(error,engine.RenderCancelled):self.set_status(str(error))
                if self._scrub_settling and current:
                    self._scrub_settling=False;self._position_target=None
            # Finish the current frame before taking the newest pointer time.
            # This keeps continuous movement from starving the frame worker.
            self.start_drag_frame()
            return False
        def work():
            try:picture=engine.preview_frame(snapshot,seconds,cancel,preview_edge=preview_edge);error=None
            except Exception as problem:picture=None;error=problem
            GLib.idle_add(finish,picture,error)
        self.frame_pool.submit(work)

    def scrub_frame(self,serial,seconds):
        self.scrub_timer=0
        if self.closed or serial!=self.scrub_serial:return False
        if self.project['clips']:
            playing=self.canvas.playing
            self.canvas.load_project(self.project,seconds,autoplay=playing)
            self._position_target=None
            self.update_play_button(playing)
            self.preview_label.set_text('Live preview')
        return False

    def source_scrub_frame(self,serial,seconds):
        self.scrub_timer=0
        if self.closed or serial!=self.scrub_serial:return False
        candidates=[(c,t) for c,t in zip(self.project['clips'],model.timeline(self.project)) if t['start']<=seconds<t['start']+t['duration']]
        if not candidates:return False
        if model.is_card(candidates[-1][0]):
            pix=GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB,False,8,64,64)
            pix.fill(int(candidates[-1][0]['color'][1:]+'ff',16))
            self.display_still(pix,candidates[-1][0]);return False
        item,timing=candidates[-1];item=copy.deepcopy(item)
        at=item['in']+(seconds-timing['start'])*item.get('speed',1)
        target=self.temp/f'scrub-{serial}.png'
        overlays=[copy.deepcopy(layer) for layer in self.project.get('layers',[]) if layer['kind']=='video' and layer['start']<=seconds<layer['end']]
        overlay_frames=[];preview_edge=self.canvas.preview_edge or max(self.project['width'],self.project['height'])
        def finish(error):
            if not self.closed and serial==self.scrub_serial and not self.preview_key and not self.canvas.live:
                if error:self.set_status(str(error))
                else:
                    for layer_id,path in overlay_frames:
                        self.layer_tools.assets[layer_id]=GdkPixbuf.Pixbuf.new_from_file(str(path))
                    self.display_still(GdkPixbuf.Pixbuf.new_from_file(str(target)),item)
                    self.preview_label.set_text('Editing frame · press play for full mix')
            target.unlink(missing_ok=True)
            for _,path in overlay_frames:path.unlink(missing_ok=True)
            return False
        def work():
            if serial!=self.scrub_serial:return
            try:
                engine.thumbnail(item['path'],target,at=at,width=preview_edge,height=preview_edge)
                for layer in overlays:
                    if serial!=self.scrub_serial:break
                    frame=self.temp/f'overlay-{layer["id"]}-{serial}.png'
                    position=min(layer['duration']-min(.04,layer['duration']/2),layer.get('in',0)+(seconds-layer['start'])*layer.get('speed',1))
                    engine.thumbnail(layer['path'],frame,at=max(0,position),width=preview_edge,height=preview_edge)
                    overlay_frames.append((layer['id'],frame))
                error=None
            except Exception as e:error=e
            GLib.idle_add(finish,error)
        self.pool.submit(work)
        return False

    def preview(self):
        if not self.project['clips'] or self.busy:return
        self.cancel_scrub();self.adapt_frame_rate();self.preview_key=None
        start=0 if self.playhead>=model.project_duration(self.project)-.1 else self.playhead
        self.canvas.load_project(self.project,start,autoplay=True)
        self.update_play_button(self.canvas.playing)
        self.preview_label.set_text('Live preview')
        self.set_status('Live playback. If your project stutters, use Project → Render smooth preview.')

    def render_preview(self):
        if not self.project['clips'] or self.busy:return
        self.adapt_frame_rate();self.cancel_scrub()
        render_project={k:v for k,v in self.project.items() if k!='timeline_order'}
        key=hashlib.sha256((json.dumps(render_project,sort_keys=True)+str(self.canvas.preview_edge)).encode()).hexdigest()
        target=self.temp/(key+'.mp4')
        if target.exists():self.preview_done(str(target),key);return
        self.render_job(str(target),True,lambda path:self.preview_done(path,key))

    def preview_done(self,path,key):
        self.preview_key=key;self.canvas.load(path,autoplay=True);self.canvas.composited=True;self.update_play_button(True)
        self.preview_label.set_text('Rendered preview');self.set_status('Preview ready. Click the timeline ruler to seek.')

    def export(self):
        if self.busy or not self.project['clips']:return
        self.adapt_frame_rate()
        dialog=Gtk.Dialog(title='Export your video',transient_for=self,modal=True)
        dialog.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Choose save location',Gtk.ResponseType.OK)
        content=dialog.get_content_area();content.set_spacing(12);content.set_border_width(22)
        content.pack_start(label(f'{self.project["width"]} × {self.project["height"]} · {self.project["fps"]:g} fps','section'),False,False,0)
        quality=Gtk.ComboBoxText()
        for key,text in [('master','Maximum quality · MP4'),('high','High quality · MP4'),('smaller','Smaller file · MP4'),('lossless','Lossless master · MKV · very large')]:quality.append(key,text)
        quality.set_active_id('master');self.export_quality_combo=quality;content.pack_start(quality,False,False,0)
        hint=label('','muted');hint.set_line_wrap(True);hint.set_max_width_chars(58);content.pack_start(hint,False,False,0)
        def update_quality_hint(*args):
            hint.set_text('Lossless FFV1 video and float PCM audio preserve the composed output. Canvas scaling, effects and 8-bit YUV color conversion still apply. Very large files; use MP4 for social uploads.' if quality.get_active_id()=='lossless' else 'Export reads your originals at the selected canvas size. Maximum quality minimizes compression changes; MP4 is not lossless. Preview zoom and resolution do not affect export.')
        quality.connect('changed',update_quality_hint);update_quality_hint()
        dialog.show_all();response=dialog.run();q=quality.get_active_id();dialog.destroy()
        if response!=Gtk.ResponseType.OK:return
        save=file_chooser(self,'Save finished video',Gtk.FileChooserAction.SAVE,'Export')
        save.set_do_overwrite_confirmation(True)
        safe_name=''.join(c for c in self.project['name'] if c.isalnum() or c in ' -_').strip() or 'My video'
        extension='.mkv' if q=='lossless' else '.mp4'
        save.set_current_name(safe_name+extension)
        path=save.get_filename() if save.run()==Gtk.ResponseType.ACCEPT else None;save.destroy()
        if not path:return
        if not path.lower().endswith(extension):
            path+=extension
            if Path(path).exists() and not self.confirm_replace(path):return
        self.render_job(path,False,self.export_done,quality=q)

    def render_job(self,path,preview,done,quality='high'):
        try:model.validate_project(self.project)
        except Exception as e:self.error(str(e));return
        self.canvas.pause();self.update_play_button(False);self.set_busy(True);snapshot=copy.deepcopy(self.project)
        if self.project_path:snapshot['_project_path']=str(Path(self.project_path).resolve())
        self.cancel_event=threading.Event();preview_edge=self.canvas.preview_edge
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
            try:engine.render(snapshot,path,preview=preview,progress=progress,cancel=self.cancel_event,quality=quality,preview_edge=preview_edge);error=None
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
            dialog=file_chooser(self,'Save project',Gtk.FileChooserAction.SAVE,'Save')
            dialog.set_do_overwrite_confirmation(True);dialog.set_current_name('My story.clipcanvas')
            path=dialog.get_filename() if dialog.run()==Gtk.ResponseType.ACCEPT else None;dialog.destroy()
            if not path:return False
            if not path.endswith('.clipcanvas'):
                path+='.clipcanvas'
                if Path(path).exists() and not self.confirm_replace(path):return False
        try:
            model.save_project(self.project,path);self.project_path=path;self.saved=json.dumps(self.project,sort_keys=True);self.update_title()
            self.recovery.clear();self.set_status('Project saved. Use Project → Save portable copy to include your media.');return True
        except Exception as e:self.error(str(e));return False

    def open_project(self,path=None):
        if self.busy or not self.confirm_discard():return
        if not path:
            dialog=file_chooser(self,'Open project',Gtk.FileChooserAction.OPEN,'Open')
            filt=Gtk.FileFilter();filt.set_name('ClipCanvas projects');filt.add_pattern('*.clipcanvas');filt.add_pattern('*.json');dialog.add_filter(filt)
            path=dialog.get_filename() if dialog.run()==Gtk.ResponseType.ACCEPT else None;dialog.destroy()
        if not path:return
        try:project=model.load_project(path)
        except Exception as e:self.error(str(e));return
        self.canvas.set_still(None);self.canvas.reset_view();self.project=project;self.project_path=path;self.saved=json.dumps(project,sort_keys=True)
        self.recovery.clear();self.cancel_scrub()
        self.history=[];self.future=[];self.selected=None;self.preview_key=None;self.playhead=0
        self.sync_project_widgets();self.stack.set_visible_child_name('editor');self.refresh()
        for kind in ('clips','audio'):
            for item in project[kind]:self.load_asset(item,kind)
        for item in project.get('layers',[]):self.layer_tools.load_asset(item)
        self.show_selected_still()
        self.adapt_frame_rate();self.set_status('Project opened. Press play to preview.')

    def autosave(self):
        if self.closed:return False
        try:
            if json.dumps(self.project,sort_keys=True)!=self.saved:
                self.recovery.write(self.project,self.project_path)
            else:self.recovery.clear()
        except Exception as e:self.set_status(f'Could not save recovery copy: {e}')
        return True

    def recover_project(self):
        if self.busy or not self.confirm_discard():return
        choices=[]
        for path in self.recovery.available()[:50]:
            try:
                project,original=self.recovery.read(path);choices.append((path,project,original))
            except (ValueError,OSError):continue
        if not choices:self.set_status('No recoverable unsaved projects were found.');return
        dialog=Gtk.Dialog(title='Recover an unsaved project',transient_for=self,modal=True)
        dialog.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Recover',Gtk.ResponseType.OK)
        content=dialog.get_content_area();content.set_border_width(20);content.set_spacing(10)
        content.pack_start(label('Recovery copies are saved every 30 seconds.','muted'),False,False,0)
        combo=Gtk.ComboBoxText()
        from datetime import datetime
        for i,(path,project,_) in enumerate(choices):
            date=datetime.fromtimestamp(path.stat().st_mtime).strftime('%d %b %H:%M')
            combo.append(str(i),f'{project["name"]} · {date}')
        combo.set_active(0);content.pack_start(combo,False,False,0);dialog.show_all()
        response=dialog.run();index=combo.get_active();dialog.destroy()
        if response!=Gtk.ResponseType.OK:return
        old,project,original=choices[index]
        self.cancel_scrub();self.canvas.set_still(None);self.canvas.reset_view();self.project=project;self.project_path=original;self.saved=''
        self.selected=None;self.history=[];self.future=[];self.preview_key=None;self.playhead=0
        self.sync_project_widgets();self.stack.set_visible_child_name('editor');self.refresh()
        for kind in ('clips','audio'):
            for item in project[kind]:self.load_asset(item,kind)
        # Preserve the previous snapshot until a new one has been saved successfully.
        for item in project.get('layers',[]):self.layer_tools.load_asset(item)
        try:self.recovery.write(project,original);old.unlink()
        except OSError as e:self.set_status(f'Recovered; save your project now: {e}');return
        self.set_status('Unsaved project recovered. Save to keep it, or relink any missing media.')

    def collect_media(self):
        if self.busy:return
        dialog=file_chooser(self,'Save portable copy in a new folder',Gtk.FileChooserAction.SAVE,'Collect project and media')
        dialog.set_current_name('ClipCanvas portable project')
        path=dialog.get_filename() if dialog.run()==Gtk.ResponseType.ACCEPT else None;dialog.destroy()
        if not path:return
        snapshot=copy.deepcopy(self.project);self.set_busy(True);self.set_status('Copying original media into the portable project folder…')
        def finish(result,error):
            if self.closed:return False
            self.set_busy(False)
            if error:self.error(str(error))
            else:self.set_status(f'Portable copy ready: {result}. Move the entire folder to your other computer.')
            return False
        def work():
            try:result=storage.collect_project(snapshot,path);error=None
            except Exception as e:result=None;error=e
            GLib.idle_add(finish,result,error)
        self.pool.submit(work)

    def relink_media(self):
        if self.busy:return
        missing=sorted({item['path'] for item in model.media_items(self.project) if not Path(item['path']).is_file()})
        if not missing:self.set_status('All source media files are available.');return
        replacements={}
        for old in missing:
            dialog=file_chooser(self,f'Locate {Path(old).name}',Gtk.FileChooserAction.OPEN,'Use this file')
            path=dialog.get_filename() if dialog.run()==Gtk.ResponseType.ACCEPT else None;dialog.destroy()
            if not path:return
            replacements[old]=path
        snapshot=copy.deepcopy(self.project);self.set_busy(True);self.set_status('Checking replacement media…')
        def finish(project,error):
            self.set_busy(False)
            if error:self.error(str(error));return False
            self.checkpoint();self.project=project;self.changed()
            for kind in ('clips','audio'):
                for item in project[kind]:self.load_asset(item,kind)
            self.layer_tools.assets.clear()
            for item in project.get('layers',[]):self.layer_tools.load_asset(item)
            self.set_status('Media relinked. Save the project to keep the new locations.');return False
        def work():
            try:
                image_paths={i['path'] for i in snapshot.get('layers',[]) if i['kind']=='image'}
                metadata={old:engine.probe(new) for old,new in replacements.items() if old not in image_paths}
                for old in image_paths & replacements.keys():GdkPixbuf.Pixbuf.new_from_file(replacements[old])
                for kind in ('clips','audio'):
                    for item in snapshot[kind]:
                        if model.is_card(item):continue
                        old=item['path']
                        if old not in replacements:continue
                        meta=metadata[old]
                        if not meta['has_video' if kind=='clips' else 'has_audio'] or meta['duration']+.001<item['out']:
                            raise ValueError(f'{Path(replacements[old]).name} does not contain the required media or is too short for the saved trim points.')
                        item['path']=str(Path(replacements[old]).resolve());item['duration']=meta['duration']
                        if kind=='clips':item['source_fps']=meta['fps']
                for item in snapshot.get('layers',[]):
                    old=item.get('path')
                    if old not in replacements:continue
                    if item['kind']=='video':
                        meta=metadata[old]
                        if not meta['has_video'] or meta['duration']<=item.get('in',0):raise ValueError('The replacement overlay video is too short or has no video stream.')
                        item['duration']=meta['duration']
                    item['path']=str(Path(replacements[old]).resolve())
                model.validate_project(snapshot);error=None
            except Exception as e:error=e
            GLib.idle_add(finish,snapshot,error)
        self.pool.submit(work)

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
        dialog.format_secondary_text('1. Choose your canvas, add videos and drag to reorder.\n2. Select a clip, then Edit, Picture or Audio for its settings.\n3. Click a round clip join to add or change a transition.\n4. Right-click a clip to Extract audio, Mute, Split or move it.\n5. Add WAV or other soundtracks; select and drag fade handles. Right-click Voice effects for echo, gain, tone and compression; compare with/without effects before applying.\n6. In Layers, add text, images/logos, or overlay video. Text has four editable style presets. Select a logo for opacity and Full video; drag on the canvas to position.\n7. In Captions, Generate from speech, then select a cue to correct words/timing. Import/export SRT is also available.\n8. Select a timeline item and use Up / Down to arrange its row.\n9. Preview your complete mix, then Export.\n\nDrag edges to trim, the ruler to scrub, or the divider to resize the timeline.\nThe folder button shows or hides your media browser.\n\nCtrl+S Save · Ctrl+O Open · Ctrl+Z Undo · Ctrl+Shift+Z Redo · S Split\n\nProject menu: portable copy, relink media, recover unsaved edits.\nRecovery copies save every 30 seconds. Original media is not backed up.\nScrubbing shows source frames; Preview includes effects and mixed sound.\nExports read originals. Soundtracks stop at the video end.\n\nClipCanvas '+__version__+' · Native, local, and free.')
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
        if name=='space' and not isinstance(focus,(Gtk.Entry,Gtk.SpinButton,Gtk.TextView)):
            self.toggle_play();return True
        if name=='s' and not ctrl and not isinstance(focus,(Gtk.Entry,Gtk.SpinButton,Gtk.TextView)):
            self.split_selected();return True
        return False

    def close_request(self,*args):
        if self.busy:self.set_status('Finish or cancel the current operation before closing.');return True
        if not self.confirm_discard():return True
        self.cleanup();Gtk.main_quit();return False

    def cleanup(self):
        for timer in (self.autosave_timer,self.scrub_timer):
            if timer:GLib.source_remove(timer)
        self.recovery.clear();self.cancel_scrub()
        self.closed=True;self.canvas.stop();self.pool.shutdown(wait=False,cancel_futures=True)
        self.frame_pool.shutdown(wait=False,cancel_futures=True)
        # Workers may still be finishing thumbnails; remove their temp directory after they exit.
        def clean():self.pool.shutdown(wait=True);self.frame_pool.shutdown(wait=True);shutil.rmtree(self.temp,ignore_errors=True)
        threading.Thread(target=clean,daemon=True).start()

def main():
    if not Gdk.Screen.get_default():
        print('ClipCanvas needs an Ubuntu desktop session.',file=sys.stderr);return 1
    provider=ui_theme.install()
    GLib.set_prgname('clipcanvas');GLib.set_application_name('ClipCanvas')
    window=ClipCanvasWindow()
    if len(sys.argv)>1 and Path(sys.argv[1]).is_file():window.open_project(sys.argv[1])
    Gtk.main();return 0

if __name__=='__main__':raise SystemExit(main())
