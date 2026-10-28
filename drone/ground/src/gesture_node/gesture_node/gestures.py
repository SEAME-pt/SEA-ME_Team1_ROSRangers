import cv2 as opencv

from .gesture_sender import send_gesture
from .hand_processing import create_holistic, detect_gesture, draw_body

# Why the camera loop ended. gesture_node turns these into its exit code.
QUIT = "quit"  # q pressed in the window
STOPPED = "stopped"  # should_stop() returned True (Ctrl+C, SIGTERM)
NO_CAMERA = "no_camera"  # camera 0 could not be opened
CAMERA_LOST = "camera_lost"  # camera 0 stopped delivering frames


def main(should_stop=lambda: False):
    """Camera loop: one send_gesture() per frame. Returns why it ended."""
    cap = opencv.VideoCapture(0)  # /dev/video*
    if not cap.isOpened():
        cap.release()
        return NO_CAMERA
    last_gesture = None

    try:
        with create_holistic() as holistic:
            while not should_stop():
                ok, frame = cap.read()
                if not ok:
                    return CAMERA_LOST

                # Detect on the camera frame as it is: Holistic's left/right is
                # then the person's own. detect_gesture() mirrors the landmarks.
                rgb = opencv.cvtColor(frame, opencv.COLOR_BGR2RGB)
                gesture, results = detect_gesture(holistic, rgb)
                if results:
                    draw_body(frame, results)
                frame = opencv.flip(frame, 1)  # mirror view

                if gesture and gesture != last_gesture:
                    print(gesture, flush=True)  # flush: shows at once in docker logs
                    last_gesture = gesture
                send_gesture(gesture, None)

                opencv.imshow("Gestures", frame)
                if opencv.waitKey(1) & 0xFF == ord("q"):
                    return QUIT
            return STOPPED
    finally:
        cap.release()
        opencv.destroyAllWindows()


if __name__ == "__main__":
    main()
