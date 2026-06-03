"""
FIT file encoder for strength training activities.
Generates a valid FIT file from Hevy workout data for upload to Strava.

FIT exercise_category and exercise_name enums from the Garmin FIT SDK Profile.
Each Hevy exercise maps to (category_int, name_int) tuples.
"""

import io
import struct
import datetime

# FIT epoch starts Jan 1, 1989
FIT_EPOCH = datetime.datetime(1989, 12, 31, 0, 0, 0, tzinfo=datetime.timezone.utc)

# Exercise category enums (from FIT SDK Profile)
EXERCISE_CATEGORY = {
    "BENCH_PRESS": 0,
    "CALF_RAISE": 1,
    "CARDIO": 2,
    "CARRY": 3,
    "CHOP": 4,
    "CORE": 5,
    "CRUNCH": 6,
    "CURL": 7,
    "DEADLIFT": 8,
    "FLYE": 9,
    "HIP_RAISE": 10,
    "HIP_STABILITY": 11,
    "HIP_SWING": 12,
    "HYPEREXTENSION": 13,
    "LATERAL_RAISE": 14,
    "LEG_CURL": 15,
    "LEG_RAISE": 16,
    "LUNGE": 17,
    "OLYMPIC_LIFT": 18,
    "PLANK": 19,
    "PLYO": 20,
    "PULL_UP": 21,
    "PUSH_UP": 22,
    "ROW": 23,
    "SHOULDER_PRESS": 24,
    "SHOULDER_STABILITY": 25,
    "SHRUG": 26,
    "SIT_UP": 27,
    "SQUAT": 28,
    "TOTAL_BODY": 29,
    "TRICEPS_EXTENSION": 30,
    "WARM_UP": 31,
    "RUN": 32,
    "UNKNOWN": 65534,
}

# Exercise name enums within each category (from FIT SDK Profile)
# Format: category_name -> {name_string: int_value}
EXERCISE_NAMES = {
    "BENCH_PRESS": {
        "BARBELL_BENCH_PRESS": 0,
        "DUMBBELL_BENCH_PRESS": 1,
        "DECLINE_DUMBBELL_BENCH_PRESS": 2,
        "DECLINE_BARBELL_BENCH_PRESS": 3,
        "INCLINE_BARBELL_BENCH_PRESS": 4,
        "INCLINE_DUMBBELL_BENCH_PRESS": 5,
        "CLOSE_GRIP_BARBELL_BENCH_PRESS": 6,
        "MACHINE_BENCH_PRESS": 8,
        "SINGLE_ARM_DUMBBELL_BENCH_PRESS": 23,
    },
    "CURL": {
        "BARBELL_CURL": 0,
        "ALTERNATING_DUMBBELL_CURL": 1,
        "CABLE_CURL": 3,
        "DUMBBELL_BICEPS_CURL": 6,
        "HAMMER_CURL": 9,
        "SEATED_DUMBBELL_BICEPS_CURL": 17,
        "STANDING_CABLE_HAMMER_CURL": 23,
        "REVERSE_CABLE_CURL": 20,
    },
    "DEADLIFT": {
        "BARBELL_DEADLIFT": 0,
        "DUMBBELL_DEADLIFT": 1,
        "SINGLE_LEG_BARBELL_DEADLIFT": 2,
        "SUMO_DEADLIFT": 3,
        "ROMANIAN_HIP_HINGE": 5,
        "BARBELL_STRAIGHT_LEG_DEADLIFT": 6,
        "DUMBBELL_STRAIGHT_LEG_DEADLIFT": 7,
        "TRAP_BAR_DEADLIFT": 10,
    },
    "LATERAL_RAISE": {
        "DUMBBELL_LATERAL_RAISE": 2,
        "CABLE_LATERAL_RAISE": 0,
        "SINGLE_ARM_CABLE_LATERAL_RAISE": 7,
    },
    "LEG_CURL": {
        "LEG_CURL": 0,
        "LYING_LEG_CURL": 1,
        "SEATED_LEG_CURL": 3,
    },
    "LEG_RAISE": {
        "LEG_RAISE": 0,
        "HANGING_LEG_RAISE": 2,
        "HANGING_KNEE_RAISE": 3,
    },
    "PULL_UP": {
        "PULL_UP": 0,
        "CHIN_UP": 2,
        "WEIGHTED_PULL_UP": 11,
        "WEIGHTED_CHIN_UP": 12,
        "LAT_PULLDOWN": 13,
        "SINGLE_ARM_LAT_PULLDOWN": 14,
        "REVERSE_GRIP_LAT_PULLDOWN": 15,
    },
    "ROW": {
        "BARBELL_ROW": 0,
        "CABLE_ROW": 1,
        "BENT_OVER_ROW": 2,
        "SEATED_CABLE_ROW": 6,
        "SINGLE_ARM_DUMBBELL_ROW": 8,
    },
    "SHOULDER_PRESS": {
        "BARBELL_OVERHEAD_PRESS": 0,
        "DUMBBELL_SHOULDER_PRESS": 4,
        "MACHINE_SHOULDER_PRESS": 7,
        "SEATED_BARBELL_SHOULDER_PRESS": 10,
        "SEATED_DUMBBELL_SHOULDER_PRESS": 11,
    },
    "SQUAT": {
        "BARBELL_BACK_SQUAT": 0,
        "BARBELL_FRONT_SQUAT": 1,
        "HACK_SQUAT": 5,
        "LEG_PRESS": 9,
        "MACHINE_SQUAT": 14,
    },
    "TRICEPS_EXTENSION": {
        "CABLE_OVERHEAD_TRICEPS_EXTENSION": 1,
        "CABLE_TRICEPS_PUSHDOWN": 3,
        "DUMBBELL_TRICEPS_EXTENSION": 6,
        "MACHINE_TRICEPS_EXTENSION": 8,
        "OVERHEAD_DUMBBELL_TRICEPS_EXTENSION": 11,
        "TRICEPS_PUSHDOWN_WITH_ROPE": 14,
    },
    "CRUNCH": {
        "CRUNCH": 0,
        "DECLINE_CRUNCH": 3,
        "WEIGHTED_CRUNCH": 20,
        "WEIGHTED_DECLINE_CRUNCH": 21,
    },
    "CORE": {
        "CRUNCH": 0,
    },
}

# Hevy exercise name -> (category_key, name_key)
HEVY_TO_FIT = {
    "Bench Press (Barbell)":               ("BENCH_PRESS",       "BARBELL_BENCH_PRESS"),
    "Incline Bench Press (Barbell)":       ("BENCH_PRESS",       "INCLINE_BARBELL_BENCH_PRESS"),
    "Bent Over Row (Barbell)":             ("ROW",               "BENT_OVER_ROW"),
    "Seated Cable Row - Bar Grip":         ("ROW",               "SEATED_CABLE_ROW"),
    "Reverse Grip Lat Pulldown (Cable)":   ("PULL_UP",           "REVERSE_GRIP_LAT_PULLDOWN"),
    "Chin Up (Weighted)":                  ("PULL_UP",           "WEIGHTED_CHIN_UP"),
    "Overhead Press (Barbell)":            ("SHOULDER_PRESS",    "BARBELL_OVERHEAD_PRESS"),
    "Seated Shoulder Press (Machine)":     ("SHOULDER_PRESS",    "MACHINE_SHOULDER_PRESS"),
    "Lateral Raise (Dumbbell)":            ("LATERAL_RAISE",     "DUMBBELL_LATERAL_RAISE"),
    "Single Arm Lateral Raise (Cable)":    ("LATERAL_RAISE",     "SINGLE_ARM_CABLE_LATERAL_RAISE"),
    "Squat (Barbell)":                     ("SQUAT",             "BARBELL_BACK_SQUAT"),
    "Hack Squat (Machine)":                ("SQUAT",             "HACK_SQUAT"),
    "Leg Press (Machine)":                 ("SQUAT",             "LEG_PRESS"),
    "Romanian Deadlift (Barbell)":         ("DEADLIFT",          "ROMANIAN_HIP_HINGE"),
    "Deadlift (Trap bar)":                 ("DEADLIFT",          "TRAP_BAR_DEADLIFT"),
    "Seated Leg Curl (Machine)":           ("LEG_CURL",          "SEATED_LEG_CURL"),
    "Lying Leg Curl (Machine)":            ("LEG_CURL",          "LYING_LEG_CURL"),
    "Single Leg Extensions":               ("SQUAT",             "MACHINE_SQUAT"),
    "Leg Raise Parallel Bars":             ("LEG_RAISE",         "HANGING_LEG_RAISE"),
    "Triceps Extension (Machine)":         ("TRICEPS_EXTENSION", "MACHINE_TRICEPS_EXTENSION"),
    "Overhead Triceps Extension (Cable)":  ("TRICEPS_EXTENSION", "CABLE_OVERHEAD_TRICEPS_EXTENSION"),
    "Bicep Curl (Cable)":                  ("CURL",              "CABLE_CURL"),
    "Hammer Curl (Cable)":                 ("CURL",              "HAMMER_CURL"),
    "Behind the Back Curl (Cable)":        ("CURL",              "CABLE_CURL"),
    "Decline Crunch (Weighted)":           ("CRUNCH",            "WEIGHTED_DECLINE_CRUNCH"),
}


def to_fit_timestamp(dt):
    """Convert datetime to FIT timestamp (seconds since FIT epoch)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int((dt - FIT_EPOCH).total_seconds())


def get_exercise_enums(hevy_name):
    """Return (category_int, name_int) for a Hevy exercise name."""
    mapping = HEVY_TO_FIT.get(hevy_name)
    if not mapping:
        return EXERCISE_CATEGORY["UNKNOWN"], 65535

    cat_key, name_key = mapping
    cat_int = EXERCISE_CATEGORY.get(cat_key, EXERCISE_CATEGORY["UNKNOWN"])
    name_int = EXERCISE_NAMES.get(cat_key, {}).get(name_key, 65535)
    return cat_int, name_int


class FitEncoder:
    """
    Minimal FIT file encoder for strength training activities with set messages.
    Produces a valid FIT activity file that Strava can ingest with structured set data.
    """

    def __init__(self):
        self._data = io.BytesIO()
        self._local_message_types = {}
        self._next_local_type = 0

    def _write_field_def(self, field_num, size, base_type):
        return struct.pack("BBB", field_num, size, base_type)

    def _write_definition_message(self, local_type, global_msg_num, fields):
        """Write a definition message."""
        # Header: definition message, local type
        header = 0x40 | local_type
        # Architecture: 0 = little endian
        arch = 0
        num_fields = len(fields)
        buf = struct.pack(">BBBBHB", header, 0, arch, 0, global_msg_num, num_fields)
        # Swap to little endian for global_msg_num
        buf = bytes([header, 0, 0]) + struct.pack("<H", global_msg_num) + struct.pack("B", num_fields)
        for field_num, size, base_type in fields:
            buf += struct.pack("BBB", field_num, size, base_type)
        self._data.write(buf)
        self._local_message_types[global_msg_num] = local_type

    def _write_data_message(self, local_type, values):
        """Write a data message."""
        header = local_type & 0x0F
        self._data.write(bytes([header]))
        for value, fmt in values:
            self._data.write(struct.pack(fmt, value))

    def _compute_crc(self, data):
        """Compute FIT CRC."""
        crc_table = [
            0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
            0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
        ]
        crc = 0
        for byte in data:
            tmp = crc_table[crc & 0xF]
            crc = (crc >> 4) & 0x0FFF
            crc ^= tmp ^ crc_table[byte & 0xF]
            tmp = crc_table[crc & 0xF]
            crc = (crc >> 4) & 0x0FFF
            crc ^= tmp ^ crc_table[(byte >> 4) & 0xF]
        return crc

    def encode(self, workout_data):
        """
        Encode a workout dict into FIT bytes.

        workout_data = {
            'title': str,
            'start_time': datetime,
            'end_time': datetime,
            'exercises': [
                {
                    'name': str,  # Hevy exercise name
                    'sets': [
                        {'weight_kg': float, 'reps': int, 'set_type': str}
                    ]
                }
            ]
        }
        """
        self._data = io.BytesIO()

        start_time = workout_data['start_time']
        end_time = workout_data['end_time']
        total_elapsed = int((end_time - start_time).total_seconds())
        fit_start = to_fit_timestamp(start_time)

        # --- FILE ID message (global msg 0) ---
        # Fields: type(0/1byte), manufacturer(1/2byte), product(2/2byte), time_created(4/4byte)
        self._write_definition_message(0, 0, [
            (0, 1, 0),    # type: ENUM (base_type 0)
            (1, 2, 132),  # manufacturer: UINT16
            (2, 2, 132),  # product: UINT16
            (4, 4, 134),  # time_created: UINT32
        ])
        self._write_data_message(0, [
            (4, "<B"),      # type = activity (4)
            (255, "<H"),    # manufacturer = development (255)
            (1, "<H"),      # product = 1
            (fit_start, "<I"),  # time_created
        ])

        # --- ACTIVITY message (global msg 34) ---
        # Fields: timestamp(253/4byte), total_timer_time(0/4byte), num_sessions(1/2byte), type(2/1byte), event(3/1byte), event_type(4/1byte)
        self._write_definition_message(1, 34, [
            (253, 4, 134),  # timestamp: UINT32
            (0, 4, 134),    # total_timer_time: UINT32 (scaled *1000)
            (1, 2, 132),    # num_sessions: UINT16
            (2, 1, 0),      # type: ENUM
            (3, 1, 0),      # event: ENUM
            (4, 1, 0),      # event_type: ENUM
        ])
        fit_end = to_fit_timestamp(end_time)
        self._write_data_message(1, [
            (fit_end, "<I"),
            (total_elapsed * 1000, "<I"),
            (1, "<H"),
            (0, "<B"),  # type = manual (0)
            (26, "<B"), # event = session (26)
            (1, "<B"),  # event_type = stop (1)
        ])

        # --- SESSION message (global msg 18) ---
        self._write_definition_message(2, 18, [
            (253, 4, 134),  # timestamp: UINT32
            (2, 4, 134),    # start_time: UINT32
            (7, 4, 134),    # total_elapsed_time: UINT32 (*1000)
            (8, 4, 134),    # total_timer_time: UINT32 (*1000)
            (0, 1, 0),      # event: ENUM
            (1, 1, 0),      # event_type: ENUM
            (5, 1, 0),      # sport: ENUM (strength_training = 4)
            (6, 1, 0),      # sub_sport: ENUM (strength_training = 23)
        ])
        self._write_data_message(2, [
            (fit_end, "<I"),
            (fit_start, "<I"),
            (total_elapsed * 1000, "<I"),
            (total_elapsed * 1000, "<I"),
            (26, "<B"),  # event = session
            (1, "<B"),   # event_type = stop
            (4, "<B"),   # sport = strength_training
            (23, "<B"),  # sub_sport = strength_training
        ])

        # --- SET messages (global msg 225) ---
        # Fields: timestamp(253/4), duration(0/4 *1000), repetitions(3/2), weight(4/2 *100 kg), set_type(5/1), category(6/2), category_subtype(7/2)
        self._write_definition_message(3, 225, [
            (253, 4, 134),  # timestamp: UINT32
            (0, 4, 134),    # duration: UINT32 (*1000)
            (3, 2, 132),    # repetitions: UINT16
            (4, 2, 132),    # weight: UINT16 (*100 kg)
            (5, 1, 0),      # set_type: ENUM (0=active, 1=rest)
            (6, 2, 132),    # category: UINT16
            (7, 2, 132),    # category_subtype: UINT16
        ])

        # Distribute sets across the workout timespan
        total_sets = sum(len(ex['sets']) for ex in workout_data['exercises'])
        set_duration = max(30, total_elapsed // max(total_sets, 1))
        current_time = fit_start

        for exercise in workout_data['exercises']:
            ex_name = exercise['name']
            cat_int, name_int = get_exercise_enums(ex_name)

            for s in exercise['sets']:
                current_time += set_duration
                weight_kg = s.get('weight_kg') or 0
                weight_raw = int(float(weight_kg) * 100)
                reps = s.get('reps') or 0
                set_type = 0  # active

                self._write_data_message(3, [
                    (current_time, "<I"),
                    (set_duration * 1000, "<I"),
                    (reps, "<H"),
                    (weight_raw, "<H"),
                    (set_type, "<B"),
                    (cat_int, "<H"),
                    (name_int, "<H"),
                ])

        # Get encoded data bytes
        data_bytes = self._data.getvalue()

        # Build header
        data_size = len(data_bytes)
        header_size = 14
        header = struct.pack("<BBHI4s",
            header_size,  # header size
            0x10,         # protocol version
            0x07D9,       # profile version (21.17)
            data_size,
            b'.FIT',
        )
        header_crc = self._compute_crc(header)
        header += struct.pack("<H", header_crc)

        # Data CRC
        data_crc = self._compute_crc(data_bytes)
        data_crc_bytes = struct.pack("<H", data_crc)

        return header + data_bytes + data_crc_bytes
