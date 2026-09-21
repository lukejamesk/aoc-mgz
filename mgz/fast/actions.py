"""Action parsing for DE >= 71094."""
import io
import struct

from mgz.util import unpack, as_hex
from mgz.fast.enums import Action


def parse_action_71094(action_type, player_id, raw):
    data = io.BytesIO(raw)
    payload = {}
    if action_type is Action.RESIGN:
        unpack('<b', data)
    if action_type is Action.RESEARCH:
        # RESEARCH is a 13-byte header (`<Ihh5x`: object_id, selected,
        # technology_id, then 5 bytes observed as `ff ff ff ff 00`),
        # optionally followed by `selected` building ids. Which one applies is
        # derived from the payload length, not the player type:
        #   - 13 + 4 * selected: player-issued research. Every RESEARCH in the
        #     66.6, 67.2 and 68.0 fixtures issued by a human has this layout
        #     (lengths 17/21/25/33/37), and object_id repeats the last id in
        #     the trailing list.
        #   - 13 with selected >= 1: AI-issued research. All 14 AI RESEARCH
        #     actions in the 68.0 vs-AI fixture are exactly 13 bytes with
        #     selected=1 and no id list (e.g. `e4 0f 00 00 01 00 65 00 ff ff
        #     ff ff 00` = building 4068 researching Feudal Age, tech 101).
        #     Reading `selected` ids here used to raise struct.error, which
        #     action() downgraded to Action.ERROR, dropping the AI's techs.
        # Any other length raises ValueError (not caught by action()) so a new
        # layout surfaces as a real failure instead of being swallowed.
        selected = struct.unpack_from('<h', raw, 4)[0]
        if selected < 0:
            raise ValueError(f"RESEARCH: negative selected count {selected}")
        if len(raw) == 13 + 4 * selected:
            has_id_list = True
        elif len(raw) == 13:
            has_id_list = False
        else:
            raise ValueError(
                f"RESEARCH: unexpected payload length {len(raw)} for selected={selected} "
                f"(expected 13 or {13 + 4 * selected})"
            )
        object_id, _selected, technology_id = unpack('<Ihh5x', data)
        if has_id_list:
            unpack(f'<{selected}I', data, shorten=False)
        payload = dict(technology_id=technology_id, object_ids=[object_id])
    if action_type is Action.GAME:
        command_id = unpack('<h', data)
        payload = dict(command_id=command_id)
        if command_id == 0:
            source_player, target_player, mode_float, mode = unpack('<2xhhfb', data)
            payload.update(dict(target_player_id=target_player, diplomacy_mode=mode))
        elif command_id == 1:
            payload['speed'] = unpack('<6xf', data)
        elif command_id in [13, 14, 17, 18]:
            payload['number'] = unpack('<4xh', data)
    if action_type is Action.DE_QUEUE:
        # DE_QUEUE's fixed header width is derived from the payload's own
        # length rather than hardcoded, because the two header widths that
        # have ever been proposed for this action disagree by exactly the 4
        # reserved/padding bytes before the object_ids array:
        #   - 16 bytes (`<h4xhhh4x`): the layout actually observed, with zero
        #     exceptions, in an exhaustive replay of every DE_QUEUE action
        #     (1052 total) across all three known save versions (66.6, 67.2,
        #     68.0).
        #   - 12 bytes (`<h4xhhh`): PR #146's claim. It has never been
        #     observed in any fixture we've checked; assuming it silently
        #     misreads the 16-byte header's own reserved field as the first
        #     object id, yielding small counter-like values (e.g. 1) instead
        #     of a real object id (e.g. 3090).
        # Rather than hardcode either width, or silently guess when a
        # payload matches neither, we compute the two candidate lengths from
        # `selected` (the object-id count, read from the payload's own first
        # field) and require the payload to match one of them exactly. A
        # payload matching neither -- which would previously either mis-
        # decode (if long enough to satisfy the 12-byte guess) or raise
        # struct.error that mgz/fast/__init__.py's action() silently
        # downgrades to Action.ERROR (if too short) -- now raises ValueError
        # instead, so it's surfaced to callers as a real parse failure
        # rather than swallowed or silently wrong. ValueError is deliberate:
        # neither action() nor operation() in mgz/fast/__init__.py catches
        # anything but struct.error, so this propagates out of parse_match.
        selected = struct.unpack_from('<h', raw)[0]
        if selected < 0:
            raise ValueError(f"DE_QUEUE: negative selected count {selected}")
        padded_len = 16 + 4 * selected
        unpadded_len = 12 + 4 * selected
        if len(raw) == padded_len:
            header_fmt = '<h4xhhh4x'
        elif len(raw) == unpadded_len:
            header_fmt = '<h4xhhh'
        else:
            raise ValueError(
                f"DE_QUEUE: unexpected payload length {len(raw)} for selected={selected} "
                f"(expected {unpadded_len} or {padded_len})"
            )
        _selected, building_type, unit_id, amount = unpack(header_fmt, data)
        object_ids = list(unpack(f'<{selected}I', data, shorten=False))
        payload = dict(object_ids=object_ids, amount=amount, unit_id=unit_id)
    if action_type is Action.MOVE:
        x, y, selected = unpack('<4x2fh', data)
        object_ids = []
        data.read(6)
        if selected > 0:
            object_ids = list(unpack(f'<{selected}I', data, shorten=False))
        payload = dict(object_ids=object_ids, x=x, y=y)
    if action_type is Action.ORDER:
        target_id, x, y, selected = unpack('<I2fh', data)
        object_ids = []
        data.read(6)
        if selected > 0:
            object_ids = list(unpack(f'<{selected}I', data, shorten=False))
        payload = dict(object_ids=object_ids, target_id=target_id, x=x, y=y)
    if action_type is Action.WORK:
        # WORK (aka "ai_interact" in the legacy body parser, mgz/body/actions.py's
        # `ai_interact` struct) has never had a parser here and fell through to the
        # generic `{"sequence": N}` payload. Its layout was derived empirically from
        # every WORK action (63,513) in the September 2026 vs-AI fixture -- the only
        # known fixture that contains any: WORK is emitted exclusively by the
        # AI-controlled player (type 3) there, at roughly the game's tick rate
        # (~40ms between repeats for the same unit), while human-issued equivalents
        # go through ORDER instead. It is byte-for-byte the same shape as ORDER
        # (Action.ORDER, above): `target_id u32, x f32, y f32, selected i16`, 6
        # reserved bytes (observed constant as `00 00 01 00 00 00` across every
        # instance -- always the same value, so left unnamed/unread rather than
        # guessed at), then `selected` object ids. Every one of the 63,513 payloads
        # is exactly 24 bytes = 14 (header) + 6 (reserved) + 4*1, with selected == 1
        # in all of them.
        #
        # Validation against the same fixture:
        #  - object_ids (the tasked unit): 81/88 distinct ids also appear as
        #    object ids in the same player's BUILD/DE_QUEUE/ORDER/MAKE actions;
        #    4087 and 4089 additionally resolve to that player's starting Villagers
        #    via the header's object list. None are 0 or 0xffffffff.
        #  - target_id: the top target ids by frequency resolve, via the header's
        #    gaia object list, to Gold Mine / Stone Mine / Bush / Tree instances --
        #    i.e. WORK's target is the resource (or other object) being worked, and
        #    targets cluster heavily (the top 10 of 155 distinct targets account for
        #    more than half of all WORK actions) rather than being uniform.
        #  - x/y fall within the match's 120x120 map on every sample checked.
        #
        # Cross-checked against aoe2rec's `patterns/aoe2operations.hexpat` (action 2,
        # there called `AiInteract`). It reads the same 20-byte header as
        # `s32 target_id, float x, float y, s32 unknown_count_1, s32 unknown_1`, i.e.
        # it takes the object-id count to be a 32-bit field at offset 12 where this
        # reads a 16-bit `selected` at the same offset. The two readings agree on every
        # payload in the fixture (the count's high half is 0 in all 63,513, and the
        # following s32 is a constant 1), and both put the count at offset 12 -- which
        # is also where ORDER, the action this shares its shape with, carries `selected`.
        # They could only diverge if the real count lived in the *second* s32 instead.
        #
        # So the payload length is validated rather than trusted: 20 bytes of header
        # plus exactly 4 * selected bytes of object ids. `unpack` (mgz.util) reads from
        # a BytesIO and silently ignores trailing bytes, so without this check a payload
        # whose count sat in the other field would decode to a plausible-looking but
        # wrong object-id list instead of failing. ValueError is deliberate and matches
        # RESEARCH/DE_QUEUE above: neither action() nor operation() in mgz/fast/__init__.py
        # catches anything but struct.error, so a new layout surfaces as a real failure
        # rather than being swallowed into Action.ERROR or decoded wrongly.
        selected = struct.unpack_from('<h', raw, 12)[0]
        if selected < 0:
            raise ValueError(f"WORK: negative selected count {selected}")
        expected_len = 20 + 4 * selected
        if len(raw) != expected_len:
            raise ValueError(
                f"WORK: unexpected payload length {len(raw)} for selected={selected} "
                f"(expected {expected_len})"
            )
        target_id, x, y, _selected = unpack('<I2fh', data)
        object_ids = []
        data.read(6)
        if selected > 0:
            object_ids = list(unpack(f'<{selected}I', data, shorten=False))
        payload = dict(object_ids=object_ids, target_id=target_id, x=x, y=y)
    if action_type is Action.BUILD:
        selected, x, y, building_id, unk2, unk3, unk4 = unpack('<h2xffI8xhbb', data)
        object_ids = list(unpack(f'{selected}I', data, shorten=False))
        payload = dict(building_id=building_id, object_ids=object_ids, x=x, y=y)
    if action_type is Action.GATHER_POINT:
        selected, x, y, target_id, target_type = unpack('<h2xffiix', data)
        object_ids = list(unpack(f'{selected}I', data, shorten=False))
        payload = dict(target_id=target_id, target_type=target_type, x=x, y=y, object_ids=object_ids)
    if action_type is Action.DE_MULTI_GATHERPOINT:
        target_id, x, y = unpack('<iff', data) # This is a best guess. There is other unknown data in the payload.
        payload = dict(target_id=target_id, x=x, y=y)
    if action_type is Action.STANCE:
        selected, stance_id = unpack('<II', data)
        object_ids = list(unpack(f'{selected}I', data, shorten=False))
        payload = dict(stance_id=stance_id, object_ids=object_ids)
    if action_type is Action.SPECIAL:
        selected, target_id, x, y, slot_id, order_id = unpack('<Iiff4xh2xh3x', data)
        object_ids = list(unpack(f'{selected}I', data, shorten=False))
        payload = dict(order_id=order_id, slot_id=slot_id, target_id=target_id, x=x, y=y, object_ids=object_ids)
    if action_type is Action.FORMATION:
        selected, formation_id = unpack('<II', data)
        object_ids = list(unpack(f'{selected}I', data, shorten=False))
        payload = dict(formation_id=formation_id, object_ids=object_ids)
    if action_type in [Action.BUY, Action.SELL]:
        resource_id, amount, object_id = unpack('<hhI', data)
        payload = dict(resource_id=resource_id, amount=amount, object_ids=[object_id])
    if action_type is Action.DE_TRANSFORM:
        # autoscout enable?
        object_id, y = unpack('<II', data)
        payload = dict(object_ids=[object_id])
    if action_type is Action.AI_ORDER:
        # used for autoscout moves
        # 01 00 00 00 75 06 00 00 ff ff ff ff 21 03 00 00 00 00 30 42 00 00 a0 42 00 00 00 00 00 00 80 3f 64 ff 01 00
        a, object_id, c, x, y = unpack('<II4xIff', data)
        payload = dict(object_ids=[object_id], x=x, y=y)
    if action_type in [Action.BACK_TO_WORK, Action.DELETE]:
        object_id = unpack('<I', data)
        payload = dict(object_ids=[object_id])
    if action_type is Action.WALL:
        selected, x1, y1, x2, y2, building_id = unpack('<IHHHHI', data)
        data.read(8)
        object_ids = list(unpack(f'{selected}I', data, shorten=False))
        payload = dict(object_ids=object_ids, x=x1, y=y1, x_end=x2, y_end=y2, building_id=building_id)
    if action_type in [Action.PATROL, Action.DE_ATTACK_MOVE]:
        selected, x, y = unpack('<I4xf36xf36x', data)
        object_ids = list(unpack(f'{selected}I', data, shorten=False))
        payload = dict(object_ids=object_ids, x=x, y=y)
    if action_type is Action.UNGARRISON:
        selected, x, y, target_id, unk = unpack('<IffiI', data)
        object_ids = list(unpack(f'{selected}I', data, shorten=False))
        payload = dict(object_ids=object_ids, x=x, y=y, target_id=target_id)
    if action_type is Action.FLARE:
        x, y, num = unpack('<4xffb', data)
        targets = list(unpack(f'<{num}b', data, shorten=False))
        payload = dict(x=x, y=y, targets=targets)
    if action_type is Action.TOWN_BELL:
        building_id, mode = unpack('<Ib',data)
        payload = dict(building_id=building_id, mode=mode)
    if action_type is Action.STOP:
        selected = unpack('<I', data)
        object_ids = list(unpack(f'{selected}I', data, shorten=False))
        payload = dict(object_ids=object_ids)
    if action_type in [Action.FOLLOW, Action.GUARD]:
        selected, target_id = unpack('<II', data)
        object_ids = list(unpack(f'{selected}I', data, shorten=False))
        payload = dict(object_ids=object_ids, target_id=target_id)
    if action_type is Action.ATTACK_GROUND:
        selected, x, y = unpack('<Iff', data)
        data.read(4)
        object_ids = list(unpack(f'{selected}I', data, shorten=False))
        payload = dict(object_ids=object_ids, x=x, y=y)
    if action_type is Action.REPAIR:
        selected, target_id = unpack('<II', data)
        data.read(4)
        object_ids = list(unpack(f'{selected}I', data, shorten=False))
        payload = dict(object_ids=object_ids, target_id=target_id)
    if action_type is Action.DE_TRIBUTE:
        wood, food, gold, stone = unpack('<ffff', data)
        data.read(16) # cost[4]
        data.read(8) # attribute id[4]
        target_id = data.read(1)
        payload = dict(target_player_id=target_id, food=food, wood=wood, stone=stone, gold=gold)
    if action_type in [Action.GATE, Action.DROP_RELIC]:
        object_id = unpack('<I', data)
        payload = dict(object_ids=[object_id])
    if action_type in [Action.DE_AUTOSCOUT, Action.RATHA_ABILITY]:
        selected = unpack('<I', data)
        object_ids = list(unpack(f'{selected}I', data, shorten=False))
        payload = dict(object_ids=object_ids)
    if action_type is Action.MAKE:
        # MAKE is aoe2rec's "AiQueue" (action 100): three 32-bit fields --
        #   s32 building_id (the producer's object *instance* id, not a building type),
        #   s32 unknown1 (observed sentinel -1),
        #   s32 unit_type_id.
        # See aoe2ct/aoe2rec `patterns/aoe2operations.hexpat` (struct AiQueue) and the raw
        # bytes in the September 2026 vs-AI fixture:
        #   e4 0f 00 00 ff ff ff ff 53 00 00 00  = producer 4068, -1, unit 83 (Villager).
        # The previous `<H6xh` read truncated any instance id >= 65536 and took only the low
        # half of the unit type field.
        building_id, unit_id = unpack('<I4xi', data)
        payload = dict(building_id=building_id, unit_id=unit_id)
    return dict(player_id=player_id, **payload)
