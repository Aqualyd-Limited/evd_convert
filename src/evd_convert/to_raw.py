import re
import string
import struct
import time
from datetime import datetime
from pathlib import Path  # ruff: ignore[typing-only-standard-library-import]

import arrow
import numpy as np
import xmltodict
from construct import (
    Array,
    Const,
    Default,
    Float32l,
    GreedyBytes,
    If,
    Int16sl,
    Int32sl,
    Int64ul,
    PaddedString,
    Struct,
    Timestamp,
    this,
)
from pynmeagps import GET, NMEAMessage

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

        ptypes = []
        first_ping = True
        file_info = None

        with evd_file.open(mode='rb') as f, out_file.open(mode='wb') as raw_fid:
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
                        print('Element with tag Fileinfo found')
                        print(file_info)

                    elif element.startswith(b'<Packet'):
                        i = first_non_printable(element)
                        if i is not None:
                            # find the trailing > character that seems to always immediately
                            # precede the start of any binary data
                            binary_start = element[:i].rfind(b'>') + 1

                            # make a valid xml element by adding the xml tag closures
                            # TODO: </Packet> is always needed, but PingData can vary
                            # TODO: cope with rfind returning -1?
                            binary_tag = b'PingData'

                            binary_end_tag = b'</' + binary_tag + b'>'
                            header = element[:binary_start] + binary_end_tag + b'</Packet>'
                            # and parse into a dict
                            packet_info = xmltodict.parse(header.decode('ansi'))
                            ptypes.append(packet_info['Packet']['@Type'])

                            # The end of the element always has the closing XML tags, so find those
                            # and any binary data
                            binary_end = element.rfind(binary_end_tag)
                            binary_data = element[binary_start:binary_end]

                        else: # a purely text packet
                            packet_info = xmltodict.parse(element.decode('ansi'))
                            ptypes.append(packet_info['Packet']['@Type'])

                        match packet_info['Packet']['@Type']:
                            case 'MultibeamPing':
                                d = parse_multibeamping(packet_info, binary_data)
                                print(d)
                                if first_ping:
                                    first_ping = False
                                    write_con_datagram(raw_fid, d['timestamp'])
                                write_raw_datagram(raw_fid, d)
                            case 'Position':
                                t, lat, lon = parse_position(packet_info)
                                write_nmea_gps_datagram(raw_fid, t, lat, lon)
                            case 'Heading':
                                t, heading = parse_heading(packet_info)
                                write_nmea_heading_datagram(raw_fid, t, heading)
                            case 'TransducerList':
                                parse_transducerlist(packet_info)
                            case _ as unsupported:
                                print(f'Unsupported tag of {unsupported}')

                    else:
                        print(f'Unknown element found: {element}')

                    num_packets += 1

                    # remove the used bytes from the buffer
                    buffer = buffer[len(match.group(0)):]

        run_time = time.perf_counter() - start_t
        print(f'Run time {run_time:.2f} s')


def parse_multibeamping(packet, data):
    timestamp = _parse_time(packet)

    alpha = float(packet['Packet']['Calibration']['@AbsorptionCoefficient'])
    c = float(packet['Packet']['Calibration']['@SoundSpeed'])

    num_beams = int(packet['Packet']['BeamAngles']['@BeamCount'])
    # beam_spread = float(packet['Packet']['BeamAngles']['@BeamSpread'])
    beam_angle_mode = packet['Packet']['BeamAngles']['@BeamAngleMode']

    start_range = float(packet['Packet']['PingData']['@StartRange'])
    stop_range = float(packet['Packet']['PingData']['@StopRange'])
    sample_count = int(packet['Packet']['PingData']['@SampleCount'])

    sample_type = packet['Packet']['PingData']['@SamplePrecision']

    if beam_angle_mode != 'Constant':
        raise ValueError('Non constant beams are currently unsupported')

    if sample_type == 'Float':
        dt = np.single
    elif sample_type == 'Double':
        dt = np.double
    else:
        print(f'Currently unsupported binary data type: {sample_type}')

    try:
        Sv = np.frombuffer(data, dtype=dt)
        Sv = np.reshape(Sv, (num_beams, sample_count))
    except ValueError:
        # happens if not the right amount of binary data
        print(f'skipped ping at {timestamp}')

    beam_to_use = 5
    sample_interval = 2 * (stop_range - start_range) / (sample_count * c)  # [s]
    gain = 26.5  # [dB]
    transmit_power = 1000  # [W]
    pulse_length = 0.001  # [s]
    psi = 0.086  # [sr]
    sa_correction = 0.0  # [dB]
    freq = 120000  # [Hz]
    wavelength = c / freq  # [m]

    r = np.linspace(start_range, stop_range, num=sample_count)  # [m]

    # convert seapix Sv values into power [dB] using EK60 equation and then into
    # the 16 bit ints that the raw format uses
    # print(Sv.shape)
    # print(r.shape)
    power = Sv - (
        + 20*np.log10(r)
        + 2*alpha*r
        - 10*np.log(transmit_power * wavelength**2 / (16*np.pi**2))
        - 2*gain
        - 10*np.log10(c*pulse_length*psi/2)
        - 2*sa_correction
    )
    # print(power)
    power = np.rint(power * 256.0 / np.log10(2.0)).astype(int)

    # print(power)

    return {
        'timestamp': timestamp,
        'channel': 1,
        'mode': 1,
        'frequency': freq,
        'transmit_power': transmit_power,
        'pulse_length': pulse_length,
        'bandwidth': 2000,
        'sample_interval': sample_interval,  # [s]
        'sound_velocity': c,
        'absorption': alpha,
        'count': sample_count,
        'power': power[beam_to_use, :]
    }


def parse_heading(packet):
    timestamp = _parse_time(packet)
    return timestamp, float(packet['Packet']['Parameters']['@Heading'])


def parse_position(packet):
    timestamp = _parse_time(packet)
    lat = float(packet['Packet']['Parameters']['@Latitude'])
    lon = float(packet['Packet']['Parameters']['@Longitude'])
    return timestamp, lat, lon


def parse_transducerlist(packet):
    return []


def _parse_time(packet: str) -> str:
    ts = packet['Packet']['Parameters']['@Time']
    return arrow.get(datetime.strptime(ts, '%d/%m/%Y %H:%M:%S.%f'))


def __write_datagram(fid, datagram):
    """Write a EK60 datagram to file."""

    length = struct.pack('<i', len(datagram))
    fid.write(length)
    fid.write(datagram)
    fid.write(length)


def write_nmea_gps_datagram(fid, timestamp, lat, lon):

    msg = NMEAMessage(
        'GP',
        'GLL',
        GET,
        time=timestamp.time(),
        lat=lat,
        lon=lon,
        status='A',
        posMode='A'
    )

    nme_dg = Struct(
        'dg_type' / Const(b'NME0'),
        'timestamp' / Timestamp(Int64ul, 1e-7, 1601),
        'msg' / GreedyBytes
    )

    __write_datagram(fid, nme_dg.build({'timestamp': timestamp, 'msg': msg.serialize()}))


def write_nmea_heading_datagram(fid, timestamp, heading):

    msg = NMEAMessage('GP', 'HDT', GET, headingT=heading, headingTu='T')

    nme_dg = Struct(
        'dg_type' / Const(b'NME0'),
        'timestamp' / Timestamp(Int64ul, 1e-7, 1601),
        'msg' / GreedyBytes
    )

    __write_datagram(fid, nme_dg.build({'timestamp': timestamp, 'msg': msg.serialize()}))


def write_con_datagram(fid, timestamp):
    """Write a EK80 CON0 datagram."""
    con_dg = Struct(
        "dg_type" / Const(b"CON0"),
        "timestamp" / Timestamp(Int64ul, 1e-7, 1601),
        "survey_name" / PaddedString(128, 'ascii'),
        "transect_name" / PaddedString(128, 'ascii'),
        "sounder_name" / PaddedString(128, 'ascii'),
        "version" / PaddedString(30, 'ascii'),
        "spare" / Const(b' '*98),
        "transducer_count" / Int32sl,
        "config_transducer" / Array(
            this.transducer_count,
            Struct(
                "channel_id" / PaddedString(128, 'ascii'),
                "beam_type" / Int32sl,  # 0 = single, 1 = split
                "frequency" / Float32l,
                "gain" / Float32l,
                "EBA" / Float32l,
                "bw_alongship" / Float32l,
                "bw_athwartship" / Float32l,
                "angle_sens_alongship" / Default(Float32l, 0.0),
                "angle_sens_athwartship" / Default(Float32l, 0.0),
                "angle_offset_alongship" / Default(Float32l, 0.0),
                "angle_offset_athwartship" / Default(Float32l, 0.0),
                "pos_x" / Default(Float32l, 0.0),
                "pos_y" / Default(Float32l, 0.0),
                "pos_z" / Default(Float32l, 0.0),
                "dir_x" / Default(Float32l, 0.0),
                "dir_y" / Default(Float32l, 0.0),
                "dir_z" / Default(Float32l, 0.0),
                "pulselength_table" / Array(5, Float32l),
                "spare1" / Const(b' '*8),
                "gain_table" / Array(5, Float32l),
                "spare2" / Const(b' '*8),
                "sa_correction_table" / Array(5, Float32l),
                "spare3" / Const(b' '*8),
                "gpt_software_version" / PaddedString(16, 'ascii'),
                "spare4" / Const(b' '*28)
            )
        )
    )

    con_data = {}
    con_data['timestamp'] = timestamp
    con_data.update({
        'survey_name': 'the survey',
        'transect_name': 'the transect',
        'sounder_name': 'EK60',
        'version': '2.0'
        })

    con_data.update({
        'transducer_count': 1,
        'config_transducer': [{
            'channel_id': 'ABC',
            'beam_type': 0,
            'frequency': 120000,
            'gain': 26.5,
            'EBA': -20.6,
            'bw_alongship': 5.0,
            'bw_athwartship': 5.0,
            'pulselength_table': [1.0, 1.0, 1.0, 1.0, 1.0],
            'gain_table': [26.5, 26.5, 26.5, 26.5, 26.5],
            'sa_correction_table': [0.0, 0.0, 0.0, 0.0, 0.0],
            'gpt_software_version': '1.0'
        }]
    })

    __write_datagram(fid, con_dg.build(con_data))
    print('Wrote CON0 datagram')


def write_raw_datagram(fid, data):
    """Write a EK60 ping datagram."""

    raw_dg = Struct(
        "dg_type" / Const(b"RAW0"),
        "timestamp" / Timestamp(Int64ul, 1e-7, 1601),
        "channel" / Int16sl,
        "mode" / Int16sl,  # 1 for power only, 2 for angles only, 3 for both
        "transducer_depth" / Default(Float32l, 0.0),
        "transmit_power" / Float32l,
        "pulse_length" / Float32l,
        "bandwidth" / Float32l,
        "sample_interval" / Float32l,
        "sound_velocity" / Float32l,
        "absorption" / Float32l,
        "heave" / Default(Float32l, 0.0),
        "tx_roll" / Default(Float32l, 0.0),
        "tx_pitch" / Default(Float32l, 0.0),
        "temperature" / Default(Float32l, 0.0),
        "spare1" / Const(0, Int16sl),
        "spare2" / Const(0, Int16sl),
        "rx_roll" / Default(Float32l, 0.0),
        "tx_roll" / Default(Float32l, 0.0),
        "offset" / Default(Int32sl, 0),
        "count" / Int32sl,
        "power" / Array(this.count, Int16sl),
        "angle" / If(this.mode > 1, Array(this.count, Int16sl))
    )

    __write_datagram(raw_dg.build(data))
    print('rote RAW0 datagram')
