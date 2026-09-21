import re
import string
import time
from pathlib import Path  # ruff: ignore[typing-only-standard-library-import]

import xmltodict

PRINTABLE = set(string.printable.encode('ascii'))


def first_non_printable(b_str: bytes) -> int:
    """Return the index of the first non-printable byte or None if all are printable."""
    return next((i for i, b in enumerate(b_str) if b not in PRINTABLE), None)


class to_raw:

    def __init__(self):
        pass

    def convert(self, evd_file: Path, out_file: Path | None):

        if not out_file:
            out_file = evd_file.with_suffix('.raw')

        # Detect FileInfo and Packet XML-formatted elements
        packet_pattern = re.compile(b'(<FileInfo(.*?)/>)|(<Packet(.*?)</Packet>)', re.DOTALL)

        # some testing indicates that this size gives a short overall run time
        chunk_size = 64 * 65536
        buffer = bytearray()

        num_packets = 0

        start_t = time.perf_counter()

        with evd_file.open(mode='rb') as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:  # EOF
                    break

                # Append new data to the leftover buffer from the previous read
                buffer.extend(chunk)

                # Search for packets in the current buffer
                for match in packet_pattern.finditer(buffer):
                    element = match.group(0)

                    if element.startswith(b'<FileInfo'):
                        file_info = xmltodict.parse(element.decode('ansi'))
                        print(file_info)
                    elif element.startswith(b'<Packet'):
                        i = first_non_printable(element)
                        if i is not None:
                            # find the trailing > character that seems to always immediately
                            # precedes the start of any binary data
                            binary_start = element[:i].rfind(b'>') + 1

                            # make a valid xml element by adding the xml tag closures
                            # TODO: </Packet> is always needed, by PingData can vary
                            # TODO: cope with rfind returning -1?
                            binary_tag = b'PingData'

                            binary_end_tag = b'</' + binary_tag + b'>'
                            header = element[:binary_start] + binary_end_tag + b'</Packet>'
                            # and parse into a dict
                            packet_info = xmltodict.parse(header.decode('ansi'))
                            print(packet_info)

                            # The end of the element always has the closing XML tags, so find those
                            # and any binary data
                            binary_end = element.rfind(binary_end_tag)
                            binary_data = element[binary_start:binary_end]
                            print(
                                f'{binary_tag.decode("ansi")} element has {len(binary_data)}'
                                ' bytes of binary data')
                            # TODO: convert into numbers as per the metadata in packet_info

                    print()

                    num_packets += 1

                    # remove the used bytes from the buffer
                    buffer = buffer[len(match.group(0)):]

        run_time = time.perf_counter() - start_t
        print(f'Run time {run_time:.2f} s')

        print(f'Read {num_packets} packets')
