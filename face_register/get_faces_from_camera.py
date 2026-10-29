import logging
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox

import cv2
import dlib
from PIL import Image, ImageTk

from database.db_utils import init_db, get_students, set_face_folder

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STUDENTS_DIR = PROJECT_ROOT / "dataset" / "students"
TARGET_PHOTOS = 15


class FaceRegister:
    def __init__(self):
        init_db()
        STUDENTS_DIR.mkdir(parents=True, exist_ok=True)

        self.detector = dlib.get_frontal_face_detector()
        self.cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)

        self.student = None
        self.students = []
        self.frame = None   # latest RGB frame
        self.faces = []

        self.win = tk.Tk()
        self.win.title("Face Register")
        self.win.geometry("1050x520")
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        self.video = tk.Label(self.win)
        self.video.pack(side=tk.LEFT, padx=10, pady=10)

        panel = tk.Frame(self.win)
        panel.pack(side=tk.LEFT, padx=20, pady=10, anchor="n")

        tk.Label(panel, text="Face Register",
                 font=("Helvetica", 20, "bold")).pack(anchor="w", pady=10)

        tk.Label(panel, text="Select student:").pack(anchor="w")
        self.combo = ttk.Combobox(panel, state="readonly", width=38)
        self.combo.pack(anchor="w", pady=4)
        self.combo.bind("<<ComboboxSelected>>", self.on_select)

        tk.Button(panel, text="Refresh student list",
                  command=self.refresh_students).pack(anchor="w", pady=4)

        self.lbl_faces = tk.Label(panel, text="Faces in frame: 0")
        self.lbl_faces.pack(anchor="w", pady=(15, 2))

        self.lbl_count = tk.Label(panel, text="Select a student first")
        self.lbl_count.pack(anchor="w", pady=2)

        tk.Button(panel, text="Save current face", width=22,
                  command=self.save_face).pack(anchor="w", pady=(15, 4))
        tk.Button(panel, text="Clear this student's photos", width=22,
                  command=self.clear_photos).pack(anchor="w", pady=4)

        self.lbl_log = tk.Label(panel, text="", fg="blue",
                                wraplength=320, justify="left")
        self.lbl_log.pack(anchor="w", pady=15)

        self.refresh_students()

    # ---------------- students ----------------
    def refresh_students(self):
        self.students = get_students()
        self.combo["values"] = [
            f"{s['student_code']} - {s['full_name']}" for s in self.students
        ]

    def on_select(self, _event=None):
        idx = self.combo.current()
        if idx < 0:
            return
        self.student = self.students[idx]
        folder = STUDENTS_DIR / str(self.student["id"])
        folder.mkdir(parents=True, exist_ok=True)
        set_face_folder(self.student["id"],
                        f"dataset/students/{self.student['id']}")
        self.update_count()
        self.lbl_log["text"] = f"Ready: {self.student['full_name']}"

    def folder(self):
        return STUDENTS_DIR / str(self.student["id"])

    def update_count(self):
        n = len(list(self.folder().glob("*.jpg")))
        self.lbl_count["text"] = f"Photos saved: {n} (aim for {TARGET_PHOTOS}+)"

    # ---------------- actions ----------------
    def save_face(self):
        if self.student is None:
            self.lbl_log["text"] = "Select a student first!"
            return
        if len(self.faces) != 1:
            self.lbl_log["text"] = "Need exactly one face in the frame!"
            return

        d = self.faces[0]
        h, w = self.frame.shape[:2]
        mx, my = d.width() // 2, d.height() // 2
        x1, y1 = max(d.left() - mx, 0), max(d.top() - my, 0)
        x2, y2 = min(d.right() + mx, w), min(d.bottom() + my, h)

        crop = cv2.cvtColor(self.frame[y1:y2, x1:x2], cv2.COLOR_RGB2BGR)
        path = self.folder() / f"img_{int(time.time() * 1000)}.jpg"
        cv2.imwrite(str(path), crop)

        self.update_count()
        self.lbl_log["text"] = f"Saved {path.name}. Change angle slightly."
        logging.info("Saved %s", path)

    def clear_photos(self):
        if self.student is None:
            return
        if not messagebox.askyesno(
            "Confirm",
            f"Delete all photos of {self.student['full_name']}?"
        ):
            return
        for f in self.folder().glob("*.jpg"):
            f.unlink()
        self.update_count()
        self.lbl_log["text"] = "Photos cleared. Retrain the model."

    # ---------------- camera loop ----------------
    def update(self):
        ok, frame = self.cap.read()
        if ok and frame is not None:
            frame = cv2.flip(cv2.resize(frame, (640, 480)), 1)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            self.frame = frame.copy()

            self.faces = list(self.detector(frame, 0))
            self.lbl_faces["text"] = f"Faces in frame: {len(self.faces)}"

            for d in self.faces:
                cv2.rectangle(frame, (d.left(), d.top()),
                              (d.right(), d.bottom()), (255, 255, 255), 2)

            img = ImageTk.PhotoImage(Image.fromarray(frame))
            self.video.imgtk = img
            self.video.configure(image=img)

        self.win.after(20, self.update)

    def close(self):
        self.cap.release()
        self.win.destroy()

    def run(self):
        self.update()
        self.win.mainloop()


def main():
    logging.basicConfig(level=logging.INFO)
    FaceRegister().run()


if __name__ == "__main__":
    main()