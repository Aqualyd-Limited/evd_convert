import argparse
from pathlib import Path

from .to_raw import to_raw


def main():
    parser = argparse.ArgumentParser(prog='evd_converter',
                description='Convert Echoview EVD files to other formats')

    parser.add_argument('-i', '--input', required=True, help='The EVD file to convert')
    parser.add_argument('-o', '--output', help='The file to produce')
    parser.add_argument(
        '-f',
        '--format',
        default='raw',
        help='The format to convert to (default: %(default)s). The only choice is currently "raw"'
        )
    args = parser.parse_args()

    o = Path(args.output) if args.output else args.output

    c = to_raw()

    c.convert(Path(args.input), o)
