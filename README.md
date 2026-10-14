# Image Studio

One cross-platform Tkinter program combining the useful image workflows in your
Image_programs.zip. The interface is rebuilt around shared state rather than
launching the old scripts separately.

## Install and start

Requires Python 3.10 or newer, Tkinter, and Pillow 10.1 or newer.

Windows:

```
py -m pip install -r requirements.txt
py image_studio.py
```

MX Linux / Debian (system packages):

```
sudo apt install python3-tk python3-pil python3-pil.imagetk
python3 image_studio.py
```

If your distribution's Pillow is older than 10.1, use a virtual environment:

```
sudo apt install python3-tk python3-venv
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python image_studio.py
```

For a separately installed Python 3.13/3.14, install Pillow with that interpreter
and make sure that interpreter has working Tkinter support. The system apt
packages normally belong to the distribution's Python, not a custom Python.

## Organizer

1. Browse to a folder or enter a folder path and press Enter.
2. Enable Recursive before scanning to include subfolders.
3. Click column headings to sort by name, byte size, dimensions, date or folder.
4. Enter part of a filename and click Apply to filter.
5. Click an image for a preview. Ctrl-click / Shift-click to select multiple files.
6. Double-click an image or choose Edit selected to send it to Editor.

Copy and Move ask for a destination and add numeric suffixes when names collide.
Move asks for confirmation. Rename previews numbered names before confirmation
and refuses existing target names. Scan again after file operations to refresh.
Find duplicates compares SHA-256 hashes of complete files; it reports exact byte
matches and does not delete anything. Export CSV saves the full scanned inventory;
modified times in the CSV are Unix timestamps. The preview pane shows one image,
not a thumbnail grid.

The shared folder bar, bookmarks and history are available while using every tab.
Settings are saved under ~/.image_studio/settings.json (your user home on Windows
also). Bookmarks can be removed through Bookmarks… .

## Network folders

Windows: enter a UNC path such as \\server\photos.
Linux: use an already mounted SMB share, such as /mnt/photos, or a filesystem
path exposed by your desktop's network mounting service. Raw smb:// URLs are
not filesystem paths and are not mounted by this app. Mount/connect to your
share first; normal operating-system permissions still apply.

## Editor

Open an image, then rotate, flip, crop, resize, adjust brightness/contrast/color,
invert, grayscale, blur, sharpen or add text. Drag a rectangle on the image and
click Crop drag selection. For text, click the desired position first.

Edits use full-resolution pixels. Fit/25%/50%/100%/200% changes the view only;
scrollbars allow panning. Undo retains eight previous images. Redo and Reset
are available. Large images and undo histories can consume substantial memory.
Use Save As to export. Existing files require overwrite confirmation. JPEG/BMP
flatten transparency onto white. PNG preserves transparency.

Keyboard shortcuts: Ctrl+O open, Ctrl+S save, Ctrl+Z undo, Ctrl+Y redo.
Opening another image or closing asks before discarding unsaved edits.

## Batch Processing

Select files in Organizer, then choose dimensions, format, rotation, flip,
grayscale, quality, prefix and output folder. Width/height zero means automatic;
with aspect ratio enabled, two dimensions act as maximum bounding dimensions.
With it disabled, specified dimensions stretch the image.

Run batch writes new files and keeps originals. Name collisions receive numeric
suffixes. Cancellation stops between files; files already exported remain.
Failures are reported per file. The editor's current edits are not included in
batch processing: batch loads the selected source files.

## Sprites, tiles, animations and encoding

- Horizontal/vertical merges use original image sizes.
- Sprite sheets fit each source into a transparent cell and use the selected
  files in their displayed Organizer order. Sort names before selecting to set
  sequence order. Number frame names with zeros (frame_001, frame_002, etc.).
- Merged output opens in Editor so it can be saved or further edited.
- Tiles split the current editor image using cell width/height. Partial edge
  tiles are included; existing files are preserved through suffixes.
- Preview selected animation plays selected files as frames. Preview GIF opens
  an existing animated GIF and uses its frame durations. Pause/Play is available.
- GIF export aligns selected images on white backgrounds at the chosen delay.
  GIF is palette-limited; PNG sheets are preferable for transparent game assets.
- Encode image creates JSON containing the original bytes in Base64 plus a
  SHA-256 checksum. Decode checks the checksum and restores the original bytes.
  This is the new JSON format, not the old script's timestamp-keyed database.

## Scope and troubleshooting

Common Pillow formats are supported; AVIF availability depends on the installed
Pillow build. RAW camera formats, vector SVG editing, layers, drawing brushes,
automatic network mounting and image inpainting are not included.
Animated inputs load their first frame in Editor and batch; use the animation
preview to see all GIF frames. Converted files may not preserve original EXIF,
ICC profiles or other metadata. EXIF orientation is applied on image loading.
The merged/animation tools load their selected images into memory. Start with
small selections for large photographs. Folder scans and batch processing run
in a worker; Tk updates happen on the main thread. Previewing or editing a very
large image can still briefly block the interface.

Unreadable files are reported without stopping the entire scan. Permission errors
on network shares should be fixed on the share/mount, not by running as root.
If Tkinter reports 'no display name', run from your normal graphical desktop.

## Relationship to your original files

The browser/manager programs supplied browsing, preview and editing workflows.
Image_Pro_V8 and Thumbnail Image_ToolV7 supplied bookmarks, recursive searching,
merges and sprite sheets. The manager's tile splitter, multiple resizer, cropper,
image encoder and animation tester are represented as integrated tools.
The flip-and-invert exercise is represented by image flipping/inversion.
test_image_convert.py is a Pillow test-suite fragment rather than a standalone
user tool, so it is not launched. The old color-picker dependency is replaced
by Tkinter's standard color dialog. Duplicate script copies, bytecode caches,
broken placeholder buttons and hard-coded local paths were not carried over.

This is new implementation code based on those workflows, not a concatenation
of the original modules. Your uploaded ZIP remains unchanged.

## Verification

The code compiles and automated checks exercise transparent image handling,
aspect ratio resize, flips, merge placement, format exports, EXIF orientation,
collision refusal and Base64 checksum recovery. A graphical desktop was not
available in the build environment, so the Tkinter interface has not been
interactively tested there. Run the included test_processing.py for the image
processing checks; it does not require a display.
