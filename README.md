# Face Recognition Attendance System

This project uses face recognition to record attendance and provides a Flask dashboard for viewing and exporting records.
## Setup

Install dependencies:

```powershell
pip install -r requirements.txt
```

Download the dlib model files and place them in `models/trained_model/`:

- `shape_predictor_68_face_landmarks.dat`
- `dlib_face_recognition_resnet_model_v1.dat`

## Run

Run commands from the project directory:

1. Collect face images: `python -m face_register.get_faces_from_camera`
2. Generate face embeddings: `python -m face_recognition.train_model`
3. Start camera attendance: `python -m face_recognition.recognize` or `python -m attendance.attendance_taker`
4. Start the web dashboard: `python app.py`
   Then open `http://127.0.0.1:5000/` in your browser and sign in.
5. Export a daily CSV report: `python -m attendance.attendance_report --date YYYY-MM-DD`

Registered face images are stored in `dataset/students/`, generated embeddings in `models/embeddings/`, and attendance records in `database/attendance.db`.
