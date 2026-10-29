import csv
import logging
from pathlib import Path

import cv2
import dlib
import numpy as np

from database.db_utils import init_db, get_students, record_attendance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = PROJECT_ROOT / "models" / "trained_model"
EMBEDDINGS_FILE = PROJECT_ROOT / "models" / "embeddings" / "features_all.csv"

detector = dlib.get_frontal_face_detector()
predictor = dlib.shape_predictor(
    str(MODEL_DIR / "shape_predictor_68_face_landmarks.dat"))
face_model = dlib.face_recognition_model_v1(
    str(MODEL_DIR / "dlib_face_recognition_resnet_model_v1.dat"))


def get_descriptor(img_path):
    """Return a 128-d numpy vector, or None if no face is found."""
    img = cv2.imread(str(img_path))
    if img is None:
        return None
    faces = detector(img, 1)
    if len(faces) == 0:
        logging.warning("No face in %s", img_path)
        return None
    shape = predictor(img, faces[0])
    return np.array(face_model.compute_face_descriptor(img, shape))


def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    EMBEDDINGS_FILE.parent.mkdir(parents=True, exist_ok=True)

    saved = 0
    with EMBEDDINGS_FILE.open("w", newline="") as f:
        writer = csv.writer(f)

        for s in get_students():
            if not s["face_folder"]:
                continue

            folder = PROJECT_ROOT / s["face_folder"]
            feats = []
            for img_path in sorted(folder.glob("*.jpg")):
                d = get_descriptor(img_path)
                if d is not None:
                    feats.append(d)

            if not feats:
                logging.warning("Skipping %s: no usable face images",
                                s["full_name"])
                continue

            mean = np.mean(feats, axis=0)
            writer.writerow([s["id"], *mean])   # first column = student id
            saved += 1
            logging.info("%s: %d images used", s["full_name"], len(feats))

    logging.info("Saved %d students into %s", saved, EMBEDDINGS_FILE)


if __name__ == "__main__":
    main()