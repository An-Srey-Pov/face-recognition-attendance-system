"""
Real-time face recognition attendance application (smooth-video version).

Performance design:
- The camera is read in its own thread (always the newest frame).
- The display loop does ONLY: read frame -> draw cached boxes -> show.
  It never waits for detection or recognition.
- Detection + recognition run in a separate worker thread.
- Faces are tracked between cycles, so the heavy face-descriptor step is
  skipped for people who were already identified a moment ago.
- Attendance (database + voice) runs in a background worker.

Voice: a short fixed message is spoken (no student name).
"""

import csv
import logging
import platform
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import dlib
import numpy as np

from attendance.voice_service import VoiceService
from database.db_utils import (
    get_students,
    init_db,
    record_attendance,
)


# ------------------------------------
# PROJECT PATHS
# ------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]

MODEL_DIR = PROJECT_ROOT / "models" / "trained_model"

EMBEDDINGS_FILE = (
    PROJECT_ROOT / "models" / "embeddings" / "features_all.csv"
)


# ------------------------------------
# CONFIGURATION
# ------------------------------------

# Smaller threshold means stricter face matching.
THRESHOLD = 0.4

CAMERA_INDEX = 0
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480
CAMERA_FPS = 30

WINDOW_NAME = "Face Attendance"

# --- Voice messages (no student name) ---
CHECK_IN_MESSAGE = "Welcome"
CHECK_OUT_MESSAGE = "Goodbye"

# --- Performance settings (tune these) ---

# Detect faces on a smaller image. Lower = faster, but may miss far faces.
DETECT_SCALE = 0.5

# Recognize at most this many faces per cycle (largest first).
MAX_FACES = 3

# Pause between recognition cycles. This leaves CPU time for the video
# display. Raise it (0.12-0.2) if the video is still choppy.
PROCESS_INTERVAL = 0.08

# A face that was identified is trusted for this long before the heavy
# descriptor is computed again for it.
REVERIFY_SECONDS = 2.0

# Keep drawing the last boxes for this long if detection briefly misses,
# which stops boxes from flickering.
HOLD_SECONDS = 0.4

# Minimum overlap (IoU) to treat a new box as the same tracked face.
TRACK_IOU = 0.3

# A student must be out of view this long before attendance can be
# attempted again.
ABSENCE_SECONDS = 5.0


# ------------------------------------
# REQUIRED DLIB MODEL FILES
# ------------------------------------

SHAPE_PREDICTOR_FILE = (
    MODEL_DIR / "shape_predictor_68_face_landmarks.dat"
)

FACE_RECOGNITION_MODEL_FILE = (
    MODEL_DIR / "dlib_face_recognition_resnet_model_v1.dat"
)


def validate_model_files():
    """Confirm that the required Dlib model files exist."""

    if not SHAPE_PREDICTOR_FILE.exists():
        raise FileNotFoundError(
            "Shape predictor model was not found:\n"
            f"{SHAPE_PREDICTOR_FILE}"
        )

    if not FACE_RECOGNITION_MODEL_FILE.exists():
        raise FileNotFoundError(
            "Face recognition model was not found:\n"
            f"{FACE_RECOGNITION_MODEL_FILE}"
        )


validate_model_files()


# ------------------------------------
# DLIB INITIALIZATION
# ------------------------------------

detector = dlib.get_frontal_face_detector()

predictor = dlib.shape_predictor(str(SHAPE_PREDICTOR_FILE))

face_recognition_model = dlib.face_recognition_model_v1(
    str(FACE_RECOGNITION_MODEL_FILE)
)

# Create missing database tables if necessary.
init_db()


# ------------------------------------
# HELPERS
# ------------------------------------

def iou(box_a, box_b):
    """Intersection-over-union of two (left, top, right, bottom) boxes."""

    x1 = max(box_a[0], box_b[0])
    y1 = max(box_a[1], box_b[1])
    x2 = min(box_a[2], box_b[2])
    y2 = min(box_a[3], box_b[3])

    inter = max(0, x2 - x1) * max(0, y2 - y1)

    if inter == 0:
        return 0.0

    area_a = (box_a[2] - box_a[0]) * (box_a[3] - box_a[1])
    area_b = (box_b[2] - box_b[0]) * (box_b[3] - box_b[1])

    return inter / float(area_a + area_b - inter)


# ------------------------------------
# THREADED CAMERA READER
# ------------------------------------

class CameraStream:
    """
    Reads frames continuously in a background thread and keeps only
    the newest one, so slow processing never causes buffered frames.
    """

    def __init__(self, capture):
        self.capture = capture
        self.frame = None
        self.running = True
        self.failed = False

        self.lock = threading.Lock()
        self.new_frame = threading.Event()

        self.thread = threading.Thread(target=self._update, daemon=True)
        self.thread.start()

    def _update(self):
        while self.running:
            success, frame = self.capture.read()

            if not success or frame is None:
                self.failed = True
                self.new_frame.set()
                break

            with self.lock:
                self.frame = frame

            self.new_frame.set()

    def read(self, timeout=1.0):
        """Return the newest unseen frame, or None on timeout/failure."""

        if not self.new_frame.wait(timeout):
            return None

        self.new_frame.clear()

        if self.failed:
            return None

        with self.lock:
            return self.frame

    def stop(self):
        self.running = False
        self.thread.join(timeout=2.0)


# ------------------------------------
# FACE RECOGNIZER
# ------------------------------------

class FaceRecognizer:
    def __init__(self):
        self.font = cv2.FONT_HERSHEY_SIMPLEX

        self.voice = VoiceService(
            rate=165,
            volume=1.0,
        )

        # Attendance runs here so nothing waits on the database.
        # NOTE: if db_utils keeps one module-level SQLite connection
        # created in the main thread, open the connection per call
        # (or use check_same_thread=False).
        self.attendance_executor = ThreadPoolExecutor(max_workers=1)

        # FPS information.
        self.frame_start_time = time.time()
        self.fps = 0.0
        self.fps_show = 0.0
        self.last_fps_update = time.time()
        self.frame_count = 0

        # Registered face information.
        self.known_ids = []
        self.known_features = np.empty((0, 128), dtype=np.float64)
        self.student_names = {}

        # Results shared between the worker and the display loop.
        self.result_lock = threading.Lock()
        self.cached_faces = []      # list of (l, t, r, b)
        self.cached_results = []    # list of {"student_id", "name"}
        self.current_frame_face_count = 0
        self.last_face_time = 0.0

        # Worker-thread-only state.
        self.tracks = []            # tracked faces between cycles
        self.last_seen = {}         # student_id -> last time seen

        # Frame hand-off to the worker.
        self.frame_lock = threading.Lock()
        self.pending_frame = None
        self.frame_event = threading.Event()
        self.worker_running = False
        self.worker_thread = None

    # ========================================================
    # LOAD FACE DATABASE
    # ========================================================

    def get_face_database(self):
        """
        Load registered student names and face embeddings.

        Expected CSV format:
            student_id, feature_1, feature_2, ..., feature_128
        """

        students = get_students()

        self.student_names = {
            student["id"]: student["full_name"]
            for student in students
        }

        if not self.student_names:
            logging.error("No students were found in the database.")
            return False

        if not EMBEDDINGS_FILE.exists():
            logging.error(
                "Face embeddings file was not found: %s",
                EMBEDDINGS_FILE,
            )
            logging.info(
                "Register student faces, then run: "
                "python -m face_recognition.train_model"
            )
            return False

        known_ids = []
        known_features = []

        try:
            with EMBEDDINGS_FILE.open(
                mode="r",
                newline="",
                encoding="utf-8",
            ) as csv_file:
                reader = csv.reader(csv_file)

                for row_number, row in enumerate(reader, start=1):
                    if len(row) != 129:
                        logging.warning(
                            "Skipping embeddings row %s: "
                            "expected 129 columns but found %s.",
                            row_number,
                            len(row),
                        )
                        continue

                    try:
                        student_id = int(row[0])
                        feature_vector = [float(v) for v in row[1:]]
                    except ValueError:
                        logging.warning(
                            "Skipping invalid embeddings row %s.",
                            row_number,
                        )
                        continue

                    if student_id not in self.student_names:
                        logging.warning(
                            "Skipping student ID %s because the "
                            "student does not exist in the database.",
                            student_id,
                        )
                        continue

                    known_ids.append(student_id)
                    known_features.append(feature_vector)

        except OSError as error:
            logging.error("Could not read the embeddings file: %s", error)
            return False

        if not known_ids:
            logging.error("No valid face embeddings were found.")
            return False

        self.known_ids = known_ids
        self.known_features = np.asarray(known_features, dtype=np.float64)

        logging.info(
            "Loaded %s registered face embedding(s).",
            len(self.known_ids),
        )
        logging.info(
            "Loaded %s student record(s).",
            len(self.student_names),
        )

        return True

    # ========================================================
    # FPS CALCULATION
    # ========================================================

    def update_fps(self):
        current_time = time.time()
        frame_time = current_time - self.frame_start_time

        if frame_time > 0:
            self.fps = 0.9 * self.fps + 0.1 * (1.0 / frame_time)

        if current_time - self.last_fps_update >= 1.0:
            self.fps_show = self.fps
            self.last_fps_update = current_time

        self.frame_start_time = current_time

    # ========================================================
    # CAMERA INFORMATION
    # ========================================================

    def draw_note(self, frame):
        cv2.putText(
            frame,
            "Face Attendance Management System",
            (20, 40),
            self.font,
            0.75,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

        for y, text in (
            (90, f"Frame: {self.frame_count}"),
            (120, f"FPS: {self.fps_show:.1f}"),
            (150, f"Faces: {self.current_frame_face_count}"),
        ):
            cv2.putText(
                frame,
                text,
                (20, y),
                self.font,
                0.65,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )

        cv2.putText(
            frame,
            "Press Q to quit",
            (20, CAMERA_HEIGHT - 20),
            self.font,
            0.65,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

    # ========================================================
    # VOICE (no student name)
    # ========================================================

    def announce(self, record_type, status):
        """
        Speak a short fixed message without the student's name.

        Uses VoiceService.speak(text) when it exists. Otherwise falls
        back to welcome()/goodbye() with an empty name.
        """

        if record_type == "check-in":
            text = CHECK_IN_MESSAGE
        else:
            text = CHECK_OUT_MESSAGE

        speak = getattr(self.voice, "speak", None)

        if callable(speak):
            return speak(text) is not False

        if record_type == "check-in":
            return self.voice.welcome("", status=status)

        return self.voice.goodbye("", status=status)

    # ========================================================
    # ATTENDANCE (runs in background worker)
    # ========================================================

    def attendance(self, student_id):
        """Record attendance and play a short voice message."""

        name = self.student_names.get(student_id, f"Student {student_id}")

        try:
            result = record_attendance(student_id)
        except Exception:
            logging.exception("Could not record attendance for %s.", name)
            return

        if result is None:
            logging.info(
                "%s already has the applicable attendance record for today.",
                name,
            )
            return

        record_type, status, attendance_time = result

        logging.info(
            "%s: %s at %s, status: %s",
            name,
            record_type,
            attendance_time,
            status,
        )

        if record_type not in ("check-in", "check-out"):
            return

        try:
            queued = self.announce(record_type, status)
        except Exception:
            logging.exception("Voice message failed for %s.", name)
            return

        if queued:
            logging.info("Voice message queued for %s.", name)
        else:
            logging.warning(
                "Voice message could not be queued for %s.",
                name,
            )

    # ========================================================
    # RECOGNIZE ONE FACE (heavy)
    # ========================================================

    def recognize_face(self, rgb_frame, face):
        """
        Recognize one detected face (face in full-frame coordinates).

        Returns (student_id, display_name) or (None, "Unknown").
        """

        shape = predictor(rgb_frame, face)

        descriptor = np.asarray(
            face_recognition_model.compute_face_descriptor(
                rgb_frame,
                shape,
            ),
            dtype=np.float64,
        )

        if len(self.known_features) == 0:
            return None, "Unknown"

        distances = np.linalg.norm(
            self.known_features - descriptor,
            axis=1,
        )

        nearest_index = int(np.argmin(distances))
        nearest_distance = float(distances[nearest_index])

        if nearest_distance < THRESHOLD:
            student_id = self.known_ids[nearest_index]
            name = self.student_names.get(
                student_id,
                f"Student {student_id}",
            )
            return student_id, name

        return None, "Unknown"

    # ========================================================
    # DETECT + RECOGNIZE (runs in the worker thread)
    # ========================================================

    def process_frame(self, frame):
        now = time.time()

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        if DETECT_SCALE != 1.0:
            small = cv2.resize(
                rgb_frame,
                (0, 0),
                fx=DETECT_SCALE,
                fy=DETECT_SCALE,
                interpolation=cv2.INTER_AREA,
            )
        else:
            small = rgb_frame

        small_faces = detector(small, 0)

        inverse = 1.0 / DETECT_SCALE

        faces = [
            dlib.rectangle(
                int(f.left() * inverse),
                int(f.top() * inverse),
                int(f.right() * inverse),
                int(f.bottom() * inverse),
            )
            for f in small_faces
        ]

        faces.sort(key=lambda f: f.width() * f.height(), reverse=True)
        faces = faces[:MAX_FACES]

        old_tracks = self.tracks
        used = set()
        new_tracks = []

        for face in faces:
            box = (face.left(), face.top(), face.right(), face.bottom())

            # Find the best matching tracked face from the last cycle.
            best_index = None
            best_iou = TRACK_IOU

            for index, track in enumerate(old_tracks):
                if index in used:
                    continue

                overlap = iou(box, track["box"])

                if overlap > best_iou:
                    best_iou = overlap
                    best_index = index

            reused = False

            if best_index is not None:
                used.add(best_index)
                track = old_tracks[best_index]

                # Trust a recently identified face: skip the heavy step.
                if (
                    track["student_id"] is not None
                    and now - track["verified_at"] < REVERIFY_SECONDS
                ):
                    student_id = track["student_id"]
                    name = track["name"]
                    verified_at = track["verified_at"]
                    reused = True

            if not reused:
                student_id, name = self.recognize_face(rgb_frame, face)
                verified_at = now

            new_tracks.append(
                {
                    "box": box,
                    "student_id": student_id,
                    "name": name,
                    "verified_at": verified_at,
                }
            )

            if student_id is None:
                continue

            last = self.last_seen.get(student_id)

            if last is None or now - last > ABSENCE_SECONDS:
                logging.info("Recognized student: %s", name)
                self.attendance_executor.submit(
                    self.attendance,
                    student_id,
                )

            self.last_seen[student_id] = now

        self.tracks = new_tracks

        boxes = [t["box"] for t in new_tracks]
        results = [
            {"student_id": t["student_id"], "name": t["name"]}
            for t in new_tracks
        ]

        with self.result_lock:
            if boxes:
                self.cached_faces = boxes
                self.cached_results = results
                self.current_frame_face_count = len(boxes)
                self.last_face_time = now

            elif now - self.last_face_time > HOLD_SECONDS:
                # Nobody visible for a while: clear the display.
                self.cached_faces = []
                self.cached_results = []
                self.current_frame_face_count = 0

    # ========================================================
    # WORKER THREAD
    # ========================================================

    def worker_loop(self):
        while self.worker_running:
            if not self.frame_event.wait(0.5):
                continue

            self.frame_event.clear()

            with self.frame_lock:
                frame = self.pending_frame
                self.pending_frame = None

            if frame is None:
                continue

            try:
                self.process_frame(frame)
            except Exception:
                logging.exception("Recognition cycle failed.")

            # Leave CPU time for the video display.
            time.sleep(PROCESS_INTERVAL)

    def submit_frame(self, frame):
        """Give the worker the newest frame if it has none waiting."""

        if self.pending_frame is not None:
            return

        with self.frame_lock:
            # Copy, because the display loop draws on its own frame.
            self.pending_frame = frame.copy()

        self.frame_event.set()

    # ========================================================
    # DRAW FACE RESULTS (fast)
    # ========================================================

    def draw_face_results(self, frame):
        frame_height, frame_width = frame.shape[:2]

        with self.result_lock:
            faces = list(self.cached_faces)
            results = list(self.cached_results)

        for (left, top, right, bottom), result in zip(faces, results):
            left = max(0, left)
            top = max(0, top)
            right = min(frame_width - 1, right)
            bottom = min(frame_height - 1, bottom)

            color = (
                (0, 255, 0)
                if result["student_id"] is not None
                else (0, 0, 255)
            )

            cv2.rectangle(frame, (left, top), (right, bottom), color, 2)

            cv2.putText(
                frame,
                result["name"],
                (left, max(top - 10, 20)),
                self.font,
                0.6,
                color,
                2,
                cv2.LINE_AA,
            )

    # ========================================================
    # CREATE CAMERA
    # ========================================================

    def create_camera(self):
        """
        DirectShow on Windows; default backend on Linux / Raspberry Pi.
        """

        if platform.system() == "Windows":
            capture = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)

            if capture.isOpened():
                return capture

            capture.release()

        return cv2.VideoCapture(CAMERA_INDEX)

    # ========================================================
    # MAIN CAMERA LOOP
    # ========================================================

    def run(self):
        capture = None
        stream = None

        try:
            if not self.get_face_database():
                logging.error(
                    "Face recognition cannot start because "
                    "the face database is unavailable."
                )
                return

            if not self.voice.engine_available:
                logging.warning(
                    "Voice service is unavailable. Face recognition and "
                    "attendance will continue without sound."
                )

            capture = self.create_camera()

            if not capture.isOpened():
                raise RuntimeError(
                    f"Could not open camera index {CAMERA_INDEX}."
                )

            # MJPG gives much higher frame rates on most USB webcams.
            capture.set(
                cv2.CAP_PROP_FOURCC,
                cv2.VideoWriter_fourcc(*"MJPG"),
            )
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
            capture.set(cv2.CAP_PROP_FPS, CAMERA_FPS)

            # Keep the driver buffer small to avoid delayed frames.
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            stream = CameraStream(capture)

            self.worker_running = True
            self.worker_thread = threading.Thread(
                target=self.worker_loop,
                daemon=True,
            )
            self.worker_thread.start()

            cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)

            logging.info("Camera started successfully.")
            logging.info("Press Q to quit.")

            while True:
                frame = stream.read()

                if frame is None:
                    logging.error("Could not read a frame from the camera.")
                    break

                if (
                    frame.shape[1] != CAMERA_WIDTH
                    or frame.shape[0] != CAMERA_HEIGHT
                ):
                    frame = cv2.resize(frame, (CAMERA_WIDTH, CAMERA_HEIGHT))

                # Mirror the camera preview.
                frame = cv2.flip(frame, 1)

                # Hand the frame to the worker (never blocks).
                self.submit_frame(frame)

                # Draw the latest known results and show immediately.
                self.draw_face_results(frame)

                self.frame_count += 1
                self.update_fps()
                self.draw_note(frame)

                cv2.imshow(WINDOW_NAME, frame)

                key = cv2.waitKey(1) & 0xFF

                if key == ord("q"):
                    logging.info("Quit command received.")
                    break

                if cv2.getWindowProperty(
                    WINDOW_NAME,
                    cv2.WND_PROP_VISIBLE,
                ) < 1:
                    logging.info("Camera window was closed.")
                    break

        except KeyboardInterrupt:
            logging.info("Attendance recognition was stopped.")

        except Exception:
            logging.exception(
                "Unexpected error in the face recognition application."
            )

        finally:
            self.worker_running = False
            self.frame_event.set()

            if self.worker_thread is not None:
                self.worker_thread.join(timeout=3.0)

            if stream is not None:
                stream.stop()

            if capture is not None:
                capture.release()

            cv2.destroyAllWindows()

            # Finish pending attendance records, then queued voice.
            self.attendance_executor.shutdown(wait=True)
            self.voice.wait_until_finished()
            self.voice.close()

            logging.info("Camera and voice resources released.")


# ------------------------------------
# APPLICATION ENTRY POINT
# ------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    recognizer = FaceRecognizer()
    recognizer.run()


if __name__ == "__main__":
    main()