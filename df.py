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
        self.id = stream.read(0x04).replace(b'\x00', b'').decode("utf-8")

        assert stream.read(4) == b'\x00' * 4
        unk2 = struct.unpack("<L", stream.read(4))[0] # 10 00

        self.length = struct.unpack("<L", stream.read(4))[0]

        if self.id == 'KWAV':
            self.chunk = KWAV(stream)
        elif self.id == 'ANG':
            self.chunk = ANG(stream, self.length)
        elif self.id == 'XXXX':
            if self.length == 0x0300: # Palette
                self.chunk = stream.read(self.length)
            else:
                self.chunk = Container(stream)
        else:
            self.chunk = stream.read(self.length)

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

        if sncm:
            logging.warning("Container: Found internal SNCM")
            self.sncm = stream.read(length - sncm)

    def export(self, directory, filename):
        self.chunk.export(directory, filename)

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

class ANG(Object):
    def __init__(self, stream, length):
        self.data = stream.read(length)

    def export(self, directory, filename):
        return False

        filename = os.path.join(directory, "{}.dat".format(filename))
        with open(filename, 'wb') as f:
            f.write(self.data)
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
        try:
            while stream.tell() < stream.size():
                start = stream.tell()
                chunk = CDChunk(stream)

                if not chunk_ids.get(chunk.id):
                    chunk_ids.update({chunk.id: 0})

                chunk_ids[chunk.id] += 1
                logging.debug(
                    "process: (0x{:012x} \\ 0x{:012x}) [{:2.2f}%] Chunk: {} (0x{:08x} bytes)".format(
                        start, stream.size(), start/stream.size() * 100, chunk.id, chunk.length)
                )

                if args.export:
                    if callable(getattr(chunk.chunk, "export", None)):
                        chunk.chunk.export(args.export, "{}-{}".format(chunk.id, chunk_ids[chunk.id]))

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
    logging.basicConfig(level=logging.DEBUG)
    main()
