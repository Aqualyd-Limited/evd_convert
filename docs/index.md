## Installing

EVD Convert is available on [PyPi](https://pypi.org) as
[`evd-convert`](https://pypi.org/project/evd-convert/).

If using [uv](https://docs.astral.sh/uv/) (to install uv, use these [instructions](https://docs.astral.sh/uv/getting-started/installation/)), run it with:

```bash
uvx evd-convert -h
```

If using pip, install evd-convert using:

```bash
pip install evd-convert
```

and then run using

```bash
evd-convert -h
```

To upgrade to the latest version run evd-convert using:

```bash
uvx evd-convert@latest -h
```

or with pip:

```bash
pip install evd-convert --upgrade
```

## Using evd-convert

EVD-convert is a command line program that extracts selected beams from Seapix 3D sonar
data files in the Echoview EVD data file format and writes them into the
Simrad EK60 raw file format. This facilitates the use of
Seapix data in conventional fisheries acoustic processing software.

EVD-convert can convert individual files or a directory of files. In all cases, the
raw files are written in the same directory as the EVD files. Details on the command line options
are available with the ```-h``` command:

```bash
uvx evd-convert -h
```

which produces:

```bash
usage: evd_converter [-h] [-i INPUT] [-d DIRECTORY] [-f FORMAT]

Convert Echoview EVD files to other formats

options:
  -h, --help            show this help message and exit
  -i, --input INPUT     An EVD file to convert
  -d, --directory DIRECTORY
                        A directory of EVD files to convert
  -f, --format FORMAT   The format to convert to (default: raw).
```

The ```-f/--format``` option is optional and defaults to 'raw'. The ```-i``` and ```-d```
are mutually exclusive and if both are given the ```-d``` option takes precedence.

## Selecting beam type and individual beams

Currently, the conversion extracts the downward Seapix beam and two beams 30° to either side of
the downwards beam. Only the Sv STV beams are converted (namely, those named `Sv pings STV` and
`Sv pings Double RX STV`). Other beams and beam types can be exported by checking out
the source code from github and editing the `to_raw.py` file on lines 57-68 as required.

The transducer name in the raw files includes the beam type and the beam number. Beam 128 is
vertically downwards, while beams 64 and 192 are the beams 30° to either the side of beam 128.

If needed, the choice of beam type and beam number can be made part of the command line
interface - create an issue on the evd-convert repository to request this and other
suggestions/changes.
