import argparse
import json
import time
import mido

from circuit_tracks import MidiConnection, PatchBuilder
from circuit_tracks import ncs_transfer
from circuit_tracks import patch
from circuit_tracks import samples


def list_directory(midi, args):
    midi.connect(args.midi_name)
    dirs = ncs_transfer.list_directory(midi)
    if args.output_format == 'json':
        print(json.dumps(dirs, indent=2))
    else:
        for dir_ in dirs:
            print(dir_)


def receive_ncs_project(midi, args):
    midi.connect(args.midi_name)
    data = ncs_transfer.receive_ncs_project(midi, args.slot)

    output_path = args.output_path or '/dev/stdout'
    with open(output_path, 'wb') as fd:
        fd.write(data)


def send_ncs_project(midi, args):
    midi.connect(args.midi_name)
    with open(args.ncs_filepath, 'rb') as fd:
        ncs_data = fd.read()
    data = ncs_transfer.send_ncs_project(
        midi,
        ncs_data=ncs_data,
        slot=args.slot,
        filename=args.filename,
    )
    print(data)


def send_patch_to_slot(midi, args):
    midi.connect(args.midi_name)
    with open(args.patch_filepath, 'rb') as fd:
        patch_data = fd.read()
    ncs_transfer.send_patch_to_slot(
        midi,
        patch_bytes=patch_data,
        synth=args.synth,
        slot=args.slot,
    )


def request_current_patch(midi, args):
    midi.connect(args.midi_name)
    raw_data = patch.request_current_patch(midi, args.synth)
    if raw_data is None:
        print('Empty response from Circuit Tracks')
        return

    if args.output_format == 'bytes':
        file_mode = 'wb'
        file_data = bytearray(raw_data[8:])

    elif args.output_format == 'sysex':
        file_mode = 'wb'
        file_data = bytearray([0xF0] + raw_data + [0xF7])

    elif args.output_format == 'text':
        file_data, file_mode = '', 'w'
        data = patch.parse_patch_data(raw_data)
        params = data.pop('params')
        data.pop('raw_params_hex_first_100')
        for key, value in data.items():
            file_data += f"{key}: {value}\n"
        for key, value in params.items():
            file_data += f"  {key}: {value}\n"
    elif args.output_format == 'json':
        data = patch.parse_patch_data(raw_data)
        data.pop('raw_params_hex_first_100')
        file_mode = 'w'
        file_data = json.dumps(data)
    elif args.output_format == 'json-bytes':
        file_mode = 'w'
        file_data = json.dumps(raw_data)
    else:
        print("Invalid output format")
        return

    output_path = args.output_path or '/dev/stdout'
    with open(output_path, file_mode) as fd:
        fd.write(file_data)


def send_current_patch(midi, args):
    midi.connect(args.midi_name)
    with open(args.patch_filepath, 'rb') as fd:
        patch_bytes = list(fd.read())
    data = patch.send_current_patch(
        midi,
        synth=args.synth,
        patch_bytes=patch_bytes
    )
    print(data)


def save_patch_to_slot(midi, args):
    midi.connect(args.midi_name)
    with open(args.patch_filepath, 'rb') as fd:
        patch_bytes = list(fd.read())
    patch.save_patch_to_slot(
        midi,
        synth=args.synth,
        slot=args.slot,
        patch_bytes=patch_bytes
    )


def send_sample(midi, args):
    midi.connect(args.midi_name)
    with open(args.sample_filepath, 'rb') as fd:
        sample_bytes = fd.read()
    sample_bytes = samples.convert_any_bytes_to_wav_48k_optimized(sample_bytes)
    data = samples.send_sample(
        midi,
        sample_data=sample_bytes,
        pack=args.pack,
        slot=args.slot,
        filename=args.filename,
    )
    print(data)


def receive_sample(midi, args):
    midi.connect(args.midi_name)
    sample_data = samples.receive_sample(
        midi,
        pack=args.pack,
        slot=args.slot,
    )

    output_path = args.output_path or '/dev/stdout'
    with open(output_path, 'wb') as fd:
        fd.write(sample_data)


def clear_sample_slot(midi, args):
    midi.connect(args.midi_name)
    data = samples.clear_sample_slot(
        midi,
        pack=args.pack,
        slot=args.slot,
    )
    print(data)


ACTIONS = {
    'test': 0,
    # 'start_handshake': 0,
    # 'build_preset': build_preset,
    'list_directory': list_directory,
    'receive_ncs_project': receive_ncs_project,
    'send_ncs_project': send_ncs_project,
    'send_patch_to_slot': send_patch_to_slot,
    'request_current_patch': request_current_patch,
    'send_current_patch': send_current_patch,
    'save_patch_to_slot': save_patch_to_slot,
    'send_sample': send_sample,
    'receive_sample': receive_sample,
    'clear_sample_slot': clear_sample_slot,
}


class MidiSyncContext:
    def __init__(self, midi, bpm):
        """
        Context manager to resume MIDI playback with a Song Position Pointer (SPP) offset
        after a blocking operation (like a sample upload).
        """
        self.port = midi
        self.bpm = bpm
        self.start_time = None

    def __enter__(self):
        # Record the exact timestamp before the upload starts
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            # 1. Calculate elapsed time during the block
            elapsed_time = time.time() - self.start_time

            # 2. Calculate how many 16th notes passed
            # Formula: (BPM / 60) gives beats per second.
            # Multiplying by 4 gives 16th notes per second.
            beats_per_second = self.bpm / 60.0
            sixteenth_notes_per_second = beats_per_second * 4
            missed_sixteenth_notes = elapsed_time * sixteenth_notes_per_second

            # SPP uses a resolution of 16th notes. We round it to the nearest integer.
            spp_position = min(16383, round(missed_sixteenth_notes))

            # Send the messages to the Circuit Tracks
            self.port.send_song_position(spp_position)
            self.port.send_realtime('continue')

            print(f"[MIDI Sync] Upload took {elapsed_time:.2f}s.")
            print(f"[MIDI Sync] Sent SPP position: {spp_position} (16th notes offset) followed by CONTINUE.")

        finally:
            pass


class MidiSyncBurstContext:
    def __init__(self, midi, bpm):
        """
        Context manager to resume MIDI playback by bursting missed MIDI clocks
        after a blocking operation (like a sample upload).
        """
        self.port = midi
        self.bpm = bpm
        self.start_time = None

    def __enter__(self):
        # Record the exact timestamp before the upload/freeze starts
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        time.sleep(1)
        try:
            # 1. Calculate elapsed time during the block
            elapsed_time = time.time() - self.start_time

            # 2. Calculate missed MIDI clocks
            # MIDI standard dictates 24 clocks per quarter note (beat).
            beats_per_second = self.bpm / 60.0
            clocks_per_second = beats_per_second * 24
            missed_clocks = round(elapsed_time * clocks_per_second)

            # 3. Execute the recovery sequence
            # First, tell the Circuit to resume from its paused state
            self.port.send_realtime('continue')

            # Second, flood it with the missed clocks to force it to catch up
            for _ in range(missed_clocks):
                self.port.send_clock()

            print(f"[MIDI Burst] Upload took {elapsed_time:.2f}s.")
            print(f"[MIDI Burst] Sent CONTINUE followed by a burst of {missed_clocks} MIDI clocks.")

        finally:
            pass


def main():
    parser = argparse.ArgumentParser(
        prog='Circuit Tracks',
    )
    parser.add_argument(
        '-m', '--midi-name',
        default='Circuit Tracks MIDI',
    )
    parser.add_argument(
        '-r', '--resume',
        action="store_true",
        help="""Run a MIDI continue after operation with clock delay.
        Useful for blocking operation. Only work with external clock"""
    )
    parser.add_argument(
        '-b', '--resume-bpm',
        type=int,
        default=120,
    )

    subparsers = parser.add_subparsers(dest="action", required=True)

    # List directory
    list_directory_parser = subparsers.add_parser("list_directory", help="")
    list_directory_parser.add_argument('--output-format', default='json', choices=('text', 'json'))
    # Receive project
    receive_ncs_project_parser = subparsers.add_parser("receive_ncs_project", help="")
    receive_ncs_project_parser.add_argument('slot', type=int)
    receive_ncs_project_parser.add_argument('-o', '--output-path')
    # Send project
    send_ncs_project_parser = subparsers.add_parser("send_ncs_project", help="")
    send_ncs_project_parser.add_argument('ncs_filepath')
    send_ncs_project_parser.add_argument('slot', type=int)
    send_ncs_project_parser.add_argument('-f', '--filename', required=False)
    # send_ncs_project_parser.add_argument('-r', '--# to-ram', action='store_true')
    # Send patch to slot
    send_patch_to_slot_parser = subparsers.add_parser("send_patch_to_slot", help="")
    send_patch_to_slot_parser.add_argument('patch_filepath')
    send_patch_to_slot_parser.add_argument('slot', type=int)
    send_patch_to_slot_parser.add_argument('synth', type=int)
    # Request current patch
    request_current_patch_parser = subparsers.add_parser("request_current_patch", help="")
    request_current_patch_parser.add_argument('synth', type=int, choices=(1, 2))
    request_current_patch_parser.add_argument('-o', '--output-path')
    request_current_patch_parser.add_argument('--output-format', default='sysex', choices=('text', 'json', 'bytes', 'json-bytes', 'sysex'))
    # Send current patch
    send_current_patch_parser = subparsers.add_parser("send_current_patch", help="")
    send_current_patch_parser.add_argument('patch_filepath')
    send_current_patch_parser.add_argument('synth', type=int, choices=(1, 2))
    # Save patch to slot
    save_patch_to_slot_parser = subparsers.add_parser("save_patch_to_slot", help="")
    save_patch_to_slot_parser.add_argument('patch_filepath')
    save_patch_to_slot_parser.add_argument('synth', type=int, choices=(1, 2))
    save_patch_to_slot_parser.add_argument('slot', type=int)
    # Send sample
    send_sample_parser = subparsers.add_parser("send_sample", help="")
    send_sample_parser.add_argument('sample_filepath')
    send_sample_parser.add_argument('pack', type=int)
    send_sample_parser.add_argument('slot', type=int)
    send_sample_parser.add_argument('--filename')
    # Receive sample
    receive_sample_parser = subparsers.add_parser("receive_sample", help="")
    receive_sample_parser.add_argument('pack', type=int)
    receive_sample_parser.add_argument('slot', type=int)
    receive_sample_parser.add_argument('-o', '--output-path')
    # Clear sample slot
    clear_sample_slot_parser = subparsers.add_parser("clear_sample_slot", help="")
    clear_sample_slot_parser.add_argument('pack', type=int)
    clear_sample_slot_parser.add_argument('slot', type=int)

    args = parser.parse_args()
    midi = MidiConnection()

    func = ACTIONS[args.action]

    if args.resume:
        with MidiSyncContext(midi=midi, bpm=args.resume_bpm):
            func(midi, args)
    else:
        func(midi, args)


if __name__ == '__main__':
    main()
