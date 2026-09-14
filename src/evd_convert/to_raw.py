import xml.etree.ElementTree as ET
from pathlib import Path


class to_raw:

    def __init__(self):
        pass

    def convert(self, evd_file: Path, out_file: Path|None):

        if not out_file:
            out_file = evd_file.with_suffix('.raw')

        # Read the XML header and structural tags
        with open(evd_file, 'r', encoding='ansi', errors='ignore') as f:
            content = f.read()

        try:
            return ET.fromstring(content)
        except ET.ParseError as e:
            print(f"Standard XML parse partial/fail due to embedded binary blocks: {e}")
            return None
