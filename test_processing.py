"""Meaningful processing checks; run: python test_processing.py."""
import base64
import hashlib
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from image_studio import load_image, save_image, transform, assemble, unique_path

class ProcessingTests(unittest.TestCase):
    def test_aspect_and_flip_alpha(self):
        im=Image.new('RGBA',(8,4),(255,0,0,100)); im.putpixel((0,0),(0,255,0,200))
        self.assertEqual(transform(im,width=4).size,(4,2))
        self.assertEqual(transform(im,width=4,height=4).size,(4,2))
        self.assertEqual(transform(im,width=4,height=4,fit=False).size,(4,4))
        self.assertEqual(transform(im,flip='Horizontal').getpixel((7,0)),(0,255,0,200))
        self.assertEqual(transform(im,gray=True).getchannel('A').tobytes(),im.getchannel('A').tobytes())

    def test_exports_collisions_and_merge(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); a=p/'a.png'; b=p/'b.png'
            save_image(Image.new('RGBA',(4,2),(255,0,0,255)),a)
            save_image(Image.new('RGBA',(2,4),(0,255,0,255)),b)
            with self.assertRaises(FileExistsError): save_image(Image.new('RGBA',(1,1)),a)
            self.assertEqual(load_image(a).size,(4,2)); self.assertNotEqual(unique_path(a),a)
            merged=assemble([a,b],'Horizontal'); self.assertEqual(merged.size,(6,4)); self.assertEqual(merged.getpixel((4,0)),(0,255,0,255)); self.assertEqual(merged.getpixel((0,3))[3],0)
            grid=assemble([a,b],'Grid',2,(4,4)); self.assertEqual(grid.size,(8,4)); self.assertEqual(grid.getpixel((0,0))[3],0)
            transparent=Image.new('RGBA',(3,3),(255,0,0,0))
            for suffix in ('.jpg','.bmp','.webp','.tif','.gif'):
                out=p/('export'+suffix); save_image(transparent,out)
                with Image.open(out) as im: self.assertEqual(im.size,(3,3))
            with Image.open(p/'export.jpg') as im: self.assertEqual(im.getpixel((0,0)),(255,255,255))
            data=a.read_bytes(); restored=base64.b64decode(base64.b64encode(data),validate=True)
            self.assertEqual(hashlib.sha256(restored).digest(),hashlib.sha256(data).digest())

    def test_exif_orientation(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'rotated.jpg'; im=Image.new('RGB',(8,4)); exif=im.getexif(); exif[274]=6; im.save(p,exif=exif)
            self.assertEqual(load_image(p).size,(4,8))

if __name__=='__main__': unittest.main()
