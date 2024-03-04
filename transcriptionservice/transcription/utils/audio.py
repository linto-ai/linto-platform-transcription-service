import os
import subprocess
from typing import Dict, List, Tuple

import numpy as np
import wavio
import webrtcvad


def transcoding(
    input_file_path: str,
    output_sr: int = 16000,
    output_channels: int = 1,
    cleanup: bool = True,
) -> str:
    """Transcode the input file into 16b PCM Mono Wave file at given sample rate"""
    # Check File
    if not os.path.isfile(input_file_path):
        raise FileNotFoundError(f"Ressource not found: {input_file_path}")

    # Output name
    folder = os.path.dirname(input_file_path)
    basename = os.path.splitext(os.path.basename(input_file_path))[0]
    if input_file_path.endswith(".wav"):
        basename = f"_{basename}.wav"
    else:
        basename = f"{basename}.wav"
    output_file_path = os.path.join(folder, basename)

    # Subprocess
    command = f"ffmpeg -i {input_file_path} -y -acodec pcm_s16le"
    if output_channels is not None:
        command += f" -ac {output_channels}"
    command += f" -ar {output_sr} {output_file_path}"

    process = subprocess.Popen(command.split(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()

    if not os.path.isfile(output_file_path):
        stderr = stderr.decode("utf-8")
        raise Exception(f"Failed transcoding (command: {command}):\n{stderr}")

    # Cleanup
    if cleanup:
        os.remove(input_file_path)

    return output_file_path


def getDuration(file_path):
    content = wavio.read(file_path)
    num_samples = content.data.shape[0]
    return num_samples / content.rate


_vad_methods = [
    "WebRTC"
]

def validate_vad_method(method):
    _method = None
    for m in _vad_methods:
        if method.lower() == m.lower():
            _method = m
            break
    if _method is None:
        raise ValueError(f"Invalid value of {method}, not in {_vad_methods}")
    return _method

def vadCutIndexes(
    audio,
    sample_rate,
    chunk_length: float = 0.03,
    mode: int = 1,
    min_silence: float = 0.6,
    max_segment_duration: float = None,
    method: str = "WebRTC",
):
    """Apply VAD on the signal and returns cut indexes located between speech segments"""
    min_silence_frame = min_silence / chunk_length
    max_speech_frame = max_segment_duration / chunk_length if max_segment_duration else None

    method = validate_vad_method(method)

    if method == "WebRTC":
        vad = webrtcvad.Vad()
        vad.set_mode(mode)
    else:
        raise NotImplementedError(f"VAD method with {method}")

    chunk_size = int(sample_rate * chunk_length)
    vad_res = []

    # Split in chunk size and process VAD
    for start in range(0, len(audio) - chunk_size, chunk_size):
        buffer = (audio[start : start + chunk_size]).astype(np.int16).tobytes()
        vad_res.append(vad.is_speech(buffer, sample_rate))

    # Determines cut indexes in the middle of silence windows
    # Ignore silence windows which length are < min_silence_frame
    was_speech = vad_res[0]
    sil_start_i = 0
    speech_start_i = 0
    previous_candidate = None
    cut_indexes = []

    for i, is_speech in enumerate(vad_res):
        if is_speech and not was_speech:  # Start of speech
            candidate = int(np.mean([sil_start_i, i]))
            is_silence_long = (i - sil_start_i > min_silence_frame)
            is_speech_long = (max_speech_frame and (i - speech_start_i > max_speech_frame))
            if is_silence_long or is_speech_long:
                if is_speech_long and previous_candidate:
                    candidate = previous_candidate
                cut_indexes.append(candidate)
                speech_start_i = candidate
                previous_candidate = None
            else:
                previous_candidate = candidate
            was_speech = True

        elif not is_speech and was_speech:  # Start of silence
            was_speech = False
            sil_start_i = i

    # Returns cut_indexes as list
    return (np.array(cut_indexes) * chunk_size).astype(np.int32).tolist()


def splitFile(
    file_path,
    method: str = "WebRTC",
    min_length: float = 10,
    min_segment_duration: float = None,
    max_segment_duration: float = None,
    min_silence: float = 0.6,
    around_min_segment_duration: bool = False,
    ) -> Tuple[List[Tuple[str, float, float]], float]:
    """
    Split a file into multiple subfiles using vad
    
    Args:
        file_path (str): Audiofile
        method (str): VAD method [WebRTC]
        min_length (float): Minimum length of the file in seconds to apply the VAD
        min_segment_duration (float): Minimum duration of a segment in seconds
        min_silence (float): Minimum duration of silence in seconds
        around_min_segment_duration (bool): If True, segments can be kept just before they reach min_segment_duration
    """

    if min_segment_duration and max_segment_duration:
        if min_segment_duration > max_segment_duration:
            raise ValueError(f"min_segment_duration ({min_segment_duration}) > max_segment_duration ({max_segment_duration})")

    # TODO: factorize with splitUsingTimestamps

    content = wavio.read(file_path)
    sr = content.rate
    audio = np.squeeze(content.data)

    # Do not split file under min_length
    if len(audio) / sr < min_length:
        return _with_stat_durations([(file_path, 0.0, len(audio) / sr)])

    # Get cut indexes based on vad
    cut_indexes = vadCutIndexes(audio, sr, method=method, min_silence=min_silence, max_segment_duration=max_segment_duration)

    # TODO: use "min_segment_duration" in vadCutIndexes()
    if min_segment_duration:
        min_segment_samples = min_segment_duration * sr
        new_cut_indexes = []
        start = 0
        stop_candidate = None
        for stop in cut_indexes:
            if stop - start > min_segment_samples:
                if around_min_segment_duration and stop_candidate is not None:
                    new_cut_indexes.append(stop_candidate)
                    start = stop_candidate
                    stop_candidate = None
                    if stop - start < min_segment_samples:
                        continue
                new_cut_indexes.append(stop)
                start = stop
                stop_candidate = None
            else:
                stop_candidate = stop
        cut_indexes = new_cut_indexes

    # If no cut detected
    if len(cut_indexes) == 0:
        return _with_stat_durations([(file_path, 0.0, len(audio) / sr)])
    basename = os.path.splitext(file_path)[0]

    # Create subfiles
    subfiles = []
    i = 0
    for start, stop in zip([0] + cut_indexes, cut_indexes + [len(audio)]):
        subfile_path = f"{basename}_{i}.wav"
        offset = start / sr
        wavio.write(subfile_path, audio[start:stop], sr)
        duration = (stop - start) / sr
        subfiles.append((subfile_path, offset, duration))
        i += 1

    return _with_stat_durations(subfiles)

def _with_stat_durations(subfiles):
    total_duration = 0.0
    min_duration = float("inf")
    max_duration = 0.0
    for _, _, duration in subfiles:
        total_duration += duration
        min_duration = min(min_duration, duration)
        max_duration = max(max_duration, duration)
    return subfiles, {
        "total": total_duration,
        "mean": total_duration / len(subfiles),
        "min": min_duration,
        "max": max_duration,
    }

def splitUsingTimestamps(
    file_path: str, timestamps: List[Dict]
) -> Tuple[List[Tuple[str, float, float]], float]:
    """Split using a list of timestamps

    Args:
        file_path (str): Audiofile
        timestamps (List[Dict]): A list of timesample {"start": float, "end": float, "id": any}

    Returns:
        Tuple[List[Tuple[str, float, float]], float]: ([(subfile_name, start, stop),], total_duration)
    """
    timestamps = sorted(timestamps, key=lambda x: x["start"])
    content = wavio.read(file_path)
    sr = content.rate
    audio = np.squeeze(content.data)
    basename = os.path.splitext(file_path)[0]
    # Create subfiles
    subfiles = []
    total_duration = 0.0
    for i, seg in enumerate(timestamps):
        subfile_path = f"{basename}_{i}.wav"
        offset = seg["start"]
        start = int(seg["start"] * sr)
        stop = int(seg["end"] * sr)
        wavio.write(subfile_path, audio[start:stop], sr)
        duration = (seg["end"] - seg["start"]) / sr
        subfiles.append((subfile_path, offset, duration))
        total_duration += duration

    return subfiles, total_duration
