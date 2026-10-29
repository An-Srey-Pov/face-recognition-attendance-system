"""
Offline voice notification service.
Uses pyttsx3 in a background thread so speech playback does not
freeze the OpenCV camera window.
On Windows, the worker thread initializes COM before creating
the pyttsx3 speech engine.
"""

import logging
import platform
import queue
import threading

import pyttsx3


class VoiceService:
    def __init__(
        self,
        rate=165,
        volume=1.0,
    ):
        self.rate = rate
        self.volume = max(
            0.0,
            min(1.0, volume),
        )

        self.message_queue = queue.Queue()
        self.closed = False
        self.engine_ready = threading.Event()
        self.engine_available = False

        self.worker_thread = threading.Thread(
            target=self._voice_worker,
            daemon=True,
            name="VoiceServiceThread",
        )

        self.worker_thread.start()

        # Wait briefly for the speech engine to initialize.
        self.engine_ready.wait(timeout=5)

        if not self.engine_available:
            logging.warning(
                "The voice engine is not available. "
                "Attendance will continue without speech."
            )

    def _voice_worker(self):
        """
        Initialize and run the text-to-speech engine inside
        the background worker thread.
        """

        engine = None
        pythoncom = None

        try:
            # Windows SAPI requires COM initialization in every
            # thread that uses the speech engine.
            if platform.system() == "Windows":
                try:
                    import pythoncom as windows_pythoncom

                    pythoncom = windows_pythoncom
                    pythoncom.CoInitialize()

                except ImportError:
                    logging.warning(
                        "pythoncom is unavailable. "
                        "Install pywin32 if Windows speech fails."
                    )

            engine = pyttsx3.init()

            engine.setProperty(
                "rate",
                self.rate,
            )

            engine.setProperty(
                "volume",
                self.volume,
            )

            self.engine_available = True

            logging.info(
                "Voice engine initialized successfully."
            )

        except Exception:
            logging.exception(
                "Could not initialize the text-to-speech engine."
            )

        finally:
            # Tell __init__ that initialization has finished,
            # whether it succeeded or failed.
            self.engine_ready.set()

        if engine is None:
            if pythoncom is not None:
                pythoncom.CoUninitialize()

            return

        try:
            while True:
                message = self.message_queue.get()

                try:
                    if message is None:
                        break

                    logging.info(
                        "Speaking: %s",
                        message,
                    )

                    engine.say(
                        str(message)
                    )

                    engine.runAndWait()

                except Exception:
                    logging.exception(
                        "Could not play voice notification."
                    )

                finally:
                    self.message_queue.task_done()

        finally:
            try:
                engine.stop()

            except Exception:
                logging.exception(
                    "Could not stop the text-to-speech engine."
                )

            if pythoncom is not None:
                pythoncom.CoUninitialize()

            logging.info(
                "Voice service stopped."
            )

    def speak(self, message):
        """
        Add a message to the background speech queue.
        """

        if self.closed:
            logging.warning(
                "Voice message ignored because "
                "the voice service is closed."
            )
            return False

        if not message:
            return False

        if not self.engine_available:
            logging.warning(
                "Voice message ignored because "
                "the speech engine is unavailable: %s",
                message,
            )
            return False

        self.message_queue.put(
            str(message)
        )

        return True

    def welcome(
        self,
        student_name,
        status=None,
    ):
        """
        Play the check-in message.
        """

        if status == "Late":
            message = (
                f"Welcome"
            )
        else:
            message = (
                f"Welcome, {student_name}. "
                "Your check-in has been recorded."
            )

        return self.speak(message)

    def goodbye(
        self,
        student_name,
        status=None,
    ):
        """
        Play the check-out message.
        """

        if status == "Early Leave":
            message = (
                f"Goodbye"
            )
        else:
            message = (
                f"Goodbye"
            )

        return self.speak(message)

    def wait_until_finished(self):
        """
        Wait until all queued speech messages are completed.
        """

        if self.engine_available:
            self.message_queue.join()

    def close(self):
        """
        Finish queued messages and stop the worker thread.
        """

        if self.closed:
            return

        self.closed = True

        if self.engine_available:
            # Messages already queued are processed before this
            # stop signal because Queue uses FIFO ordering.
            self.message_queue.put(None)

            self.worker_thread.join(
                timeout=15
            )

            if self.worker_thread.is_alive():
                logging.warning(
                    "Voice thread did not finish within "
                    "the timeout."
                )