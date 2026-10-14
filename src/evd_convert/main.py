import argparse
from pathlib import Path

from rich import print

from .to_raw import to_raw


def main():
    parser = argparse.ArgumentParser(prog='evd_converter',
                description='Convert Echoview EVD files to other formats')

    parser.add_argument('-i', '--input', help='The EVD file to convert')
    # parser.add_argument('-o', '--output', help='The file to produce')
    parser.add_argument('-d', '--directory', help='A directory of EVD files to convert')
    parser.add_argument(
        '-f',
        '--format',
        default='raw',
        help='The format to convert to (default: %(default)s). The only choice is currently "raw"'
        )
    args = parser.parse_args()

    c = to_raw()

    if args.directory:
        files = list(Path(args.directory).glob('*.evd'))
        for i, f in enumerate(files):
            print(
                f'Converting [green]{f.name}[/green] [blue]({i+1} of {len(files)})',
                highlight=False
            )
            c.convert(f)

    else:
        c.convert(Path(args.input))
