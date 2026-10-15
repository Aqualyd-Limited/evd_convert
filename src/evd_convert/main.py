import argparse
from pathlib import Path

import arrow
from rich.console import Console

from .to_raw import to_raw


def main():
    parser = argparse.ArgumentParser(prog='evd_converter',
                description='Convert Echoview EVD files to other formats')

    parser.add_argument('-i', '--input', help='An EVD file to convert')
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
        console = Console()
        files = list(Path(args.directory).glob('*.evd'))
        last_ping_time = arrow.Arrow(1970, 1, 1, 0, 0, 0)
        for i, f in enumerate(files):
            console.print(
                f'Converting [green]{f.name}[/green] [blue]({i+1} of {len(files)})',
                highlight=False
            )
            last_ping_time = c.convert(f, last_ping_time=last_ping_time)

    else:
        c.convert(Path(args.input))
