import struct

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

nme_dg = Struct(
    'dg_type' / Const(b'NME0'),
    'timestamp' / Timestamp(Int64ul, 1e-7, 1601),
    'msg' / GreedyBytes
)

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

    __write_datagram(fid, nme_dg.build({'timestamp': timestamp, 'msg': msg.serialize()}))

def write_nmea_heading_datagram(fid, timestamp, heading):

    msg = NMEAMessage('GP', 'HDT', GET, headingT=heading, headingTu='T')

    __write_datagram(fid, nme_dg.build({'timestamp': timestamp, 'msg': msg.serialize()}))

def write_con_datagram(fid, timestamp, channel_config):
    """Write a EK80 CON0 datagram."""

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

    __write_datagram(fid, raw_dg.build(data))
