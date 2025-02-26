#
# The Python Imaging Library.
# $Id$
#
# JPEG (JFIF) file handling
#
# See "Digital Compression and Coding of Continous-Tone Still Images,
# Part 1, Requirements and Guidelines" (CCITT T.81 / ISO 10918-1)
#
# History:
# 1995-09-09 fl   Created
# 1995-09-13 fl   Added full parser
# 1996-03-25 fl   Added hack to use the IJG command line utilities
# 1996-05-05 fl   Workaround Photoshop 2.5 CMYK polarity bug
# 1996-05-28 fl   Added draft support, JFIF version (0.1)
# 1996-12-30 fl   Added encoder options, added progression property (0.2)
# 1997-08-27 fl   Save mode 1 images as BW (0.3)
# 1998-07-12 fl   Added YCbCr to draft and save methods (0.4)
# 1998-10-19 fl   Don't hang on files using 16-bit DQT's (0.4.1)
# 2001-04-16 fl   Extract DPI settings from JFIF files (0.4.2)
# 2002-07-01 fl   Skip pad bytes before markers; identify Exif files (0.4.3)
# 2003-04-25 fl   Added experimental EXIF decoder (0.5)
# 2003-06-06 fl   Added experimental EXIF GPSinfo decoder
# 2003-09-13 fl   Extract COM markers
# 2009-09-06 fl   Added icc_profile support (from Florian Hoech)
# 2009-03-06 fl   Changed CMYK handling; always use Adobe polarity (0.6)
# 2009-03-08 fl   Added subsampling support (from Justin Huff).
#
# Copyright (c) 1997-2003 by Secret Labs AB.
# Copyright (c) 1995-1996 by Fredrik Lundh.
#
# See the README file for information on usage and redistribution.
#

__version__ = "0.6"

import array, struct
from PIL import Image, ImageFile, _binary
from PIL.JpegPresets import presets
from PIL._util import isStringType

i8 = _binary.i8
o8 = _binary.o8
i16 = _binary.i16be
i32 = _binary.i32be

#
# Parser

def Skip(self, marker):
    n = i16(self.fp.read(2))-2
    ImageFile._safe_read(self.fp, n)

def APP(self, marker):
    #
    # Application marker.  Store these in the APP dictionary.
    # Also look for well-known application markers.

    n = i16(self.fp.read(2))-2
    s = ImageFile._safe_read(self.fp, n)

    app = "APP%d" % (marker&15)

    self.app[app] = s # compatibility
    self.applist.append((app, s))

    if marker == 0xFFE0 and s[:4] == b"JFIF":
        # extract JFIF information
        self.info["jfif"] = version = i16(s, 5) # version
        self.info["jfif_version"] = divmod(version, 256)
        # extract JFIF properties
        try:
            jfif_unit = i8(s[7])
            jfif_density = i16(s, 8), i16(s, 10)
        except:
            pass
        else:
            if jfif_unit == 1:
                self.info["dpi"] = jfif_density
            self.info["jfif_unit"] = jfif_unit
            self.info["jfif_density"] = jfif_density
    elif marker == 0xFFE1 and s[:5] == b"Exif\0":
        # extract Exif information (incomplete)
        self.info["exif"] = s # FIXME: value will change
    elif marker == 0xFFE2 and s[:5] == b"FPXR\0":
        # extract FlashPix information (incomplete)
        self.info["flashpix"] = s # FIXME: value will change
    elif marker == 0xFFE2 and s[:12] == b"ICC_PROFILE\0":
        # Since an ICC profile can be larger than the maximum size of
        # a JPEG marker (64K), we need provisions to split it into
        # multiple markers. The format defined by the ICC specifies
        # one or more APP2 markers containing the following data:
        #   Identifying string      ASCII "ICC_PROFILE\0"  (12 bytes)
        #   Marker sequence number  1, 2, etc (1 byte)
        #   Number of markers       Total of APP2's used (1 byte)
        #   Profile data            (remainder of APP2 data)
        # Decoders should use the marker sequence numbers to
        # reassemble the profile, rather than assuming that the APP2
        # markers appear in the correct sequence.
        self.icclist.append(s)
    elif marker == 0xFFEE and s[:5] == b"Adobe":
        self.info["adobe"] = i16(s, 5)
        # extract Adobe custom properties
        try:
            adobe_transform = i8(s[1])
        except:
            pass
        else:
            self.info["adobe_transform"] = adobe_transform

def COM(self, marker):
    #
    # Comment marker.  Store these in the APP dictionary.

    n = i16(self.fp.read(2))-2
    s = ImageFile._safe_read(self.fp, n)

    self.app["COM"] = s # compatibility
    self.applist.append(("COM", s))

def SOF(self, marker):
    #
    # Start of frame marker.  Defines the size and mode of the
    # image.  JPEG is colour blind, so we use some simple
    # heuristics to map the number of layers to an appropriate
    # mode.  Note that this could be made a bit brighter, by
    # looking for JFIF and Adobe APP markers.

    n = i16(self.fp.read(2))-2
    s = ImageFile._safe_read(self.fp, n)
    self.size = i16(s[3:]), i16(s[1:])

    self.bits = i8(s[0])
    if self.bits != 8:
        raise SyntaxError("cannot handle %d-bit layers" % self.bits)

    self.layers = i8(s[5])
    if self.layers == 1:
        self.mode = "L"
    elif self.layers == 3:
        self.mode = "RGB"
    elif self.layers == 4:
        self.mode = "CMYK"
    else:
        raise SyntaxError("cannot handle %d-layer images" % self.layers)

    if marker in [0xFFC2, 0xFFC6, 0xFFCA, 0xFFCE]:
        self.info["progressive"] = self.info["progression"] = 1

    if self.icclist:
        # fixup icc profile
        self.icclist.sort() # sort by sequence number
        if i8(self.icclist[0][13]) == len(self.icclist):
            profile = []
            for p in self.icclist:
                profile.append(p[14:])
            icc_profile = b"".join(profile)
        else:
            icc_profile = None # wrong number of fragments
        self.info["icc_profile"] = icc_profile
        self.icclist = None

    for i in range(6, len(s), 3):
        t = s[i:i+3]
        # 4-tuples: id, vsamp, hsamp, qtable
        self.layer.append((t[0], i8(t[1])//16, i8(t[1])&15, i8(t[2])))

def DQT(self, marker):
    #
    # Define quantization table.  Support baseline 8-bit tables
    # only.  Note that there might be more than one table in
    # each marker.

    # FIXME: The quantization tables can be used to estimate the
    # compression quality.

    n = i16(self.fp.read(2))-2
    s = ImageFile._safe_read(self.fp, n)
    while len(s):
        if len(s) < 65:
            raise SyntaxError("bad quantization table marker")
        v = i8(s[0])
        if v//16 == 0:
            self.quantization[v&15] = array.array("b", s[1:65])
            s = s[65:]
        else:
            return # FIXME: add code to read 16-bit tables!
            # raise SyntaxError, "bad quantization table element size"


#
# JPEG marker table

MARKER = {
    0xFFC0: ("SOF0", "Baseline DCT", SOF),
    0xFFC1: ("SOF1", "Extended Sequential DCT", SOF),
    0xFFC2: ("SOF2", "Progressive DCT", SOF),
    0xFFC3: ("SOF3", "Spatial lossless", SOF),
    0xFFC4: ("DHT", "Define Huffman table", Skip),
    0xFFC5: ("SOF5", "Differential sequential DCT", SOF),
    0xFFC6: ("SOF6", "Differential progressive DCT", SOF),
    0xFFC7: ("SOF7", "Differential spatial", SOF),
    0xFFC8: ("JPG", "Extension", None),
    0xFFC9: ("SOF9", "Extended sequential DCT (AC)", SOF),
    0xFFCA: ("SOF10", "Progressive DCT (AC)", SOF),
    0xFFCB: ("SOF11", "Spatial lossless DCT (AC)", SOF),
    0xFFCC: ("DAC", "Define arithmetic coding conditioning", Skip),
    0xFFCD: ("SOF13", "Differential sequential DCT (AC)", SOF),
    0xFFCE: ("SOF14", "Differential progressive DCT (AC)", SOF),
    0xFFCF: ("SOF15", "Differential spatial (AC)", SOF),
    0xFFD0: ("RST0", "Restart 0", None),
    0xFFD1: ("RST1", "Restart 1", None),
    0xFFD2: ("RST2", "Restart 2", None),
    0xFFD3: ("RST3", "Restart 3", None),
    0xFFD4: ("RST4", "Restart 4", None),
    0xFFD5: ("RST5", "Restart 5", None),
    0xFFD6: ("RST6", "Restart 6", None),
    0xFFD7: ("RST7", "Restart 7", None),
    0xFFD8: ("SOI", "Start of image", None),
    0xFFD9: ("EOI", "End of image", None),
    0xFFDA: ("SOS", "Start of scan", Skip),
    0xFFDB: ("DQT", "Define quantization table", DQT),
    0xFFDC: ("DNL", "Define number of lines", Skip),
    0xFFDD: ("DRI", "Define restart interval", Skip),
    0xFFDE: ("DHP", "Define hierarchical progression", SOF),
    0xFFDF: ("EXP", "Expand reference component", Skip),
    0xFFE0: ("APP0", "Application segment 0", APP),
    0xFFE1: ("APP1", "Application segment 1", APP),
    0xFFE2: ("APP2", "Application segment 2", APP),
    0xFFE3: ("APP3", "Application segment 3", APP),
    0xFFE4: ("APP4", "Application segment 4", APP),
    0xFFE5: ("APP5", "Application segment 5", APP),
    0xFFE6: ("APP6", "Application segment 6", APP),
    0xFFE7: ("APP7", "Application segment 7", APP),
    0xFFE8: ("APP8", "Application segment 8", APP),
    0xFFE9: ("APP9", "Application segment 9", APP),
    0xFFEA: ("APP10", "Application segment 10", APP),
    0xFFEB: ("APP11", "Application segment 11", APP),
    0xFFEC: ("APP12", "Application segment 12", APP),
    0xFFED: ("APP13", "Application segment 13", APP),
    0xFFEE: ("APP14", "Application segment 14", APP),
    0xFFEF: ("APP15", "Application segment 15", APP),
    0xFFF0: ("JPG0", "Extension 0", None),
    0xFFF1: ("JPG1", "Extension 1", None),
    0xFFF2: ("JPG2", "Extension 2", None),
    0xFFF3: ("JPG3", "Extension 3", None),
    0xFFF4: ("JPG4", "Extension 4", None),
    0xFFF5: ("JPG5", "Extension 5", None),
    0xFFF6: ("JPG6", "Extension 6", None),
    0xFFF7: ("JPG7", "Extension 7", None),
    0xFFF8: ("JPG8", "Extension 8", None),
    0xFFF9: ("JPG9", "Extension 9", None),
    0xFFFA: ("JPG10", "Extension 10", None),
    0xFFFB: ("JPG11", "Extension 11", None),
    0xFFFC: ("JPG12", "Extension 12", None),
    0xFFFD: ("JPG13", "Extension 13", None),
    0xFFFE: ("COM", "Comment", COM)
}


def _accept(prefix):
    return prefix[0:1] == b"\377"

##
# Image plugin for JPEG and JFIF images.

class JpegImageFile(ImageFile.ImageFile):

    format = "JPEG"
    format_description = "JPEG (ISO 10918)"

    def _open(self):

        s = self.fp.read(1)

        if i8(s[0]) != 255:
            raise SyntaxError("not a JPEG file")

        # Create attributes
        self.bits = self.layers = 0

        # JPEG specifics (internal)
        self.layer = []
        self.huffman_dc = {}
        self.huffman_ac = {}
        self.quantization = {}
        self.app = {} # compatibility
        self.applist = []
        self.icclist = []

        while True:

            s = s + self.fp.read(1)

            i = i16(s)

            if i in MARKER:
                name, description, handler = MARKER[i]
                # print hex(i), name, description
                if handler is not None:
                    handler(self, i)
                if i == 0xFFDA: # start of scan
                    rawmode = self.mode
                    if self.mode == "CMYK":
                        rawmode = "CMYK;I" # assume adobe conventions
                    self.tile = [("jpeg", (0,0) + self.size, 0, (rawmode, ""))]
                    # self.__offset = self.fp.tell()
                    break
                s = self.fp.read(1)
            elif i == 0 or i == 65535:
                # padded marker or junk; move on
                s = "\xff"
            else:
                raise SyntaxError("no marker found")

    def draft(self, mode, size):

        if len(self.tile) != 1:
            return

        d, e, o, a = self.tile[0]
        scale = 0

        if a[0] == "RGB" and mode in ["L", "YCbCr"]:
            self.mode = mode
            a = mode, ""

        if size:
            scale = max(self.size[0] // size[0], self.size[1] // size[1])
            for s in [8, 4, 2, 1]:
                if scale >= s:
                    break
            e = e[0], e[1], (e[2]-e[0]+s-1)//s+e[0], (e[3]-e[1]+s-1)//s+e[1]
            self.size = ((self.size[0]+s-1)//s, (self.size[1]+s-1)//s)
            scale = s

        self.tile = [(d, e, o, a)]
        self.decoderconfig = (scale, 1)

        return self

    def load_djpeg(self):

        # ALTERNATIVE: handle JPEGs via the IJG command line utilities

        import tempfile, os
        f, path = tempfile.mkstemp()
        os.close(f)
        eval(self.filename)
        if os.path.exists(self.filename):
            os.system("djpeg '%s' >'%s'" % (self.filename, path))
        else:
            raise ValueError("Invalid Filename")

        try:
            self.im = Image.core.open_ppm(path)
        finally:
            try: os.unlink(path)
            except: pass

        self.mode = self.im.mode
        self.size = self.im.size

        self.tile = []

    def _getexif(self):
        return _getexif(self)


def _getexif(self):
    # Extract EXIF information.  This method is highly experimental,
    # and is likely to be replaced with something better in a future
    # version.
    from PIL import TiffImagePlugin
    import io
    def fixup(value):
        if len(value) == 1:
            return value[0]
        return value
    # The EXIF record consists of a TIFF file embedded in a JPEG
    # application marker (!).
    try:
        data = self.info["exif"]
    except KeyError:
        return None
    file = io.BytesIO(data[6:])
    head = file.read(8)
    exif = {}
    # process dictionary
    info = TiffImagePlugin.ImageFileDirectory(head)
    info.load(file)
    for key, value in info.items():
        exif[key] = fixup(value)
    # get exif extension
    try:
        file.seek(exif[0x8769])
    except KeyError:
        pass
    else:
        info = TiffImagePlugin.ImageFileDirectory(head)
        info.load(file)
        for key, value in info.items():
            exif[key] = fixup(value)
    # get gpsinfo extension
    try:
        file.seek(exif[0x8825])
    except KeyError:
        pass
    else:
        info = TiffImagePlugin.ImageFileDirectory(head)
        info.load(file)
        exif[0x8825] = gps = {}
        for key, value in info.items():
            gps[key] = fixup(value)
    return exif

# --------------------------------------------------------------------
# stuff to save JPEG files

RAWMODE = {
    "1": "L",
    "L": "L",
    "RGB": "RGB",
    "RGBA": "RGB",
    "RGBX": "RGB",
    "CMYK": "CMYK;I", # assume adobe conventions
    "YCbCr": "YCbCr",
}

zigzag_index = ( 0,  1,  5,  6, 14, 15, 27, 28,
                 2,  4,  7, 13, 16, 26, 29, 42,
                 3,  8, 12, 17, 25, 30, 41, 43,
                 9, 11, 18, 24, 31, 40, 44, 53,
                10, 19, 23, 32, 39, 45, 52, 54,
                20, 22, 33, 38, 46, 51, 55, 60,
                21, 34, 37, 47, 50, 56, 59, 61,
                35, 36, 48, 49, 57, 58, 62, 63)

samplings = {
             (1, 1, 1, 1, 1, 1): 0,
             (2, 1, 1, 1, 1, 1): 1,
             (2, 2, 1, 1, 1, 1): 2,
            }

def convert_dict_qtables(qtables):
    qtables = [qtables[key] for key in xrange(len(qtables)) if qtables.has_key(key)]
    for idx, table in enumerate(qtables):
        qtables[idx] = [table[i] for i in zigzag_index]
    return qtables

def get_sampling(im):
    sampling = im.layer[0][1:3] + im.layer[1][1:3] + im.layer[2][1:3]
    return samplings.get(sampling, -1)

def _save(im, fp, filename):

    try:
        rawmode = RAWMODE[im.mode]
    except KeyError:
        raise IOError("cannot write mode %s as JPEG" % im.mode)

    info = im.encoderinfo

    dpi = info.get("dpi", (0, 0))

    quality = info.get("quality", 0)
    subsampling = info.get("subsampling", -1)
    qtables = info.get("qtables")

    if quality == "keep":
        quality = 0
        subsampling = "keep"
        qtables = "keep"
    elif quality in presets:
        preset = presets[quality]
        quality = 0
        subsampling = preset.get('subsampling', -1)
        qtables = preset.get('quantization')
    elif not isinstance(quality, int):
        raise ValueError("Invalid quality setting")
    else:
        if subsampling in presets:
            subsampling = presets[subsampling].get('subsampling', -1)
        if qtables in presets:
            qtables = presets[qtables].get('quantization')

    if subsampling == "4:4:4":
        subsampling = 0
    elif subsampling == "4:2:2":
        subsampling = 1
    elif subsampling == "4:1:1":
        subsampling = 2
    elif subsampling == "keep":
        if im.format != "JPEG":
            raise ValueError("Cannot use 'keep' when original image is not a JPEG")
        subsampling = get_sampling(im)

    def validate_qtables(qtables):
        if qtables is None:
            return qtables
        if isStringType(qtables):
            try:
                lines = [int(num) for line in qtables.splitlines()
                         for num in line.split('#', 1)[0].split()]
            except ValueError:
                raise ValueError("Invalid quantization table")
            else:
                qtables = [lines[s:s+64] for s in xrange(0, len(lines), 64)]
        if isinstance(qtables, (tuple, list, dict)):
            if isinstance(qtables, dict):
                qtables = convert_dict_qtables(qtables)
            elif isinstance(qtables, tuple):
                qtables = list(qtables)
            if not (0 < len(qtables) < 5):
                raise ValueError("None or too many quantization tables")
            for idx, table in enumerate(qtables):
                try:
                    if len(table) != 64:
                        raise
                    table = array.array('b', table)
                except TypeError:
                    raise ValueError("Invalid quantization table")
                else:
                    qtables[idx] = list(table)
            return qtables

    if qtables == "keep":
        if im.format != "JPEG":
            raise ValueError("Cannot use 'keep' when original image is not a JPEG")
        qtables = getattr(im, "quantization", None)
    qtables = validate_qtables(qtables)

    extra = b""

    icc_profile = info.get("icc_profile")
    if icc_profile:
        ICC_OVERHEAD_LEN = 14
        MAX_BYTES_IN_MARKER = 65533
        MAX_DATA_BYTES_IN_MARKER = MAX_BYTES_IN_MARKER - ICC_OVERHEAD_LEN
        markers = []
        while icc_profile:
            markers.append(icc_profile[:MAX_DATA_BYTES_IN_MARKER])
            icc_profile = icc_profile[MAX_DATA_BYTES_IN_MARKER:]
        i = 1
        for marker in markers:
            size = struct.pack(">H", 2 + ICC_OVERHEAD_LEN + len(marker))
            extra = extra + (b"\xFF\xE2" + size + b"ICC_PROFILE\0" + o8(i) + o8(len(markers)) + marker)
            i = i + 1

    # get keyword arguments
    im.encoderconfig = (
        quality,
        # "progressive" is the official name, but older documentation
        # says "progression"
        # FIXME: issue a warning if the wrong form is used (post-1.1.7)
        "progressive" in info or "progression" in info,
        info.get("smooth", 0),
        "optimize" in info,
        info.get("streamtype", 0),
        dpi[0], dpi[1],
        subsampling,
        qtables,
        extra,
        info.get("exif", b"")
        )


    # if we optimize, libjpeg needs a buffer big enough to hold the whole image in a shot.
    # Guessing on the size, at im.size bytes. (raw pizel size is channels*size, this
    # is a value that's been used in a django patch.
    # https://github.com/jdriscoll/django-imagekit/issues/50
    bufsize=0
    if "optimize" in info or "progressive" in info or "progression" in info:
        bufsize = im.size[0]*im.size[1]

    # The exif info needs to be written as one block, + APP1, + one spare byte.
    # Ensure that our buffer is big enough
    bufsize = max(ImageFile.MAXBLOCK, bufsize, len(info.get("exif",b"")) + 5 )

    ImageFile._save(im, fp, [("jpeg", (0,0)+im.size, 0, rawmode)], bufsize)

def _save_cjpeg(im, fp, filename):
    # ALTERNATIVE: handle JPEGs via the IJG command line utilities.
    import os
    file = im._dump()
    os.system("cjpeg %s >%s" % (file, filename))
    try: os.unlink(file)
    except: pass

# -------------------------------------------------------------------q-
# Registry stuff

Image.register_open("JPEG", JpegImageFile, _accept)
Image.register_save("JPEG", _save)

Image.register_extension("JPEG", ".jfif")
Image.register_extension("JPEG", ".jpe")
Image.register_extension("JPEG", ".jpg")
Image.register_extension("JPEG", ".jpeg")

Image.register_mime("JPEG", "image/jpeg")
