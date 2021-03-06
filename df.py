#!/usr/bin/python3

import argparse
import logging
import struct
import os
from pathlib import Path
import glob
import mmap
import io
import subprocess
import uuid
import json

import PIL.Image as PILImage
from mrcrowbar import utils

def value_assert(stream, target, type="value", warn=False):
    ax = stream
    try:
        ax = stream.read(len(target))
    except AttributeError:
        pass

    msg = "Expected {} {}{}, received {}{}".format(
        type, target, " (0x{:0>4x})".format(target) if isinstance(target, int) else "",
        ax, " (0x{:0>4x})".format(ax) if isinstance(ax, int) else "",
    )
    if warn and ax != target:
        logging.warning(msg)
    else:
        assert ax == target, msg

def encode_filename(filename, fmt):
    if filename[-4:] != ".{}".format(fmt.lower()):
        filename += (".{}".format(fmt.lower()))

    return filename

class Object:
    def __format__(self, spec):
        return self.__repr__()

class HDChunk(Object):
    def __init__(self, stream):
        self.id = stream.read(0x04).replace(b'\x00', b'').decode("utf-8")
        unk1 = struct.unpack("<L", stream.read(4))[0]
        unk2 = struct.unpack("<L", stream.read(4))[0]
        unk3 = struct.unpack("<L", stream.read(4))[0]
        # assert unk1 == unk2

        assert stream.read(0x04) == b'COMP'
        unk4 = struct.unpack("<L", stream.read(4))[0]
        unk5 = struct.unpack("<L", stream.read(4))[0]
        # assert unk3 == unk4

        self.length = struct.unpack("<L", stream.read(4))[0]
        data = stream.read(self.length)

class CDChunk(Object):
    def __init__(self, stream):
        self.chunk = None
        self.id = stream.read(0x04).replace(b'\x00', b'').decode("utf-8")

        assert stream.read(4) == b'\x00' * 4
        unk2 = struct.unpack("<L", stream.read(4))[0] # 10 00

        self.length = struct.unpack("<L", stream.read(4))[0]

        if self.id == 'KWAV':
            self.chunk = KWAV(stream)
        elif self.id == 'ANG':
            self.chunk = ANG(stream)
        elif self.id == 'FNT0':
            self.chunk = FNT0(stream, self.length)
        elif self.id == 'CHR':
            self.chunk = CHR(stream, self.length)
        elif self.id == 'P800':
            self.chunk = ANGFrame(stream)
        elif self.id == 'SNCM':
            self.chunk = SNCM(stream)
        elif self.id == 'XXXX':
            if self.length == 0x0300: # Palette
                self.chunk = stream.read(self.length)
            else:
                self.chunk = Container(stream)
        else:
            logging.warning("CDChunk: Unknown chunk type: {}".format(self.id))
            self.chunk = stream.read(self.length)

    def export(self, directory, filename, **kwargs):
        if callable(getattr(self.chunk, "export", None)):
            self.chunk.export(directory, filename)
        else:
            with open(os.path.join(directory, filename), 'wb') as of:
                of.write(self.chunk)

class Container(Object):
    def __init__(self, stream):
        code = struct.unpack("<L", stream.read(4))[0] # 00 00
        if code == 0x0c:
            sncm = struct.unpack("<L", stream.read(4))[0] # 00 00
        else:
            sncm = None

        length = struct.unpack("<L", stream.read(4))[0] # 00 00

        id =  stream.read(4).replace(b'\x00', b'').decode("utf-8")
        if id == "KWAV":
            logging.debug("Container: Processing internal WAV...")
            self.chunk = KWAV(stream, check=False)
        else:
            raise TypeError("Unknown type in container: {}".format(id))

        self.sncm = None
        if sncm:
            logging.warning("Container: Found internal SNCM (0x{:012x} bytes)".format(length - sncm))
            self.sncm = stream.read(length - sncm)

    def export(self, directory, filename, **kwargs):
        self.chunk.export(directory, filename)
        if self.sncm:
            with open(os.path.join(directory, filename), 'wb') as of:
                of.write(self.sncm)

class FNT0(Object):
    def __init__(self, stream, size, check=True):
        self.data = stream.read(size)

    def export(self, directory, filename, **kwargs):
        with open(os.path.join(directory, filename), 'wb') as of:
            of.write(self.data)

class CHR(Object):
    def __init__(self, stream, check=True):
        assert stream.read(4) == b'CHR\x00'
        assert stream.read(4) == b'\x00' * 4

        length = struct.unpack("<L", stream.read(4))[0]
        assert stream.read(0x100) == b'\x00' * 0x100

        component_count =  struct.unpack("<L", stream.read(4))[0]
        logging.debug("CHR: Expecting {} components".format(component_count))

        self.names = []
        for i in range(component_count):
            name = {
                "string": stream.read(0x13c).replace(b'\x00', b'').decode("utf-8"),
                "id": struct.unpack("<L", stream.read(4))[0]
            }
            self.names.append(name)
            logging.debug("CHR: Registered component: {}".format(name))

        actn_count = struct.unpack("<L", stream.read(4))[0]
        logging.debug("CHR: Expecting {} ACTN chunks".format(actn_count))

        self.actns = []
        for i in range(actn_count):
            logging.debug("~~~~ ({}) ACTN ~~~~".format(i))
            self.actns.append(ACTN(stream, component_count))
            logging.debug("~" * 20)
        
    def export(self, directory, filename, **kwargs):
        if filename:
            directory = os.path.join(directory, filename)
        
        Path(directory).mkdir(parents=True, exist_ok=True)
        for i, actn in enumerate(self.actns):
            actn.export(directory, str(i))

class ACTN(Object):
    def __init__(self, stream, component_count, check=True):
        if check:
            assert stream.read(4) == b'ACTN'

        assert stream.read(4) == b'\x00' * 4
        length = struct.unpack("<L", stream.read(4))[0]
        assert stream.read(0x100) == b'\x00' * 0x100

        self.name = stream.read(0x40).replace(b'\x00', b'').decode("utf-8")
        self.parts = []
        for i in range(component_count):
            assert stream.read(4) == b'PART'
            assert stream.read(4) == b'\x00' * 4

            part_length = struct.unpack("<L", stream.read(4))[0]
            name = stream.read(0x40).replace(b'\x00', b'').decode("utf-8")
            name2 = stream.read(0x40).replace(b'\x00', b'').decode("utf-8")

            unk1 = struct.unpack("<L", stream.read(4))[0]
            if unk1 == 0:
                self.parts.append(None)
                logging.debug("CHR: No part")
                continue

            self.parts.append(ANG(stream))

    def export(self, directory, filename, **kwargs):
        if filename:
            directory = os.path.join(directory, filename)
                   
        Path(directory).mkdir(parents=True, exist_ok=True)
        for i, part in enumerate(self.parts):
            if part: part.export(directory, str(i))

class KWAV(Object):
    def __init__(self, stream, check=True):
        if check:
            assert stream.read(4) == b'KWAV'

        length = struct.unpack("<L", stream.read(4))[0]
        self.data = stream.read(length)

    def export(self, directory, filename=None):
        if not filename:
            filename = "{}-{}".format("KWAV", str(uuid.uuid4()))

        filename = os.path.join(directory, "{}.wav".format(filename))
        command = ['ffmpeg', '-y', '-f', 's16le', '-ar', '11.025k', '-ac', '1', '-i', 'pipe:', filename]
        with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as p:
            p.stdin.write(self.data)

        logging.debug("KWAV.export: Wrote output on {}".format(filename))

class SNCM(Object): # What does this mean?
    def __init__(self, stream):
        assert stream.read(4) == b'SNCM'
        assert stream.read(4) == b'\x00' * 4

        line_count = struct.unpack("<L", stream.read(4))[0]
        logging.debug("SNCM: Expecting {} lines".format(line_count))

        # Do we know how mnay azeros there are?
        while stream.read(1) == b'\x00':
            continue

        stream.seek(stream.tell() - 1)

        self.lines = []
        for i  in range(line_count):
            size = struct.unpack("<L", stream.read(4))[0]
            if i == 0:
                self.size = size

            run = stream.read(size)

            self.lines.append(run)

    def export(self, directory, filename, **kwargs):
        with open("{}.txt".format(os.path.join(directory, filename)), 'w') as of:
            for line in self.lines:
                of.write(str(list(line)))
            # of.write(b''.join(self.lines))

class ANG(Object):
    def __init__(self, stream):
        start = stream.tell()

        assert stream.read(4) == b'ANG\x00'
        assert stream.read(4) == b'\x00' * 4
        
        unk1 = struct.unpack("<L", stream.read(4))[0] # 01 00 00 00
        logging.debug("ANG: Unk1: {}".format(unk1))
        assert unk1 == 1

        frame_count = struct.unpack("<L", stream.read(4))[0]
        logging.debug("ANG: Expecting {} frames".format(frame_count))

        assert stream.read(4) == b'\x00' * 4
        unk2 = struct.unpack("<L", stream.read(4))[0] # 00 00 00 01
        logging.debug("ANG: Unk2: {}".format(unk1))

        offsets = []
        for _ in range(frame_count + 1):
            offset = struct.unpack("<L", stream.read(4))[0]
            logging.debug("ANG: Registered frame offset 0x{:04x}".format(offset))
            offsets.append(offset)

        stream.seek(offsets[0] + start) # TODO: Determine the lengths of this field

        self.meta_frames = []
        for _ in range(frame_count):
            header = stream.read(2)
            while header != b'\x02\x7f':
                header = stream.read(2)

            meta_frame = {
                "x": struct.unpack("<H", stream.read(2))[0],
                "y": struct.unpack("<H", stream.read(2))[0],
                "n": struct.unpack("<H", stream.read(2))[0]
            }

            self.meta_frames.append(meta_frame)
            assert stream.read(2) == b'\x00\x00'

            footer = stream.read(2)
            if footer == b'\x02\x7f':
                stream.seek(stream.tell() - 2)
            else:
                assert footer == b'\x01\x7f'

            logging.debug("ANG: Registered frame header: {}".format(meta_frame))

        # HACK: I don't actually know what the unk2 signifies, but it means a
        # strante frame structure here.
        footer = stream.read(2)
        while footer != b'\x00\x7f':
            footer = stream.read(2)

        self.frames = []
        for frame in self.meta_frames:
            logging.debug("**** Reading frame {:03d} ****".format(frame["n"]))
            self.frames.append({"frame": ANGFrame(stream)})
            logging.debug("***************************")

    def export(self, directory, filename, **kwargs):
        if filename:
            directory = os.path.join(directory, filename)

        Path(directory).mkdir(parents=True, exist_ok=True)

        for i, frame in enumerate(self.frames):
            frame["frame"].export(directory, str(i))

        with open(os.path.join(directory, "anim.json"), 'w') as header:
            json.dump(self.meta_frames, fp=header)

class ANGFrame(Object):
    def __init__(self, stream, check=True):
        if check:
            value_assert(stream, b'P800')

        self.width = struct.unpack("<H", stream.read(2))[0]
        logging.debug("ANGFrame: Width: 0x{:04x}".format(self.width))

        self.line_count = struct.unpack("<H", stream.read(2))[0]
        logging.debug("ANGFrame: Expecting {} lines".format(self.line_count))

        compressed = struct.unpack("<L", stream.read(4))[0]
        logging.debug("ANGFrame: Compression: 0x{:04x}".format(compressed))

        self.lines = []
        if self.width == 0:
            return
        
        if compressed == 0x0201: # Crazy RLE format
            pos = stream.tell()
            end = struct.unpack("<L", stream.read(4))[0] + pos # byte count to end STARTS here, and also starts here for all succeeding offsets

            self.offsets = []
            for _ in range(self.line_count):
                offset = struct.unpack("<L", stream.read(4))[0]
                logging.debug("ANGFrame: Registered offset 0x{:04x} (true: 0x{:04x})".format(offset, offset + pos))
                self.offsets.append(offset + pos)

            self.offsets.append(end)

            # find ~/tmp/rr2 -name "*.dat"  -exec ./df.py '{}' \;
            value_assert(stream.tell(), self.offsets[0])
            prev = self.offsets[0]

            for i, offset in enumerate(self.offsets[1:]):
                end = offset
                length = offset - stream.tell()
                logging.debug("ANGFrame: ({}) Reading line 0x{:04x} (0x{:04x}) -> 0x{:04x} (0x{:04x} bytes)".format(i+1, stream.tell(), prev, offset, length))
                line = []
                while stream.tell() < end:
                    op = int.from_bytes(stream.read(1), byteorder="little")
                    if op >> 7: # RLE byte next
                        color = stream.read(1)
                        run = color * (op & 0b01111111)
                    else: # unencoded data
                        run = stream.read(op)

                    line.append(run)

                line = b''.join(line)
                line += b'\x00' * max(0, self.width - len(line))
                assert len(line) == self.width
                self.lines.append(line)
        elif compressed == 0x0001: # uncompressed bitmap
            for i in range(self.line_count):
                self.lines.append(stream.read(self.width))
        else:
            raise ValueError("Unknown compression type: 0x{:04x}".format(compressed))

    def export(self, directory, filename, fmt="png"):
        if self.width == 0 or self.line_count == 0:
            return
        
        output = PILImage.frombytes("P", (self.width, self.line_count), b''.join(self.lines))
        output.save(encode_filename(os.path.join(directory, filename), fmt), fmt)

def process(filename):
    logging.debug("Processing file: {}".format(filename))
    if args.export:
        Path(args.export).mkdir(parents=True, exist_ok=True)

    with open(filename, mode='rb') as f:
        stream = mmap.mmap(f.fileno(), length=0, access=mmap.ACCESS_READ)
        assert stream.read(4) == b'\x44\x46\x00\x00'

        chunks = []
        # stream.seek(0xea6c)
        # stream.seek(0x5dcc)
        # stream.seek(0x1d5cc39)
        # stream.seek(0x5e96e8)
        stream.seek(0x010882)
        chunk_ids = {}
        file_map = []
        try:
            while stream.tell() < stream.size():
                start = stream.tell()
                chunk = CDChunk(stream)

                if not chunk_ids.get(chunk.id):
                    chunk_ids.update({chunk.id: 0})

                chunk_ids[chunk.id] += 1
                logging.info(
                    "process: (0x{:012x} \\ 0x{:012x}) [{:2.2f}%] Chunk: {} (0x{:08x} bytes)".format(
                        start, stream.size(), start/stream.size() * 100, chunk.id, chunk.length)
                )

                file_map.append({
                    "id": chunk.id,
                    "start": "0x{:012x}".format(start),
                    "length": chunk.length,
                    "chunk": True if chunk.chunk else False
                })

                if args.export:
                    chunk.export(args.export, "{}-{}".format(chunk.id, chunk_ids[chunk.id]))

                    with open(os.path.join(args.export, "df.json"), 'w') as fmap:
                        json.dump(file_map, fp=fmap)

        except Exception as e:
            logging.error("Exception at {}:{:012x}".format(filename, stream.tell()))
            raise

def main():
    process(args.input)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="jumpstart", formatter_class=argparse.RawTextHelpFormatter,
         description="""Parse asset structures and extract assets from RR interactive titles."""
    )

    parser.add_argument(
        "input", help="Pass a DF filename to process the file."
    )

    parser.add_argument(
        "export", nargs='?', default=None,
        help="Specify the location for exporting assets, or omit to skip export."
    )

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    main()
