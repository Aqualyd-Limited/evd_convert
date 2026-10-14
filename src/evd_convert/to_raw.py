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

    def convert(self, evd_file: Path, out_file: Path | None = None):

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
        # file_info = None
        datagram_count = 0

        # 'beam_index' is an index into the sample data in the EVD file
        # 'channel_num' is the number to use in the raw file for this channel of data. These
        # are populated automatically (they need to start at 1 and increase monotonically)
        channels_to_accept = {
            'Sv pings STT': [
                {'beam_index': 5, 'channel_num': None},
                {'beam_index': 128, 'channel_num': None},
            ],
            'Sv pings SAT': [
                {'beam_index': 5, 'channel_num': None},
            ],
            # 'Sv pings Double RX STV',
            # 'Sv pings Double RX SAV',
        }

        channels_to_accept = {
            'Sv pings STV': [
                {'beam_index': 64, 'channel_num': None},
                {'beam_index': 128, 'channel_num': None},
                {'beam_index': 192, 'channel_num': None},
            ],
            'Sv pings Double RX STV': [
                {'beam_index': 64, 'channel_num': None},
                {'beam_index': 128, 'channel_num': None},
                {'beam_index': 192, 'channel_num': None},
            ],
        }


        # populate the channel_num value in the channels_to_accept dict
        i = 1
        for config in channels_to_accept.values():
            for c in config:
                c['channel_num'] = i
                i += 1

        # Now read in the EVD file and progressively write out the raw file.
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
                        # file_info = xmltodict.parse(element.decode('ansi'))
                        pass
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
                                dgs = parse_multibeamping(
                                    packet_info,
                                    binary_data,
                                    channels_to_accept
                                )

                                for dg in dgs:
                                    if first_ping:
                                        first_ping = False
                                        datagram_count += 1
                                        write_con_datagram(raw_fid, dg['timestamp'], channels_to_accept)

                                    datagram_count += 1
                                    write_raw_datagram(raw_fid, dg)
                            case 'Position':
                                t, lat, lon = parse_position(packet_info)
                                datagram_count += 1
                                write_nmea_gps_datagram(raw_fid, t, lat, lon)
                            case 'Heading':
                                t, heading = parse_heading(packet_info)
                                datagram_count += 1
                                write_nmea_heading_datagram(raw_fid, t, heading)
                            case 'Roll':
                                t, heading = parse_roll(packet_info)
                                datagram_count += 1
                            case 'Pitch':
                                t, heading = parse_pitch(packet_info)
                                datagram_count += 1

                            case 'TransducerList':
                                # tx = parse_transducerlist(packet_info)
                                pass
                            case _ as unsupported:
                                print(f'Unsupported tag of {unsupported}')

                    else:
                        print(f'Unknown element found: {element}')

                    num_packets += 1

                    # remove the used bytes from the buffer
                    buffer = buffer[len(match.group(0)):]

        # run_time = time.perf_counter() - start_t
        print(f'Wrote {datagram_count} datagrams')
        # print(f'Run time {run_time:.2f} s')


def parse_multibeamping(packet: dict, data, channel_config: dict):
    timestamp = _parse_time(packet)

    alpha = float(packet['Packet']['Calibration']['@AbsorptionCoefficient'])
    c = float(packet['Packet']['Calibration']['@SoundSpeed'])

    num_beams = int(packet['Packet']['BeamAngles']['@BeamCount'])
    beam_spread = float(packet['Packet']['BeamAngles']['@BeamSpread'])
    beam_angle_mode = packet['Packet']['BeamAngles']['@BeamAngleMode']

    start_range = float(packet['Packet']['PingData']['@StartRange'])
    stop_range = float(packet['Packet']['PingData']['@StopRange'])
    sample_count = int(packet['Packet']['PingData']['@SampleCount'])

    sample_type = packet['Packet']['PingData']['@SamplePrecision']
    data_type = packet['Packet']['PingData']['@StorageDataType']

    # transducer = packet['Packet']['Parameters']['@Transducer']
    # channel = packet['Packet']['Parameters']['@Channel']
    source = packet['Packet']['Parameters']['@Source']

    if data_type != 'Sv':
        return []

    if source not in channel_config:
        return []

    if beam_angle_mode != 'Constant':
        raise ValueError('Non constant beams are currently unsupported')

    beam_angles = np.linspace(-beam_spread/2, beam_spread/2, num=num_beams)

    if sample_type == 'Float':
        dt = np.single
    elif sample_type == 'Double':
        dt = np.double
    else:
        print(f'Currently unsupported binary data type: {sample_type}')

    try:
        Sv = np.frombuffer(data, dtype=dt)
        # Sv = np.reshape(Sv, (num_beams, sample_count))
        Sv = np.reshape(Sv, (sample_count, num_beams)).T
    except ValueError:
        # happens if not the right amount of binary data
        print(f'skipped ping at {timestamp}')
        return []

    sample_interval = 2 * (stop_range - start_range) / (sample_count * c)  # [s]
    gain = 10**(26.5/10)  # [dB]
    transmit_power = 1000  # [W]
    pulse_length = 0.001024  # [s]
    psi = 0.0087  # [sr]
    sa_correction = 0.0  # [dB]
    freq = 150000  # [Hz]
    wavelength = c / freq  # [m]

    r = np.linspace(start_range, stop_range, num=sample_count)  # [m]

    # convert seapix Sv values into power [dB] using EK60 equation and then into
    # the 16 bit ints that the raw format uses
    power = Sv - (
        + 20*np.log10(r)
        + 2*alpha*r
        - 10*np.log(transmit_power * gain**2 * wavelength**2 / (16*np.pi**2))
        - 10*np.log10(c*pulse_length*psi/2)
        - 2*sa_correction
    )
    power = np.rint(power * 256.0 / (10 * np.log10(2.0))).astype(int)

    raw_datagrams = []
    for beam in channel_config[source]:
        raw_datagrams.append({
            'timestamp': timestamp,
            'channel': beam['channel_num'],
            'beam_angles': beam_angles,
            'mode': 1,
            'frequency': freq,
            'transmit_power': transmit_power,
            'pulse_length': pulse_length,
            'bandwidth': 2000,
            'sample_interval': sample_interval,  # [s]
            'sound_velocity': c,
            'absorption': alpha,
            'count': sample_count,
            'power': power[beam['beam_index'], :],
            'angle': []
        })

    return raw_datagrams

def parse_heading(packet):
    timestamp = _parse_time(packet)
    return timestamp, float(packet['Packet']['Parameters']['@Heading'])


def parse_roll(packet):
    timestamp = _parse_time(packet)
    return timestamp, float(packet['Packet']['Parameters']['@Roll'])


def parse_pitch(packet):
    timestamp = _parse_time(packet)
    return timestamp, float(packet['Packet']['Parameters']['@Pitch'])


def parse_position(packet):
    timestamp = _parse_time(packet)
    lat = float(packet['Packet']['Parameters']['@Latitude'])
    lon = float(packet['Packet']['Parameters']['@Longitude'])
    return timestamp, lat, lon


def parse_transducerlist(packet):
    return {t['@ID']: t['@Sounder'] for t in packet['Packet']['Transducer']}


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


def write_con_datagram(fid, timestamp, channel_config):
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
                "channel_name" / PaddedString(128, 'ascii'),
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

    num_channels = 0
    for config in channel_config.values():
        num_channels += len(config)

    con_data['transducer_count'] = num_channels

    configs = []
    for name, config in channel_config.items():
        for beam in config:
            configs.append({
                'channel_name': f'{name} (beam {beam["beam_index"]})',
                'beam_type': 0,
                'frequency':150000,
                'gain': 26.5,
                'EBA': -20.6,
                'bw_alongship': 5.0,
                'bw_athwartship': 5.0,
                'pulselength_table': [0.256, 0.512, 0.1024, 0.2048, 0.4096],
                'gain_table': [26.5, 26.5, 26.5, 26.5, 26.5],
                'sa_correction_table': [0.0, 0.0, 0.0, 0.0, 0.0],
                'gpt_software_version': '1.0'
            })

    con_data['config_transducer'] = configs

    __write_datagram(fid, con_dg.build(con_data))


def write_raw_datagram(fid, data):
    """Write a EK60 ping datagram."""

    raw_dg = Struct(
        "dg_type" / Const(b"RAW0"),
        "timestamp" / Timestamp(Int64ul, 1e-7, 1601),
        "channel" / Int16sl,
        "mode" / Int16sl,  # 1 for power only, 2 for angles only, 3 for both
        "transducer_depth" / Default(Float32l, 0.0),
        "frequency" / Float32l,
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

    __write_datagram(fid, raw_dg.build(data))
