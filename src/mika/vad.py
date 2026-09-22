import numpy as np

SILENCE_DB = -100.0  # Returned for a frame with zero energy (no sound)
FULL_SCALE = 32768.0  # Maximum amplitude for 16-bit audio


def frame_db(frame):
    cvt_frame = frame.astype(np.float64)
    rms = np.sqrt(np.mean(cvt_frame**2))
    if rms == 0:
        return SILENCE_DB
    return 20 * np.log10(rms / FULL_SCALE)
