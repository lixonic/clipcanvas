#!/usr/bin/env python3
"""Capture the effect UI on an isolated synthetic project, never user media.

Run from the repository with the Ubuntu system Python in a graphical session:
    DCONF_BACKEND=memory /usr/bin/python3 tools/qa_effects.py
"""
import argparse
from pathlib import Path
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import cairo
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk,Gdk
from clipcanvas import model,ui_theme
from clipcanvas.app import ClipCanvasWindow


def pump(seconds=.3):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        while Gtk.events_pending():Gtk.main_iteration_do(False)
        time.sleep(.01)


def screenshot(window,path):
    pump(.5)
    pixbuf=Gdk.pixbuf_get_from_window(window.get_window(),0,0,window.get_allocated_width(),window.get_allocated_height())
    if pixbuf is None:raise RuntimeError('Could not capture the isolated QA window.')
    pixbuf.savev(str(path),'png',[],[]);print(path)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',type=Path,default=ROOT/'docs'/'images')
    options=parser.parse_args();options.output_dir.mkdir(parents=True,exist_ok=True)
    if not Gtk.init_check()[0]:raise SystemExit('A graphical desktop session is required.')
    with tempfile.TemporaryDirectory(prefix='clipcanvas-effects-qa-') as directory:
        window=ClipCanvasWindow(recovery_dir=Path(directory)/'recovery')
        provider=ui_theme.install(window)
        try:
            window.start_project(1920,1080);window.project['name']='Effect tools · local demo'
            colours=['#1c2551','#435996','#c77764','#243c54']
            window.project['clips']=[model.new_card(colour,8) for colour in colours]
            window.project['clips'][0].update(transition='fade',transition_duration=.5)
            window.project['clips'][1].update(transition='colour_wipe',transition_duration=.65)
            logo=Path(directory)/'logo.png';surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,240,240);cr=cairo.Context(surface)
            cr.set_source_rgb(.55,.24,1);cr.arc(120,120,110,0,6.284);cr.fill()
            cr.set_source_rgb(1,1,1);cr.select_font_face('Sans',cairo.FONT_SLANT_NORMAL,cairo.FONT_WEIGHT_BOLD);cr.set_font_size(100)
            cr.move_to(45,155);cr.show_text('CC');surface.write_to_png(str(logo))
            title=model.new_text('Your story, in motion',0,8);title.update(y=.42,font_size=110,outline_width=0)
            title['animation'].update(enter='rise',exit='fade',duration=.5,text_effect='highlight');title['match_id']='story-title'
            title['sound_effect'].update(preset='pop',volume=.35)
            image=model.new_visual(logo,'image',0,8,{'width':240,'height':240});image.update(x=.83,y=.8,width=.12,height=.21,opacity=.8)
            image['animation'].update(enter='pop',exit='fade')
            window.project['layers']=[title,image]
            window.project['captions']=[model.new_caption('Made on your computer. No uploads.',1,6)]
            window.changed();window.resize(1380,900);window.present();pump()
            window.show_transition(1);screenshot(window,options.output_dir/'transition-effects.png')
            window.properties_popover.popdown();pump();window.select('layers',0,seek=False);window.cancel_scrub()
            window.canvas.composited=False;window.playhead=2;window.show_inspector();window.open_properties('Animate')
            screenshot(window,options.output_dir/'layer-animations.png')
            window.open_properties('Sound');screenshot(window,options.output_dir/'layer-sounds.png')
            window.properties_popover.popdown();pump();window.select('captions',0,seek=False);window.cancel_scrub()
            window.layer_tools.animation_editor.text_effect.set_active_id('snake');window.open_properties('Animate')
            pump();adjustment=window.properties_scroll.get_vadjustment()
            adjustment.set_value(adjustment.get_upper()-adjustment.get_page_size())
            screenshot(window,options.output_dir/'caption-animations.png')
        finally:
            window.cleanup();window.destroy();pump(.05)


if __name__=='__main__':main()
