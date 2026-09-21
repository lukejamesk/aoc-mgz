import unittest
from mgz.fast.actions import parse_action_71094
from mgz.fast.enums import Action
from mgz.fast.header import parse
from mgz.util import Version

class TestFastUserPatch15(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open('tests/recs/small.mgz', 'rb') as handle:
            cls.data = parse(handle)

    def test_version(self):
        self.assertEqual(self.data['version'], Version.USERPATCH15)

    def test_players(self):
        players = self.data.get('players')
        self.assertEqual(len(players), 3)
        self.assertEqual(players[0]['diplomacy'], [1, 4, 4, -1, -1, -1, -1, -1, -1])
        self.assertEqual(players[1]['diplomacy'], [0, 1, 4, -1, -1, -1, -1, -1, -1])
        self.assertEqual(players[2]['diplomacy'], [0, 4, 1, -1, -1, -1, -1, -1, -1])

    def test_map(self):
        self.assertEqual(self.data['scenario']['map_id'], 44)


class TestFastDE(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open('tests/recs/de-13.34.aoe2record', 'rb') as handle:
            cls.data = parse(handle)

    def test_version(self):
        self.assertEqual(self.data['version'], Version.DE)

    def test_players(self):
        players = self.data.get('players')
        self.assertEqual(len(players), 3)

    def test_map(self):
        self.assertEqual(self.data['scenario']['map_id'], 9)
        self.assertEqual(self.data['lobby']['seed'], -1970180596)


class TestFastDEScenario(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open('tests/recs/de-50.6-scenario.aoe2record', 'rb') as handle:
            cls.data = parse(handle)

    def test_version(self):
        self.assertEqual(self.data['version'], Version.DE)

    def test_players(self):
        players = self.data.get('players')
        self.assertEqual(len(players), 3)


class TestFastDEScenarioWithTriggers(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open('tests/recs/de-50.6-scenario-with-triggers.aoe2record', 'rb') as handle:
            cls.data = parse(handle)

    def test_version(self):
        self.assertEqual(self.data['version'], Version.DE)

    def test_players(self):
        players = self.data.get('players')
        self.assertEqual(len(players), 3)


class TestFastHD(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open('tests/recs/hd-5.8.aoe2record', 'rb') as handle:
            cls.data = parse(handle)

    def test_version(self):
        self.assertEqual(self.data['version'], Version.HD)

    def test_players(self):
        players = self.data.get('players')
        self.assertEqual(len(players), 7)

    def test_map(self):
        self.assertEqual(self.data['scenario']['map_id'], 0)


class TestFastActionWork(unittest.TestCase):
    """Action.WORK (id 2), aka "ai_interact" in the legacy body parser.

    No committed fixture in this repo happens to contain a WORK action (it is
    emitted only by the AI-controlled player, and none of the AI games in
    tests/recs generate one before ending). These payloads are copied
    byte-for-byte from real WORK actions observed in a save-68.0 vs-AI DE
    replay (see mgz/fast/actions.py for the full derivation/validation), so
    the test exercises the real wire format rather than a fixture file.
    """

    def test_villager_tasked_to_gold_mine(self):
        # player 2 tasking villager (object id 5180) to gold mine (object id 4186)
        raw = bytes.fromhex('5a1000000000d34200005a4201000000010000003c140000')
        payload = parse_action_71094(Action.WORK, 2, raw)
        self.assertEqual(payload['target_id'], 4186)
        self.assertAlmostEqual(payload['x'], 105.5)
        self.assertAlmostEqual(payload['y'], 54.5)
        self.assertEqual(payload['object_ids'], [5180])
        self.assertEqual(payload['player_id'], 2)

    def test_villager_tasked_to_another_object(self):
        # player 2 tasking villager (object id 4087, resolved via the header's
        # starting-object list) to object id 4318 (built or spawned after the
        # header snapshot, so it can't be named from the header alone)
        raw = bytes.fromhex('de1000008745bd42937686420100000001000000f70f0000')
        payload = parse_action_71094(Action.WORK, 2, raw)
        self.assertEqual(payload['target_id'], 4318)
        self.assertAlmostEqual(payload['x'], 94.63579559326172)
        self.assertAlmostEqual(payload['y'], 67.2315902709961)
        self.assertEqual(payload['object_ids'], [4087])

    def test_exact_consumption(self):
        # 14-byte header + 6 reserved bytes + 4 * selected object ids, exactly.
        raw = bytes.fromhex('5a1000000000d34200005a4201000000010000003c140000')
        self.assertEqual(len(raw), 24)
        parse_action_71094(Action.WORK, 2, raw)  # must not raise

    def test_unexpected_length_raises(self):
        # unpack() (mgz.util) reads from a BytesIO, so trailing bytes are silently
        # ignored rather than raising -- the payload length is what catches a payload
        # that does not match the layout. aoe2rec's hexpat reads the object-id count as
        # a 32-bit field at offset 12 where this reads a 16-bit one; a payload whose
        # count really lived in the following s32 would otherwise decode to a wrong
        # object-id list instead of failing. See mgz/fast/actions.py.
        raw = bytes.fromhex('5a1000000000d34200005a4201000000010000003c140000')
        self.assertRaises(ValueError, parse_action_71094, Action.WORK, 2, raw + b'\x00\x00\x00\x00')
        self.assertRaises(ValueError, parse_action_71094, Action.WORK, 2, raw[:-4])

    def test_negative_selected_raises(self):
        # selected is read as a signed 16-bit field; a negative count is not a length.
        raw = bytes.fromhex('5a1000000000d34200005a42ffff0000010000003c140000')
        self.assertRaises(ValueError, parse_action_71094, Action.WORK, 2, raw)

    def test_multiple_selected_object_ids(self):
        # Every observed WORK action has selected == 1; this constructs a
        # synthetic payload (same header, 2 trailing ids instead of 1) to
        # confirm the array is read using the same `selected`-driven formula
        # as Action.ORDER, rather than being hardcoded to one id.
        header = bytes.fromhex('5a1000000000d34200005a4202000000010000003c140000')[:20]
        ids = (5180).to_bytes(4, 'little') + (5181).to_bytes(4, 'little')
        raw = header + ids
        payload = parse_action_71094(Action.WORK, 2, raw)
        self.assertEqual(payload['object_ids'], [5180, 5181])


class TestFastActionSpecial(unittest.TestCase):
    """Raw Unqueue payloads from each AgeLens replay generation."""

    def test_unqueue_object_ids_start_after_the_29_byte_header(self):
        cases = (
            (
                'December 2025 save 66.6',
                2,
                '01000000ffffffff0000000000000000ffffffff0000000004000001000b0c0000',
                0,
                3083,
            ),
            (
                'February 2026 save 67.2',
                2,
                '01000000ffffffff0000000000000000ffffffff010000000400000100500b0000',
                1,
                2896,
            ),
            (
                'September 2026 save 68.0',
                1,
                '01000000ffffffff0000000000000000ffffffff030000000400000100dd0f0000',
                3,
                4061,
            ),
        )

        for generation, player_id, raw_hex, slot_id, object_id in cases:
            with self.subTest(generation=generation):
                payload = parse_action_71094(Action.SPECIAL, player_id, bytes.fromhex(raw_hex))
                self.assertEqual(
                    payload,
                    {
                        'player_id': player_id,
                        'order_id': 4,
                        'slot_id': slot_id,
                        'target_id': -1,
                        'x': 0.0,
                        'y': 0.0,
                        'object_ids': [object_id],
                    },
                )
