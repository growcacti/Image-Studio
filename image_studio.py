#!/usr/bin/env python3
"""Image Studio: unified editor, organizer and batch toolbox. Python 3.10+."""
import base64
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import ttk, filedialog as fd, messagebox as mb, simpledialog as sd, colorchooser
from datetime import datetime
try:
    from PIL import Image, ImageTk, ImageOps, ImageEnhance, ImageFilter, ImageDraw, ImageSequence, ImageGrab
except ImportError:
    raise SystemExit('Install Pillow first: python -m pip install Pillow')

EXTS = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.tif', '.tiff', '.webp', '.ico', '.avif'}
CONFIG = Path.home() / '.image_studio' / 'settings.json'

def load_image(path):
    with Image.open(path) as im:
        return ImageOps.exif_transpose(im).convert('RGBA')

def save_image(image, path, quality=92, overwrite=False):
    """Write atomically. New outputs use exclusive creation to prevent clobbering."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = Image.registered_extensions().get(path.suffix.lower())
    if not fmt:
        raise ValueError('Choose a supported image extension.')
    out = image
    if fmt in ('JPEG', 'BMP', 'PDF'):
        out = Image.new('RGB', image.size, 'white')
        rgba = image.convert('RGBA')
        out.paste(rgba, mask=rgba.getchannel('A'))
    handle, temp = tempfile.mkstemp(dir=path.parent, suffix=path.suffix)
    os.close(handle)
    try:
        out.save(temp, format=fmt, quality=quality)
        if overwrite:
            os.replace(temp, path)
        else:
            with path.open('xb') as dest, open(temp, 'rb') as src:
                shutil.copyfileobj(src, dest)
    finally:
        Path(temp).unlink(missing_ok=True)

def unique_path(path):
    path = Path(path)
    result, i = path, 1
    while result.exists():
        result = path.with_name(f'{path.stem}_{i}{path.suffix}')
        i += 1
    return result

def transform(im, width=0, height=0, fit=True, rotate=0, flip='None', gray=False):
    if width or height:
        if fit:
            if width and height:
                im = ImageOps.contain(im, (width, height), Image.Resampling.LANCZOS)
            else:
                ratio = width / im.width if width else height / im.height
                im = im.resize((max(1, round(im.width * ratio)), max(1, round(im.height * ratio))), Image.Resampling.LANCZOS)
        else:
            im = im.resize((width or im.width, height or im.height), Image.Resampling.LANCZOS)
    if rotate:
        im = im.rotate(rotate, expand=True)
    if flip == 'Horizontal':
        im = ImageOps.mirror(im)
    elif flip == 'Vertical':
        im = ImageOps.flip(im)
    if gray:
        alpha = im.getchannel('A')
        im = ImageOps.grayscale(im).convert('RGBA')
        im.putalpha(alpha)
    return im

def assemble(paths, mode, columns=4, cell=(128, 128)):
    images = [load_image(p) for p in paths]
    if not images:
        raise ValueError('Select images in Organizer first.')
    if mode == 'Horizontal':
        size = (sum(i.width for i in images), max(i.height for i in images))
    elif mode == 'Vertical':
        size = (max(i.width for i in images), sum(i.height for i in images))
    else:
        size = (columns * cell[0], math.ceil(len(images) / columns) * cell[1])
    if size[0] * size[1] > 80_000_000:
        raise ValueError('Output too large; use fewer images or smaller cells.')
    out = Image.new('RGBA', size)
    x = y = 0
    for n, im in enumerate(images):
        if mode == 'Grid':
            im = ImageOps.contain(im, cell, Image.Resampling.LANCZOS)
            x = (n % columns) * cell[0] + (cell[0] - im.width) // 2
            y = (n // columns) * cell[1] + (cell[1] - im.height) // 2
        out.alpha_composite(im, (x, y))
        if mode == 'Horizontal':
            x += im.width
        elif mode == 'Vertical':
            y += im.height
    return out

class Studio(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Image Studio — Editor & Organizer')
        self.geometry('1180x780'); self.minsize(900, 620)
        self.report_callback_exception = lambda cls, exc, tb: mb.showerror('Image Studio', str(exc), parent=self)
        self.events = queue.Queue(); self.cancel = threading.Event(); self.busy = False
        self.image = None; self.dirty = False; self.undo_stack = []; self.redo_stack = []
        self.records = []; self.generation = 0; self.anim_id = None
        try:
            self.settings = json.loads(CONFIG.read_text())
            if not isinstance(self.settings, dict): self.settings = {}
        except (OSError, ValueError): self.settings = {}
        self.settings.setdefault('bookmarks', []); self.settings.setdefault('history', [])
        style = ttk.Style(self); style.theme_use('clam')
        style.configure('TButton', padding=6)
        style.configure('TNotebook.Tab', padding=(18, 9))
        style.configure('Treeview', rowheight=28)
        style.configure('Accent.TButton', background='#b6dcef', foreground='#12394d')
        self.path = tk.StringVar(value=self.settings.get('folder', str(Path.home())))
        top = ttk.Frame(self, padding=8); top.pack(fill='x')
        ttk.Label(top, text='Shared folder').pack(side='left')
        self.pathbox = ttk.Combobox(top, textvariable=self.path, values=self.settings['history']); self.pathbox.pack(side='left', fill='x', expand=True, padx=8)
        self.pathbox.bind('<Return>', lambda e: self.scan())
        self.pathbox.bind('<<ComboboxSelected>>', lambda e: self.scan())
        for label, fn in [('Browse…', self.browse), ('Scan', self.scan), ('Bookmark', self.bookmark), ('Bookmarks…', self.bookmarks)]:
            ttk.Button(top, text=label, command=fn).pack(side='left', padx=2)
        self.nb = ttk.Notebook(self); self.nb.pack(fill='both', expand=True, padx=8)
        self.organizer = ttk.Frame(self.nb); self.editor = ttk.Frame(self.nb); self.batch = ttk.Frame(self.nb); self.tools = ttk.Frame(self.nb)
        for f, title in [(self.organizer,'Organizer'),(self.editor,'Editor'),(self.batch,'Batch Processing'),(self.tools,'Sprites • Tiles • Animation • Encoding')]: self.nb.add(f, text=title)
        self.build_organizer(); self.build_editor(); self.build_batch(); self.build_tools()
        self.thumbnail = ttk.Frame(self.nb); self.nb.insert(1, self.thumbnail, text='Clipboard Thumbnails')
        self.build_thumbnail()
        bottom = ttk.Frame(self, padding=8); bottom.pack(fill='x')
        self.status = tk.StringVar(value='Choose a folder and scan, or open an image in Editor.')
        ttk.Label(bottom, textvariable=self.status).pack(side='left', fill='x', expand=True)
        ttk.Button(bottom, text='Cancel job', command=self.cancel.set).pack(side='right')
        self.after(80, self.poll); self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Control-z>', lambda e: self.undo()); self.bind('<Control-y>', lambda e: self.undo(True))
        self.bind('<Control-s>', lambda e: self.thumb_save() if self.nb.select()==str(self.thumbnail) else self.save()); self.bind('<Control-o>', lambda e: self.open())

    def build_thumbnail(self):
        self.thumb_image = None
        self.thumb_dirty = False
        self.thumb_box = None
        self.thumb_folder = tk.StringVar(value=self.settings.get('thumbnail_folder', self.path.get()))
        self.thumb_name = tk.StringVar(value='thumbnail.png')
        self.thumb_width = tk.StringVar(value=self.settings.get('thumbnail_width', '0'))
        self.thumb_height = tk.StringVar(value=self.settings.get('thumbnail_height', '0'))
        ttk.Label(self.thumbnail, text='Screenshot → Paste image → Choose game/file (or paste its name) → Save PNG. Drag on the preview to crop.', padding=8).pack(anchor='w')
        self.buttons(self.thumbnail, [('Paste image', self.thumb_paste), ('Paste + Save', lambda:self.thumb_paste(True)),
            ('Choose game/file…', self.thumb_choose_file), ('Save PNG', self.thumb_save),
            ('Crop selection', self.thumb_crop), ('Open in Editor', self.thumb_to_editor)])
        form = ttk.Frame(self.thumbnail); form.pack(fill='x')
        self.field(form, 0, 'Destination folder', self.thumb_folder)
        self.field(form, 1, 'PNG filename', self.thumb_name)
        row = self.buttons(self.thumbnail, [('Browse destination…', lambda:self.choose_output(self.thumb_folder)),
            ('Use shared folder above', lambda:self.thumb_folder.set(self.path.get())),
            ('Paste filename', self.thumb_paste_name)])
        ttk.Label(row, text='Max width').pack(side='left', padx=(12,2))
        ttk.Entry(row, textvariable=self.thumb_width, width=6).pack(side='left')
        ttk.Label(row, text='Max height').pack(side='left', padx=(8,2))
        ttk.Entry(row, textvariable=self.thumb_height, width=6).pack(side='left')
        ttk.Label(row, text='0 = original; proportions kept').pack(side='left', padx=8)
        self.thumb_info = tk.StringVar(value='Click the canvas, then Ctrl+V to paste a screenshot. Ctrl+S saves PNG.')
        ttk.Label(self.thumbnail, textvariable=self.thumb_info, padding=6).pack(anchor='w')
        self.thumb_canvas = tk.Canvas(self.thumbnail, bg='#243440', highlightthickness=0, takefocus=True)
        self.thumb_canvas.pack(fill='both', expand=True, padx=6, pady=6)
        self.thumb_canvas.bind('<Control-v>', lambda e:(self.thumb_paste(), 'break')[1])
        self.thumb_canvas.bind('<Configure>', lambda e:self.thumb_render())
        self.thumb_canvas.bind('<Button-1>', self.thumb_start)
        self.thumb_canvas.bind('<B1-Motion>', self.thumb_drag)
        self.thumb_canvas.bind('<Button-3>', lambda e:self.thumb_paste())
        self.thumb_canvas.create_text(25,25,anchor='nw',fill='white',text='Paste image button, Ctrl+V here, or right-click to paste.')

    def thumb_resolve_folder(self, value):
        """Map an already mounted smb URI to its Linux GVfs filesystem path."""
        if value.lower().startswith('smb://'):
            from urllib.parse import urlsplit, unquote
            uri = urlsplit(value)
            parts = unquote(uri.path).strip('/').split('/')
            host, share = uri.hostname, parts[0]
            root = Path(os.environ.get('XDG_RUNTIME_DIR', '/run/user/' + str(os.getuid()))) / 'gvfs'
            for mount in root.glob('smb-share:*'):
                attrs = dict(item.split('=',1) for item in mount.name.split(':',1)[1].split(',') if '=' in item)
                if attrs.get('server','').casefold()==(host or '').casefold() and attrs.get('share','').casefold()==share.casefold():
                    return mount.joinpath(*parts[1:])
            raise ValueError('Open this Samba share in Thunar first, or choose its mounted folder with Browse destination. On Windows use a UNC path.')
        return Path(value).expanduser()

    def thumb_choose_file(self):
        try: folder = self.thumb_resolve_folder(self.thumb_folder.get())
        except Exception: folder = Path.home()
        path = fd.askopenfilename(title='Choose game or file to name its thumbnail', initialdir=str(folder), filetypes=[('All files', '*')])
        if path:
            self.thumb_folder.set(str(Path(path).parent))
            self.thumb_name.set(Path(path).stem + '.png')

    def thumb_paste_name(self):
        try: name = self.clipboard_get().strip().strip('"')
        except tk.TclError:
            mb.showinfo('Filename', 'Copy a filename as text first.'); return
        name = name.replace('\\', '/').rsplit('/', 1)[-1]
        if not name or '\n' in name or '\r' in name:
            mb.showerror('Filename', 'Copy one filename at a time.'); return
        self.thumb_name.set(Path(name).stem + '.png')

    def thumb_paste(self, save=False):
        if self.thumb_dirty and not mb.askyesno('Unsaved thumbnail', 'Replace the unsaved thumbnail with the clipboard image?'): return
        def read():
            try: result = ImageGrab.grabclipboard()
            except Exception as exc:
                raise ValueError('Could not read the image clipboard. On Linux install xclip (Xfce/X11) or wl-clipboard (Wayland), then copy a screenshot as an image. Details: ' + str(exc)) from exc
            if isinstance(result, list):
                for path in result:
                    try: return load_image(path)
                    except (OSError, ValueError): continue
            if not isinstance(result, Image.Image):
                raise ValueError('No image on the clipboard. Copy a screenshot as an image, then use Paste image. Filename text is pasted separately.')
            return result.convert('RGBA')
        def done(im):
            self.thumb_image = im; self.thumb_dirty = True; self.thumb_box = None
            self.thumb_render(); self.thumb_canvas.focus_set()
            self.status.set('Clipboard image ready. Choose a filename and Save PNG.')
            if save: self.thumb_save()
        self.job(read, done)

    def thumb_render(self):
        if self.thumb_image is None: return
        canvas = self.thumb_canvas
        w,h = max(1,canvas.winfo_width()-20), max(1,canvas.winfo_height()-20)
        self.thumb_scale = min(w/self.thumb_image.width, h/self.thumb_image.height, 1)
        im = self.thumb_image.copy(); im.thumbnail((w,h), Image.Resampling.LANCZOS)
        self.thumb_photo = ImageTk.PhotoImage(im)
        canvas.delete('all'); canvas.create_image(10,10,anchor='nw',image=self.thumb_photo)
        self.thumb_box = None; self.thumb_rect = None
        self.thumb_info.set(f'{self.thumb_image.width} × {self.thumb_image.height} pixels • Drag to select crop • Ctrl+S saves PNG')

    def thumb_point(self, e):
        return tuple(max(0,min(limit,round((value-10)/self.thumb_scale))) for value,limit in [(e.x,self.thumb_image.width),(e.y,self.thumb_image.height)])

    def thumb_start(self, e):
        self.thumb_canvas.focus_set()
        if self.thumb_image is None: return
        self.thumb_anchor = self.thumb_point(e); self.thumb_box = None
        if self.thumb_rect: self.thumb_canvas.delete(self.thumb_rect)
        self.thumb_rect = self.thumb_canvas.create_rectangle(e.x,e.y,e.x,e.y,outline='#ffc34d',width=2)

    def thumb_drag(self, e):
        if self.thumb_image is None or not self.thumb_rect: return
        x,y = self.thumb_point(e); a,b = self.thumb_anchor
        self.thumb_box = (min(a,x),min(b,y),max(a,x),max(b,y))
        self.thumb_canvas.coords(self.thumb_rect, *[10+v*self.thumb_scale for v in self.thumb_box])

    def thumb_crop(self):
        box = self.thumb_box
        if not box or box[0]>=box[2] or box[1]>=box[3]:
            mb.showinfo('Crop', 'Drag a rectangle on the preview first.'); return
        self.thumb_image = self.thumb_image.crop(box); self.thumb_dirty = True; self.thumb_render()

    def thumb_to_editor(self):
        if self.thumb_image is not None and self.discard(): self.set_image(self.thumb_image.copy())

    def thumb_save(self):
        if self.thumb_image is None:
            mb.showinfo('Thumbnail', 'Paste a screenshot first.'); return
        if self.busy:
            mb.showinfo('Working', 'Wait for the current job to finish.'); return
        try:
            if not self.thumb_folder.get().strip(): raise ValueError('Choose a destination folder.')
            folder = self.thumb_resolve_folder(self.thumb_folder.get().strip())
            if not folder.is_dir(): raise ValueError('Destination unavailable. Mount/open the Samba share or choose an existing folder.')
            name = self.thumb_name.get().strip()
            if not name or name in ('.','..') or re.search(r'[\\/:*?"<>|\x00-\x1f]', name):
                raise ValueError('Enter a filename without path separators or special path characters.')
            if not name.lower().endswith('.png'): name += '.png'
            width,height = int(self.thumb_width.get()), int(self.thumb_height.get())
            if not 0<=width<=20000 or not 0<=height<=20000: raise ValueError('Dimensions must be from 0 to 20000.')
            im = transform(self.thumb_image.copy(), width, height)
            target = folder / name; exists = target.exists()
            if exists and not mb.askyesno('Overwrite thumbnail', f'Replace {target}?'): return
        except Exception as exc:
            mb.showerror('Save thumbnail', str(exc)); return
        def done(result):
            self.thumb_dirty = False; self.thumb_name.set(name)
            self.settings.update(thumbnail_folder=self.thumb_folder.get(), thumbnail_width=str(width), thumbnail_height=str(height))
            self.persist(); self.status.set(f'Saved PNG: {target} • {im.width} × {im.height}')
            self.thumb_info.set(f'Saved {name} • Ready for the next screenshot')
        self.job(lambda:save_image(im,target,overwrite=exists), done)

    def buttons(self, parent, items):
        row = ttk.Frame(parent, padding=5); row.pack(fill='x')
        for name, fn in items: ttk.Button(row, text=name, command=fn).pack(side='left', padx=2)
        return row

    def job(self, fn, done):
        if self.busy:
            mb.showinfo('Working', 'Wait for the current job or cancel it.'); return
        self.busy = True; self.cancel.clear(); self.status.set('Working…')
        def worker():
            try: self.events.put(('done', done, fn()))
            except Exception as e: self.events.put(('error', str(e)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        for _ in range(60):
            try: event = self.events.get_nowait()
            except queue.Empty: break
            if event[0] == 'done':
                self.busy = False; event[1](event[2])
            elif event[0] == 'error':
                self.busy = False; self.status.set('Job failed.'); mb.showerror('Error', event[1])
            elif event[0] == 'status': self.status.set(event[1])
        self.after(80, self.poll)

    def persist(self):
        try:
            CONFIG.parent.mkdir(parents=True, exist_ok=True)
            temp = CONFIG.with_suffix('.tmp'); temp.write_text(json.dumps(self.settings, indent=2)); temp.replace(CONFIG)
        except OSError as e: self.status.set(f'Could not save settings: {e}')

    def browse(self):
        p = fd.askdirectory(initialdir=self.path.get() if Path(self.path.get()).is_dir() else None)
        if p: self.path.set(p); self.scan()

    def bookmark(self):
        p = self.path.get()
        if p not in self.settings['bookmarks']: self.settings['bookmarks'].append(p)
        self.persist(); self.status.set('Bookmark saved; available across all tabs.')

    def bookmarks(self):
        win = tk.Toplevel(self); win.title('Shared bookmarks')
        box = tk.Listbox(win, width=90); box.pack(fill='both', expand=True)
        for p in self.settings['bookmarks']: box.insert('end', p)
        def use():
            if box.curselection(): self.path.set(box.get(box.curselection()[0])); win.destroy(); self.scan()
        def remove():
            if box.curselection():
                self.settings['bookmarks'].remove(box.get(box.curselection()[0])); box.delete(box.curselection()[0]); self.persist()
        self.buttons(win, [('Use folder', use), ('Remove bookmark', remove)])

    def build_organizer(self):
        row = self.buttons(self.organizer, [('Select all', lambda: self.tree.selection_set(self.tree.get_children())),('Clear selection', lambda: self.tree.selection_remove(self.tree.selection())),('Edit selected', self.edit_selected),('Copy…', lambda: self.transfer(False)),('Move…', lambda: self.transfer(True)),('Rename…', self.rename),('Find duplicates', self.duplicates),('Export CSV…', self.export_csv)])
        self.recursive = tk.BooleanVar(value=False); ttk.Checkbutton(row, text='Recursive', variable=self.recursive).pack(side='left')
        row = ttk.Frame(self.organizer, padding=5); row.pack(fill='x')
        ttk.Label(row, text='Filter filename').pack(side='left'); self.filter = tk.StringVar(); ttk.Entry(row, textvariable=self.filter).pack(side='left', fill='x', expand=True)
        ttk.Button(row, text='Apply', command=self.populate).pack(side='left')
        pane = ttk.Panedwindow(self.organizer, orient='horizontal'); pane.pack(fill='both', expand=True)
        left = ttk.Frame(pane); right = ttk.Frame(pane); pane.add(left, weight=3); pane.add(right, weight=2)
        cols = ('name','size','dimensions','modified','folder')
        self.tree = ttk.Treeview(left, columns=cols, show='headings', selectmode='extended')
        for c in cols:
            self.tree.heading(c, text=c.title(), command=lambda c=c: self.sort(c)); self.tree.column(c, width=150 if c in ('name','folder') else 100)
        ys = ttk.Scrollbar(left, orient='vertical', command=self.tree.yview); xs = ttk.Scrollbar(left, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.tree.grid(row=0,column=0,sticky='nsew'); ys.grid(row=0,column=1,sticky='ns'); xs.grid(row=1,column=0,sticky='ew'); left.rowconfigure(0,weight=1); left.columnconfigure(0,weight=1)
        self.preview = tk.Label(right, bg='#243440', fg='white', text='Select an image for preview'); self.preview.pack(fill='both', expand=True)
        self.info = tk.StringVar(); ttk.Label(right, textvariable=self.info, wraplength=380, padding=8).pack(fill='x')
        self.tree.bind('<<TreeviewSelect>>', self.preview_selected); self.tree.bind('<Double-1>', lambda e: self.edit_selected())

    def scan(self):
        folder = Path(self.path.get()).expanduser()
        if not folder.is_dir(): mb.showerror('Folder', 'Folder unavailable. Use a local, mounted network, or Windows UNC path.'); return
        self.path.set(str(folder)); recursive = self.recursive.get(); self.generation += 1
        def work():
            rows = []; errors = []
            def walk_error(e): errors.append(str(e))
            for root, dirs, files in os.walk(folder, onerror=walk_error, followlinks=False):
                for name in files:
                    if self.cancel.is_set(): return rows, errors
                    p = Path(root) / name
                    if p.suffix.lower() not in EXTS: continue
                    try:
                        stat = p.stat()
                        with Image.open(p) as im: dims = im.size; frames = getattr(im,'n_frames',1)
                        rows.append({'path':str(p),'name':name,'size':stat.st_size,'dimensions':dims,'modified':stat.st_mtime,'folder':root,'frames':frames})
                    except Exception as e: errors.append(f'{p}: {e}')
                if not recursive: break
            return rows, errors
        def done(result):
            self.records, errors = result; self.populate()
            history = self.settings['history']; history = [str(folder)] + [p for p in history if p != str(folder)]
            self.settings.update(folder=str(folder), history=history[:30]); self.pathbox['values'] = history[:30]; self.persist()
            self.status.set(f'{len(self.records)} images • {len(errors)} unreadable files/folders')
            if errors: mb.showwarning('Scan details', '\n'.join(errors[:12]))
        self.job(work, done)

    def populate(self):
        self.tree.delete(*self.tree.get_children()); needle = self.filter.get().casefold()
        for n, r in enumerate(self.records):
            if needle in r['name'].casefold(): self.tree.insert('', 'end', iid=str(n), values=(r['name'],r['size'],f"{r['dimensions'][0]} × {r['dimensions'][1]}",datetime.fromtimestamp(r['modified']).strftime('%Y-%m-%d %H:%M'),r['folder']))

    def sort(self, col):
        reverse = getattr(self,'sort_state',None) == (col,False); self.sort_state=(col,reverse)
        self.records.sort(key=lambda r: r[col].casefold() if isinstance(r[col],str) else r[col], reverse=reverse); self.populate()

    def selected(self): return [self.records[int(i)]['path'] for i in self.tree.selection()]

    def preview_selected(self, event=None):
        paths = self.selected()
        if not paths: return
        try:
            im = load_image(paths[0]); im.thumbnail((430,450)); photo = ImageTk.PhotoImage(im); self.preview.configure(image=photo,text=''); self.preview.image=photo
            r = self.records[int(self.tree.selection()[0])]; self.info.set(f"{r['path']}\n{r['dimensions']} • {r['size']:,} bytes • {r['frames']} frame(s)\n{len(paths)} selected")
        except Exception as e: self.info.set(str(e))

    def edit_selected(self):
        if self.selected(): self.open(self.selected()[0])

    def transfer(self, move):
        paths = self.selected()
        if not paths: mb.showinfo('Selection','Select images first.'); return
        dest = fd.askdirectory(title='Destination folder')
        if not dest: return
        if move and not mb.askyesno('Move images',f'Move {len(paths)} files to {dest}?'): return
        def work():
            errors=[]; count=0
            for p in paths:
                if self.cancel.is_set(): break
                try:
                    target=unique_path(Path(dest)/Path(p).name)
                    if Path(p).parent.resolve()==Path(dest).resolve(): continue
                    with target.open('xb') as out, open(p,'rb') as src: shutil.copyfileobj(src,out)
                    shutil.copystat(p,target)
                    if move: Path(p).unlink()
                    count+=1
                except Exception as e: errors.append(f'{p}: {e}')
            return count,errors
        self.job(work, lambda r: self.finished(r,'Transferred'))

    def finished(self, result, verb):
        count, errors=result; self.status.set(f'{verb}: {count} • Errors: {len(errors)}. Scan to refresh.')
        mb.showinfo(verb, f'{count} completed.\n'+ ('\n'.join(errors[:15]) if errors else 'No errors.'))

    def rename(self):
        paths=self.selected()
        if not paths: return
        prefix=sd.askstring('Rename','Prefix for numbered names (example: Holiday):')
        if not prefix: return
        if re.search(r'[\\/:*?"<>|]',prefix) or prefix in ('.','..'): raise ValueError('Use a plain filename prefix.')
        targets=[Path(p).with_name(f'{prefix}_{n:04d}{Path(p).suffix}') for n,p in enumerate(paths,1)]
        if any(p.exists() for p in targets): raise ValueError('A proposed name already exists. Choose another prefix.')
        if not mb.askyesno('Rename preview','\n'.join(f'{Path(p).name} → {t.name}' for p,t in list(zip(paths,targets))[:12])+f'\nRename {len(paths)} files?'): return
        errors=[]; count=0
        for p,t in zip(paths,targets):
            try:
                with t.open('xb') as out, open(p,'rb') as src: shutil.copyfileobj(src,out)
                shutil.copystat(p,t); Path(p).unlink(); count+=1
            except Exception as e: errors.append(str(e))
        self.finished((count,errors),'Renamed'); self.scan()

    def duplicates(self):
        paths=[r['path'] for r in self.records]
        def work():
            hashes={}; duplicates=[]; errors=[]
            for n,p in enumerate(paths):
                if self.cancel.is_set(): break
                try:
                    h=hashlib.sha256()
                    with open(p,'rb') as f:
                        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
                    key=h.hexdigest()
                    if key in hashes: duplicates.append((hashes[key],p))
                    else: hashes[key]=p
                except OSError as e: errors.append(str(e))
                self.events.put(('status',f'Checking duplicates {n+1}/{len(paths)}'))
            return duplicates,errors
        def done(result):
            dup,errors=result; win=tk.Toplevel(self); win.title(f'{len(dup)} exact duplicates')
            text=tk.Text(win,width=110,height=25); text.pack(fill='both',expand=True)
            text.insert('end','Exact file-byte matches; nothing is deleted.\n\n'+'\n\n'.join(f'Original: {a}\nDuplicate: {b}' for a,b in dup)+'\n\n'+'\n'.join(errors)); text.configure(state='disabled')
            self.status.set(f'{len(dup)} exact duplicate files found.')
        self.job(work,done)

    def export_csv(self):
        p=fd.asksaveasfilename(defaultextension='.csv')
        if p:
            with open(p,'w',newline='',encoding='utf-8') as f:
                writer=csv.DictWriter(f,fieldnames=['path','name','size','dimensions','modified','folder','frames']); writer.writeheader(); writer.writerows(self.records)
            self.status.set('Inventory exported.')

    def build_editor(self):
        self.buttons(self.editor,[('Open…',self.open),('Save As…',self.save),('Undo',self.undo),('Redo',lambda:self.undo(True)),('Reset',self.reset),('Resize…',self.resize),('Crop drag selection',self.crop),('Text…',self.text)])
        self.buttons(self.editor,[('Rotate 90°',lambda:self.apply(lambda im:im.rotate(90,expand=True))),('Rotate…',self.rotate),('Flip H',lambda:self.apply(ImageOps.mirror)),('Flip V',lambda:self.apply(ImageOps.flip)),('Grayscale',lambda:self.apply(lambda im:transform(im,gray=True))),('Invert',lambda:self.apply(self.invert)),('Blur',lambda:self.apply(lambda im:im.filter(ImageFilter.GaussianBlur(2)))),('Sharpen',lambda:self.apply(lambda im:im.filter(ImageFilter.UnsharpMask())))])
        row=self.buttons(self.editor,[('Brightness…',lambda:self.enhance('Brightness')),('Contrast…',lambda:self.enhance('Contrast')),('Color…',lambda:self.enhance('Color'))])
        self.zoom=tk.StringVar(value='Fit'); ttk.Combobox(row,textvariable=self.zoom,values=['Fit','25%','50%','100%','200%'],width=8,state='readonly').pack(side='left'); self.zoom.trace_add('write',lambda *a:self.render())
        ttk.Label(row,text='Drag on image to select crop or text position.').pack(side='left',padx=10)
        area=ttk.Frame(self.editor); area.pack(fill='both',expand=True); area.rowconfigure(0,weight=1); area.columnconfigure(0,weight=1)
        self.canvas=tk.Canvas(area,bg='#243440',highlightthickness=0)
        self.canvas.grid(row=0,column=0,sticky='nsew')
        y=ttk.Scrollbar(area,command=self.canvas.yview); y.grid(row=0,column=1,sticky='ns'); x=ttk.Scrollbar(area,orient='horizontal',command=self.canvas.xview); x.grid(row=1,column=0,sticky='ew'); self.canvas.configure(xscrollcommand=x.set,yscrollcommand=y.set)
        self.canvas.bind('<Configure>',lambda e:self.render()); self.canvas.bind('<Button-1>',self.drag_start); self.canvas.bind('<B1-Motion>',self.drag_move)
        self.crop_box=None; self.rect_id=None

    def discard(self): return not self.dirty or mb.askyesno('Unsaved changes','Discard unsaved editor changes?')

    def open(self, path=None):
        if not self.discard(): return
        path=path or fd.askopenfilename(filetypes=[('Images','*.png *.jpg *.jpeg *.gif *.bmp *.tif *.tiff *.webp *.ico *.avif'),('All files','*')])
        if not path: return
        im=load_image(path); self.source=path; self.original=im.copy(); self.image=im; self.undo_stack=[]; self.redo_stack=[]; self.dirty=False; self.crop_box=None
        self.nb.select(self.editor); self.render(); self.status.set(f'Opened {Path(path).name} • {im.width} × {im.height}. Editor loads the first frame.')

    def set_image(self,im):
        self.image=im; self.original=im.copy(); self.undo_stack=[]; self.redo_stack=[]; self.dirty=True; self.crop_box=None; self.nb.select(self.editor); self.render()

    def apply(self,fn):
        if self.image is None: mb.showinfo('Editor','Open an image first.'); return
        result=fn(self.image.copy())
        self.undo_stack.append(self.image); self.undo_stack=self.undo_stack[-8:]; self.redo_stack=[]; self.image=result; self.dirty=True; self.crop_box=None; self.render()

    def undo(self,redo=False):
        a,b=(self.redo_stack,self.undo_stack) if redo else (self.undo_stack,self.redo_stack)
        if a: b.append(self.image); self.image=a.pop(); self.dirty=True; self.crop_box=None; self.render()

    def reset(self):
        if self.image is not None: self.apply(lambda im:self.original.copy())

    def render(self):
        if self.image is None: return
        w=max(1,self.canvas.winfo_width()-20); h=max(1,self.canvas.winfo_height()-20)
        self.scale=min(w/self.image.width,h/self.image.height,1) if self.zoom.get()=='Fit' else float(self.zoom.get().rstrip('%'))/100
        size=(max(1,round(self.image.width*self.scale)),max(1,round(self.image.height*self.scale)))
        if size[0]*size[1]>40_000_000: self.zoom.set('Fit'); return
        im=self.image.resize(size,Image.Resampling.LANCZOS); self.photo=ImageTk.PhotoImage(im)
        self.canvas.delete('all'); self.canvas.create_image(10,10,anchor='nw',image=self.photo); self.canvas.configure(scrollregion=(0,0,size[0]+20,size[1]+20)); self.rect_id=None; self.crop_box=None

    def point(self,e): return (max(0,min(self.image.width,int((self.canvas.canvasx(e.x)-10)/self.scale))),max(0,min(self.image.height,int((self.canvas.canvasy(e.y)-10)/self.scale))))
    def drag_start(self,e):
        if self.image is None: return
        self.start=self.point(e); self.crop_box=None
        if self.rect_id: self.canvas.delete(self.rect_id)
        x,y=self.start; self.rect_id=self.canvas.create_rectangle(x*self.scale+10,y*self.scale+10,x*self.scale+10,y*self.scale+10,outline='#ffc34d',width=2)
    def drag_move(self,e):
        if self.image is None or self.rect_id is None: return
        x,y=self.point(e); a,b=self.start; self.crop_box=(min(a,x),min(b,y),max(a,x),max(b,y)); self.canvas.coords(self.rect_id,*[v*self.scale+10 for v in self.crop_box])
    def crop(self):
        box=self.crop_box
        if not box or box[2]<=box[0] or box[3]<=box[1]: mb.showinfo('Crop','Drag a rectangle on the image first.'); return
        self.apply(lambda im:im.crop(box))
    def resize(self):
        if self.image is None: return
        w=sd.askinteger('Resize','Width in pixels:',initialvalue=self.image.width,minvalue=1,maxvalue=20000)
        if w is None: return
        if mb.askyesno('Aspect ratio','Keep aspect ratio?'): self.apply(lambda im:transform(im,width=w))
        else:
            h=sd.askinteger('Resize','Height in pixels:',initialvalue=self.image.height,minvalue=1,maxvalue=20000)
            if h: self.apply(lambda im:transform(im,width=w,height=h,fit=False))
    def rotate(self):
        n=sd.askfloat('Rotate','Degrees counterclockwise:',initialvalue=90)
        if n is not None: self.apply(lambda im:im.rotate(n,expand=True))
    def enhance(self,name):
        n=sd.askfloat(name,'Factor (1 = unchanged):',initialvalue=1.2,minvalue=0,maxvalue=10)
        if n is not None: self.apply(lambda im:getattr(ImageEnhance,name)(im).enhance(n))
    @staticmethod
    def invert(im):
        alpha=im.getchannel('A'); out=ImageOps.invert(im.convert('RGB')).convert('RGBA'); out.putalpha(alpha); return out
    def text(self):
        if self.image is None: return
        text=sd.askstring('Text','Text to draw (click image first for position):')
        if not text: return
        color=colorchooser.askcolor(parent=self)[1]
        if not color: return
        size=sd.askinteger('Text','Text size in pixels:',initialvalue=24,minvalue=6,maxvalue=500)
        if not size: return
        pos=getattr(self,'start',(10,10))
        def draw(im): ImageDraw.Draw(im).text(pos,text,fill=color,font_size=size); return im
        self.apply(draw)
    def save(self):
        if self.image is None: return
        p=fd.asksaveasfilename(defaultextension='.png',filetypes=[('PNG','*.png'),('JPEG','*.jpg'),('WebP','*.webp'),('TIFF','*.tif'),('BMP','*.bmp'),('GIF (single frame)','*.gif')])
        if p:
            exists=Path(p).exists()
            if exists and not mb.askyesno('Overwrite',f'Replace {p}?'): return
            save_image(self.image,p,overwrite=exists); self.dirty=False; self.status.set(f'Saved {p}')

    def field(self,parent,row,label,var,values=None):
        ttk.Label(parent,text=label).grid(row=row,column=0,sticky='w',padx=10,pady=6)
        widget=ttk.Combobox(parent,textvariable=var,values=values,state='readonly') if values else ttk.Entry(parent,textvariable=var)
        widget.grid(row=row,column=1,sticky='ew',padx=10,pady=6); parent.columnconfigure(1,weight=1)

    def build_batch(self):
        ttk.Label(self.batch,text='Uses the selected Organizer images. Exports new files; original images are preserved.',padding=12).pack(anchor='w')
        form=ttk.Frame(self.batch); form.pack(fill='x',padx=20)
        self.bw=tk.StringVar(value='0'); self.bh=tk.StringVar(value='0'); self.bfmt=tk.StringVar(value='PNG'); self.brot=tk.StringVar(value='0'); self.bflip=tk.StringVar(value='None'); self.bquality=tk.StringVar(value='92'); self.bout=tk.StringVar(); self.bprefix=tk.StringVar(value='edited_')
        for r,(label,var,values) in enumerate([('Width (0 = automatic)',self.bw,None),('Height (0 = automatic)',self.bh,None),('Format',self.bfmt,['PNG','JPEG','WEBP','BMP','TIFF']),('Rotation degrees',self.brot,None),('Flip',self.bflip,['None','Horizontal','Vertical']),('JPEG/WebP quality (1–100)',self.bquality,None),('Output prefix',self.bprefix,None),('Output folder',self.bout,None)]): self.field(form,r,label,var,values)
        self.bfit=tk.BooleanVar(value=True); self.bgray=tk.BooleanVar()
        ttk.Checkbutton(form,text='Preserve aspect ratio / fit inside bounds',variable=self.bfit).grid(row=8,column=1,sticky='w'); ttk.Checkbutton(form,text='Grayscale',variable=self.bgray).grid(row=9,column=1,sticky='w')
        self.buttons(self.batch,[('Choose output folder…',lambda:self.choose_output(self.bout)),('Run batch',self.run_batch)])

    def choose_output(self,var):
        p=fd.askdirectory()
        if p: var.set(p)

    def run_batch(self):
        paths=self.selected()
        if not paths: mb.showinfo('Batch','Select images in Organizer first (Select all is available).'); return
        width,height=int(self.bw.get()),int(self.bh.get()); quality=int(self.bquality.get()); rotate=float(self.brot.get()); fit=self.bfit.get(); gray=self.bgray.get(); flip=self.bflip.get(); fmt=self.bfmt.get(); prefix=self.bprefix.get(); out=Path(self.bout.get())
        if not self.bout.get() or not out.is_dir(): raise ValueError('Choose an existing output folder.')
        if not 0<=width<=20000 or not 0<=height<=20000 or not 1<=quality<=100: raise ValueError('Invalid dimensions or quality.')
        if re.search(r'[\\/:*?"<>|]',prefix): raise ValueError('Prefix must not contain path characters.')
        def work():
            count=0; errors=[]; suffix={'JPEG':'.jpg','TIFF':'.tif'}.get(fmt,'.'+fmt.lower())
            for p in paths:
                if self.cancel.is_set(): break
                try:
                    im=transform(load_image(p),width,height,fit,rotate,flip,gray)
                    save_image(im,unique_path(out/(prefix+Path(p).stem+suffix)),quality); count+=1
                except Exception as e: errors.append(f'{p}: {e}')
                self.events.put(('status',f'Processed {count}/{len(paths)}'))
            return count,errors
        self.job(work,lambda r:self.finished(r,'Batch export'))

    def build_tools(self):
        ttk.Label(self.tools,text='Merge, sprites and animation use selected Organizer files in displayed order.',padding=12).pack(anchor='w')
        form=ttk.Frame(self.tools); form.pack(fill='x',padx=20)
        self.columns=tk.StringVar(value='4'); self.cellw=tk.StringVar(value='128'); self.cellh=tk.StringVar(value='128'); self.delay=tk.StringVar(value='100')
        for r,(label,var) in enumerate([('Grid columns',self.columns),('Cell / tile width',self.cellw),('Cell / tile height',self.cellh),('Animation delay (milliseconds)',self.delay)]): self.field(form,r,label,var)
        self.buttons(self.tools,[('Merge horizontally',lambda:self.merge('Horizontal')),('Merge vertically',lambda:self.merge('Vertical')),('Build sprite sheet',lambda:self.merge('Grid')),('Split editor image into tiles…',self.tiles)])
        self.buttons(self.tools,[('Preview selected animation',self.animation),('Preview GIF…',lambda:self.animation(True)),('Export selected as GIF…',self.export_gif),('Encode image as JSON…',self.encode),('Decode JSON…',self.decode)])
        ttk.Label(self.tools,text='Sprite sheets use transparent cells and preserve aspect ratio. Tiles include partial edge tiles.\nAnimation accepts an image sequence or an existing GIF.\nJSON encoding stores the original image bytes for lossless recovery.',padding=15).pack(anchor='w')

    def dimensions(self):
        c,w,h=int(self.columns.get()),int(self.cellw.get()),int(self.cellh.get())
        if not 1<=c<=100 or not 1<=w<=10000 or not 1<=h<=10000: raise ValueError('Invalid columns/cell dimensions.')
        return c,(w,h)
    def merge(self,mode):
        paths=self.selected(); c,cell=self.dimensions()
        if not paths: mb.showinfo('Selection','Select images in Organizer.'); return
        if not self.discard(): return
        self.job(lambda:assemble(paths,mode,c,cell),self.set_image)
    def tiles(self):
        if self.image is None: mb.showinfo('Tiles','Open an image in Editor first.'); return
        _,(w,h)=self.dimensions(); folder=fd.askdirectory(title='Tile output folder')
        if not folder: return
        im=self.image.copy()
        def work():
            count=0; errors=[]
            for y in range(0,im.height,h):
                for x in range(0,im.width,w):
                    if self.cancel.is_set(): return count,errors
                    try: save_image(im.crop((x,y,min(x+w,im.width),min(y+h,im.height))),unique_path(Path(folder)/f'tile_{y//h:03d}_{x//w:03d}.png')); count+=1
                    except Exception as e: errors.append(str(e))
            return count,errors
        self.job(work,lambda r:self.finished(r,'Tile export'))
    def animation(self,gif=False):
        delay=int(self.delay.get())
        if not 20<=delay<=10000: raise ValueError('Use animation delay 20–10000 ms.')
        paths=self.selected()
        if gif:
            p=fd.askopenfilename(filetypes=[('GIF','*.gif')])
            if not p: return
            frames=[]; durations=[]
            with Image.open(p) as im:
                for frame in ImageSequence.Iterator(im):
                    copy=frame.convert('RGBA'); copy.thumbnail((700,500)); frames.append(copy); durations.append(max(20,frame.info.get('duration',delay)))
        else:
            if not paths: mb.showinfo('Animation','Select an ordered image sequence.'); return
            frames=[load_image(p) for p in paths]; durations=[delay]*len(frames)
            for im in frames: im.thumbnail((700,500))
        if not frames: return
        win=tk.Toplevel(self); win.title('Animation preview'); label=tk.Label(win,bg='#243440'); label.pack(fill='both',expand=True)
        photos=[ImageTk.PhotoImage(im) for im in frames]; state={'n':0,'id':None,'playing':True}
        def tick():
            label.configure(image=photos[state['n']]); n=state['n']; state['n']=(n+1)%len(photos)
            if state['playing']: state['id']=win.after(durations[n],tick)
        def toggle():
            state['playing']=not state['playing']
            if state['playing']: tick()
            elif state['id']: win.after_cancel(state['id'])
        def close():
            if state['id']: win.after_cancel(state['id'])
            win.destroy()
        self.buttons(win,[('Pause / Play',toggle)]); win.protocol('WM_DELETE_WINDOW',close); tick()
    def export_gif(self):
        paths=self.selected(); delay=int(self.delay.get())
        if not paths: return
        if not 20<=delay<=10000: raise ValueError('Invalid animation delay.')
        p=fd.asksaveasfilename(defaultextension='.gif')
        if not p: return
        if Path(p).exists() and not mb.askyesno('Overwrite',f'Replace {p}?'): return
        def work():
            frames=[load_image(a) for a in paths]; size=(max(i.width for i in frames),max(i.height for i in frames)); aligned=[]
            for im in frames:
                canvas=Image.new('RGB',size,'white'); canvas.paste(im,((size[0]-im.width)//2,(size[1]-im.height)//2),im); aligned.append(canvas)
            handle,temp=tempfile.mkstemp(dir=Path(p).parent,suffix='.gif'); os.close(handle)
            try: aligned[0].save(temp,save_all=True,append_images=aligned[1:],duration=delay,loop=0,disposal=2); os.replace(temp,p)
            finally: Path(temp).unlink(missing_ok=True)
            return (1,[])
        self.job(work,lambda r:self.finished(r,'GIF export'))
    def encode(self):
        p=fd.askopenfilename(title='Image to encode')
        if not p: return
        out=fd.asksaveasfilename(defaultextension='.json')
        if out:
            data=Path(p).read_bytes()
            Path(out).write_text(json.dumps({'version':1,'filename':Path(p).name,'sha256':hashlib.sha256(data).hexdigest(),'data':base64.b64encode(data).decode('ascii')},indent=2)); self.status.set('Encoded original image bytes.')
    def decode(self):
        p=fd.askopenfilename(filetypes=[('Encoded JSON','*.json')])
        if not p: return
        obj=json.loads(Path(p).read_text()); data=base64.b64decode(obj['data'],validate=True)
        if obj.get('sha256') and hashlib.sha256(data).hexdigest()!=obj['sha256']: raise ValueError('Checksum mismatch; JSON may be damaged.')
        out=fd.asksaveasfilename(initialfile=Path(obj.get('filename','decoded.png')).name)
        if out:
            if Path(out).exists() and not mb.askyesno('Overwrite',f'Replace {out}?'): return
            Path(out).write_bytes(data); self.status.set('Decoded original image bytes.')
    def close(self):
        if not self.discard(): return
        if self.thumb_dirty and not mb.askyesno('Unsaved thumbnail', 'Close without saving the clipboard thumbnail?'): return
        self.cancel.set(); self.persist(); self.destroy()

if __name__=='__main__': Studio().mainloop()
