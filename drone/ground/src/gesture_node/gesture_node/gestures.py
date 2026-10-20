import cv2 as opencv
from .hand_processing import create_hands, detect_gesture, draw_hand
from .gesture_sender import send_gesture

def main():
    cap = opencv.VideoCapture(0)  # /dev/video*
    last_gesture = None

    with create_hands() as hands:
        while cap.isOpened():
            ok, frame = cap.read()
            if not ok:
                break

            frame = opencv.flip(frame, 1)  # mirror view
            rgb = opencv.cvtColor(frame, opencv.COLOR_BGR2RGB)

            gesture, hand = detect_gesture(hands, rgb)
            if hand:
                draw_hand(frame, hand)

            if gesture and gesture != last_gesture:
                print(gesture)
                last_gesture = gesture
            send_gesture(gesture, None)

            opencv.imshow("Gestures", frame)
            if opencv.waitKey(1) & 0xFF == ord("q"):
                break

    cap.release()
    opencv.destroyAllWindows()


if __name__ == "__main__":
    main()
