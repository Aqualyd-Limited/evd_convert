import re
import string
import xml
from datetime import datetime
from pathlib import Path

import arrow
import numpy as np
import xmltodict

from .ek60_format import (
    write_con_datagram,
    write_nmea_gps_datagram,
    write_nmea_heading_datagram,
    write_raw_datagram,
)

PRINTABLE = set(string.printable.encode('ascii'))


def first_non_printable(b_str: bytes) -> int:
    """Return the index of the first non-printable byte or None if all are printable."""
    return next((i for i, b in enumerate(b_str) if b not in PRINTABLE), None)


class to_raw:

    def __init__(self):
        pass

    def convert(
        self,
        evd_file: Path,
        out_file: Path | None = None,
        last_ping_time: arrow.Arrow | None = None):

        if not out_file:
            out_file = evd_file.with_suffix('.raw')

        # Detect FileInfo and Packet XML-formatted elements
        packet_pattern = re.compile(b'(<FileInfo(.*?)/>)|(<Packet(.*?)</Packet>)', re.DOTALL)

        # some testing indicates that this size gives a short overall run time
        chunk_size = 64 * 65536
        buffer = bytearray()

        ptypes = []
        first_ping = True
        # file_info = None
        raw_datagram_count = 0
        latest_ping_time = arrow.Arrow(1970, 1, 1, 0, 0, 0)

        # 'beam_index' is an index into the sample data in the EVD file
        # 'channel_num' is the number to use in the raw file for this channel of data. These
        # are populated automatically (they need to start at 1 and increase monotonically)

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
                            try:
                                packet_info = xmltodict.parse(header.decode('ansi'))
                            except xml.parsers.expat.ExpatError:
                                print('Error parsing this XML:')
                                print(header.decode('ansi'))
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
                                    # Sometimes the first ping in a file has the same timestamp
                                    # as the last ping in the previous file and LSSS doesn't
                                    # like that, so tweak timestamps if that occurs
                                    if last_ping_time is not None and dg['timestamp'] <= last_ping_time:
                                        print('Dropped ping data due to overlapping timestamps:')
                                        print(f'  Current ping time is {dg["timestamp"]}')
                                        print(f'  Last ping in previous file was {last_ping_time}')
                                        continue

                                    latest_ping_time = max(dg['timestamp'], latest_ping_time)

                                    if first_ping:
                                        first_ping = False
                                        write_con_datagram(raw_fid, dg['timestamp'], channels_to_accept)
                                    raw_datagram_count += 1
                                    write_raw_datagram(raw_fid, dg)
                            case 'Position':
                                t, lat, lon = parse_position(packet_info)
                                write_nmea_gps_datagram(raw_fid, t, lat, lon)
                            case 'Heading':
                                t, heading = parse_heading(packet_info)
                                write_nmea_heading_datagram(raw_fid, t, heading)
                            case 'Roll':
                                t, heading = parse_roll(packet_info)
                            case 'Pitch':
                                t, heading = parse_pitch(packet_info)

                            case 'TransducerList':
                                # tx = parse_transducerlist(packet_info)
                                pass
                            case _ as unsupported:
                                print(f'Unsupported tag of {unsupported}')

                    else:
                        print(f'Unknown element found: {element}')

                    # remove the used bytes from the buffer
                    buffer = buffer[len(match.group(0)):]

        print(f'Wrote {raw_datagram_count} raw datagrams')

        return latest_ping_time


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
        raise ValueError('Non constant beam angles are currently unsupported')

    beam_angles = np.linspace(-beam_spread/2, beam_spread/2, num=num_beams)

    if sample_type == 'Float':
        dt = np.single
    elif sample_type == 'Double':
        dt = np.double
    else:
        print(f'Currently unsupported binary data type of {sample_type}')

    try:
        Sv = np.frombuffer(data, dtype=dt)
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
