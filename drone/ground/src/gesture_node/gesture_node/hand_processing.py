import math

import mediapipe as mp

mp_holistic = mp.solutions.holistic
mp_draw = mp.solutions.drawing_utils

# Pose landmarks used for the arm check (the person's own left and right)
LEFT_ELBOW, RIGHT_ELBOW = 13, 14
LEFT_WRIST, RIGHT_WRIST = 15, 16

# Hand landmark cheat sheet (MCP = knuckle, PIP = middle joint):
#   0 wrist
#   thumb:  2 MCP, 3 IP,  4 tip
#   index:  5 MCP, 6 PIP, 8 tip
#   middle: 9 MCP, 10 PIP, 12 tip
#   ring:   13 MCP, 14 PIP, 16 tip
#   pinky:  17 MCP, 18 PIP, 20 tip

THUMB = (2, 3, 4)
INDEX = (5, 6, 8)
MIDDLE = (9, 10, 12)
RING = (13, 14, 16)
PINKY = (17, 18, 20)

FINGERS = {
    "THUMB": THUMB,
    "INDEX": INDEX,
    "MIDDLE": MIDDLE,
    "RING": RING,
    "PINKY": PINKY,
}

KNUCKLE_LINE = (17, None, 5)  # pinky knuckle -> index knuckle, used like a finger

EXTENDED_ANGLE = 140  # degrees at the middle joint; above this the finger is extended
THUMB_OUT_RATIO = (
    0.5  # how far the thumb tip sticks out past the palm edge, in hand sizes (a guess)
)


# ---------- MediaPipe ----------
def create_holistic(static_image_mode=False):
    """False = video (tracks the body between frames), True = single unrelated images.
    model_complexity 1 ships with the mediapipe package (2 is downloaded at
    run time); 0 is faster if the frame rate drops."""
    return mp_holistic.Holistic(
        static_image_mode=static_image_mode,
        model_complexity=1,
        min_detection_confidence=0.7,
        min_tracking_confidence=0.5,
    )


def detect_gesture(holistic, rgb_image):
    """rgb_image: NumPy array, shape (height, width, 3), dtype uint8, RGB order,
    NOT mirrored: Holistic's left/right is then the person's own left/right.

    Returns (gesture, results). gesture names both hands, left first, e.g.
    "L:CLOSED_HAND+R:THUMB_INDEX_UP" or "L:NONE+R:INDEX_UP", and is None when
    neither hand is raised. results is the Holistic output, for draw_body()."""
    results = holistic.process(rgb_image)
    height, width = rgb_image.shape[:2]
    pose = results.pose_landmarks
    left = raised_hand_gesture(
        results.left_hand_landmarks, pose, LEFT_ELBOW, LEFT_WRIST, width, height
    )
    right = raised_hand_gesture(
        results.right_hand_landmarks, pose, RIGHT_ELBOW, RIGHT_WRIST, width, height
    )
    return combine_hands(left, right), results


def raised_hand_gesture(hand, pose, elbow, wrist, width, height):
    """Gesture of one hand, or None when the hand or the pose is not seen or
    the arm is down (wrist not above elbow)."""
    if hand is None or pose is None:
        return None
    if not is_arm_raised(pose.landmark, elbow, wrist):
        return None
    # Mirrored like the window (x -> width - x), so RIGHT is the pilot's right
    lm = [((1 - p.x) * width, p.y * height) for p in hand.landmark]
    return recognize(lm)


def is_arm_raised(pose, elbow, wrist):
    """True when the wrist is above the elbow (image y grows downwards)."""
    return pose[wrist].y < pose[elbow].y


def combine_hands(left, right):
    """One name for both hands, always left first so it doesn't flip between
    frames: "L:<gesture>+R:<gesture>", NONE for a hand that doesn't count.
    None when neither hand counts."""
    if left is None and right is None:
        return None
    return f"L:{left or 'NONE'}+R:{right or 'NONE'}"


def draw_body(image, results):
    """Draw the pose and both hands onto the image (in place). Parts that
    were not found are skipped."""
    mp_draw.draw_landmarks(image, results.pose_landmarks, mp_holistic.POSE_CONNECTIONS)
    for hand in (results.left_hand_landmarks, results.right_hand_landmarks):
        mp_draw.draw_landmarks(image, hand, mp_holistic.HAND_CONNECTIONS)


# ---------- Math ----------
def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def angle(a, b, c):
    """Angle in degrees at point b, between the lines b->a and b->c."""
    v1 = (a[0] - b[0], a[1] - b[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    norm = math.hypot(*v1) * math.hypot(*v2)
    if norm == 0:
        return 0
    cos = (v1[0] * v2[0] + v1[1] * v2[1]) / norm
    return math.degrees(math.acos(max(-1, min(1, cos))))


def hand_size(lm):
    return max(dist(lm[0], lm[9]), 1e-6)  # wrist to middle knuckle


def thumb_out(lm):
    """How far the thumb tip sticks out past the index knuckle, along the palm's width axis.
    Negative or near 0 = tucked, clearly positive = out."""
    ux = lm[5][0] - lm[17][0]  # axis from the pinky knuckle toward the index knuckle
    uy = lm[5][1] - lm[17][1]
    palm_width = max(math.hypot(ux, uy), 1e-6)
    along = ((lm[4][0] - lm[5][0]) * ux + (lm[4][1] - lm[5][1]) * uy) / palm_width
    return along / hand_size(lm)


def is_finger_extended(lm, finger):
    """finger is one of THUMB, INDEX, MIDDLE, RING, PINKY."""
    mcp, pip, tip = finger

    if finger == THUMB:
        return thumb_out(lm) > THUMB_OUT_RATIO

    return angle(lm[mcp], lm[pip], lm[tip]) > EXTENDED_ANGLE


def pointing_direction(lm, fingers):
    """Direction the given fingers point (knuckle to tip, added together):
    UP, DOWN, LEFT or RIGHT."""
    dx = sum(lm[tip][0] - lm[mcp][0] for mcp, pip, tip in fingers)
    dy = sum(lm[tip][1] - lm[mcp][1] for mcp, pip, tip in fingers)
    a = math.degrees(math.atan2(dx, -dy))  # 0 = up, 90 = right, -90 = left, 180 = down
    if -45 <= a < 45:
        return "UP"
    if 45 <= a < 135:
        return "RIGHT"
    if -135 <= a < -45:
        return "LEFT"
    return "DOWN"


# ---------- Gesture recognition ----------
def recognize(lm):
    # Names of the fingers that are extended, e.g. ["INDEX", "MIDDLE"]
    extended = [
        name for name, finger in FINGERS.items() if is_finger_extended(lm, finger)
    ]

    # Which way the hand points (not used when the hand is closed). The thumb
    # can't stick out at 90 degrees, so its own line leans toward the fingers:
    # it only counts as out or not. Thumb alone: the knuckle line points where
    # the thumb sticks out. Otherwise: the other extended fingers.
    if extended == ["THUMB"]:
        pointing = [KNUCKLE_LINE]
    else:
        pointing = [FINGERS[name] for name in extended if name != "THUMB"]
    direction = pointing_direction(lm, pointing)

    match extended:
        case []:
            return "CLOSED_HAND"
        case ["THUMB", "INDEX", "MIDDLE", "RING", "PINKY"]:
            return "OPEN_HAND_" + direction
        case _:
            return "_".join(extended) + "_" + direction  # e.g. "INDEX_MIDDLE_LEFT"
