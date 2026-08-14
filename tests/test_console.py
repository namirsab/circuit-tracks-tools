from unittest import mock
from circuit_tracks.console import main


class TestListDirectory:
    @mock.patch('circuit_tracks.MidiConnection._ensure_connected')
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_output_format_text(self, midi_connect, ensure_connected):
        args = ['', 'list_directory', '--output-format', 'text']
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert ensure_connected.called

    @mock.patch('circuit_tracks.MidiConnection._ensure_connected')
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_output_format_json(self, midi_connect, ensure_connected):
        args = ['', 'list_directory', '--output-format', 'json']
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert ensure_connected.called


class TestReceiveProject:
    @mock.patch('circuit_tracks.ncs_transfer.receive_ncs_project', return_value=b"foo")
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_slot_zero(self, midi_connect, receive_ncs_project):
        args = ['', 'receive_ncs_project', '0']
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert receive_ncs_project.called

    @mock.patch('circuit_tracks.ncs_transfer.receive_ncs_project', return_value=b"foo")
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_slot_one(self, midi_connect, receive_ncs_project):
        args = ['', 'receive_ncs_project', '1']
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert receive_ncs_project.called

    @mock.patch('circuit_tracks.ncs_transfer.receive_ncs_project', return_value=b"foo")
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_slot_zero_to_file(self, midi_connect, receive_ncs_project):
        filename = '/tmp/foo'
        args = ['', 'receive_ncs_project', '0', '--output-path', filename]
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert receive_ncs_project.called
        with open(filename, 'rb') as fd:
            assert fd.read() == b'foo'


class TestSendNcsProject:
    def setUp(self):
        self.filename = '/tmp/foo.project'
        with open(self.filename, 'wb') as fd:
            fd.write(b'foo')

    @mock.patch('circuit_tracks.ncs_transfer.send_ncs_project')
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_slot_zero(self, midi_connect, send_ncs_project):
        self.setUp()
        args = ['', 'send_ncs_project', self.filename, '0']
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert send_ncs_project.called

    @mock.patch('circuit_tracks.ncs_transfer.send_ncs_project')
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_slot_one(self, midi_connect, send_ncs_project):
        self.setUp()
        args = ['', 'send_ncs_project', self.filename, '1']
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert send_ncs_project.called

    @mock.patch('circuit_tracks.ncs_transfer.send_ncs_project')
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_filename(self, midi_connect, send_ncs_project):
        self.setUp()
        args = ['', 'send_ncs_project', self.filename, '0', '--filename', 'FOO']
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert send_ncs_project.called


class TestSendPatchToSlot:
    def setUp(self):
        self.filename = '/tmp/foo.patch'
        with open(self.filename, 'wb') as fd:
            fd.write(b'foo')

    @mock.patch('circuit_tracks.ncs_transfer.send_patch_to_slot')
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_slot_zero_synth_zero(self, midi_connect, send_patch_to_slot):
        self.setUp()
        args = ['', 'send_patch_to_slot', self.filename, '0', '0']
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert send_patch_to_slot.called

    @mock.patch('circuit_tracks.ncs_transfer.send_patch_to_slot')
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_slot_zero_synth_one(self, midi_connect, send_patch_to_slot):
        self.setUp()
        args = ['', 'send_patch_to_slot', self.filename, '0', '1']
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert send_patch_to_slot.called

    @mock.patch('circuit_tracks.ncs_transfer.send_patch_to_slot')
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_slot_one_synth_zero(self, midi_connect, send_patch_to_slot):
        self.setUp()
        args = ['', 'send_patch_to_slot', self.filename, '1', '0']
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert send_patch_to_slot.called

    @mock.patch('circuit_tracks.ncs_transfer.send_patch_to_slot')
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_slot_one_synth_one(self, midi_connect, send_patch_to_slot):
        self.setUp()
        args = ['', 'send_patch_to_slot', self.filename, '1', '1']
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert send_patch_to_slot.called


class TestRequestCurrentPatch:
    @mock.patch('circuit_tracks.patch.parse_patch_data', return_value={'params': {}})
    @mock.patch('circuit_tracks.patch.request_current_patch', return_value=[])
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_synth_one(self, midi_connect, request_current_patch, parse_patch_data):
        args = ['', 'request_current_patch', '1']
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert request_current_patch.called

    @mock.patch('circuit_tracks.patch.parse_patch_data', return_value={
        'params': {},
        'raw_params_hex_first_100': [],
    })
    @mock.patch('circuit_tracks.patch.request_current_patch', return_value=[])
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_output_format_text(self, midi_connect, request_current_patch, parse_patch_data):
        args = ['', 'request_current_patch', '1', '--output-format', 'text']
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert request_current_patch.called

    @mock.patch('circuit_tracks.patch.parse_patch_data', return_value={
        'params': {},
        'raw_params_hex_first_100': [],
    })
    @mock.patch('circuit_tracks.patch.request_current_patch', return_value=[])
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_output_format_json(self, midi_connect, request_current_patch, parse_patch_data):
        args = ['', 'request_current_patch', '1', '--output-format', 'json']
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert request_current_patch.called

    @mock.patch('circuit_tracks.patch.request_current_patch', return_value=[])
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_output_format_bytes(self, midi_connect, request_current_patch):
        args = ['', 'request_current_patch', '1', '--output-format', 'bytes']
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert request_current_patch.called

    @mock.patch('circuit_tracks.patch.request_current_patch', return_value=[])
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_output_format_json_bytes(self, midi_connect, request_current_patch):
        args = ['', 'request_current_patch', '1', '--output-format', 'json-bytes']
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert request_current_patch.called

    @mock.patch('circuit_tracks.patch.request_current_patch', return_value=[])
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_output_format_sysex(self, midi_connect, request_current_patch):
        args = ['', 'request_current_patch', '1', '--output-format', 'sysex']
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert request_current_patch.called


class TestSavePatchToSlot:
    def setUp(self):
        self.filename = '/tmp/foo.patch'
        with open(self.filename, 'wb') as fd:
            fd.write(b'foo')

    @mock.patch('circuit_tracks.patch.save_patch_to_slot')
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_synth_one_slot_zero(self, midi_connect, save_patch_to_slot):
        self.setUp()
        args = ['', 'save_patch_to_slot', self.filename, '1', '0']
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert save_patch_to_slot.called

    @mock.patch('circuit_tracks.patch.save_patch_to_slot')
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_synth_two_slot_zero(self, midi_connect, save_patch_to_slot):
        self.setUp()
        args = ['', 'save_patch_to_slot', self.filename, '2', '0']
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert save_patch_to_slot.called


class TestSendSample:
    def setUp(self):
        self.filename = '/tmp/foo.wav'
        with open(self.filename, 'wb') as fd:
            fd.write(b'foo')

    @mock.patch('circuit_tracks.samples.convert_any_bytes_to_wav_48k_optimized')
    @mock.patch('circuit_tracks.samples.send_sample')
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_pack_zero_slot_zero(self, midi_connect, send_sample, convert_any_bytes_to_wav_48k_optimized):
        self.setUp()
        args = ['', 'send_sample', self.filename, '0', '0']
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert send_sample.called
        assert convert_any_bytes_to_wav_48k_optimized.called

    @mock.patch('circuit_tracks.samples.convert_any_bytes_to_wav_48k_optimized')
    @mock.patch('circuit_tracks.samples.send_sample')
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_pack_zero_slot_one(self, midi_connect, send_sample, convert_any_bytes_to_wav_48k_optimized):
        self.setUp()
        args = ['', 'send_sample', self.filename, '0', '1']
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert send_sample.called
        assert convert_any_bytes_to_wav_48k_optimized.called


class TestReceiveSample:
    @mock.patch('circuit_tracks.samples.receive_sample', return_value=b'foo')
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_pack_zero_slot_zero(self, midi_connect, receive_sample):
        args = ['', 'receive_sample', '0', '0']
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert receive_sample.called

    @mock.patch('circuit_tracks.samples.receive_sample', return_value=b'foo')
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_pack_zero_slot_one(self, midi_connect, receive_sample):
        args = ['', 'receive_sample', '0', '1']
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert receive_sample.called


class TestClearSampleSlot:
    @mock.patch('circuit_tracks.samples.clear_sample_slot')
    @mock.patch('circuit_tracks.MidiConnection.connect')
    def test_pack_zero_slot_zero(self, midi_connect, clear_sample_slot):
        args = ['', 'clear_sample_slot', '0', '0']
        with mock.patch('sys.argv', args):
            main()
        assert midi_connect.called
        assert clear_sample_slot.called
