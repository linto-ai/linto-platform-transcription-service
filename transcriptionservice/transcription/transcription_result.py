"""The transcription_result module holds classes responsible for holding, merging and formating transcription results."""
import json
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Union, Any


@dataclass
class Word:
    """Contains word informations"""

    word: str
    start: float
    end: float
    conf: float

    def apply_offset(self, offset: float):
        self.start += offset
        self.end += offset

    @property
    def json(self) -> dict:
        return self.__dict__


@dataclass
class DiarizationSegment:
    """Contain diarization segment information"""

    seg_begin: float
    seg_end: float
    spk_id: Any
    seg_id: int

    @property
    def json(self) -> dict:
        return self.__dict__


@dataclass
class SpeechSegment:
    speaker_id: str = None
    words: list = field(default_factory=list)
    processed_segment = None

    def toString(self, include_spkid: bool = False, spk_sep: str = ":"):
        output = (
            f"{self.speaker_id}{spk_sep} "
            if include_spkid and self.speaker_id is not None
            else ""
        )
        return output + (
            self.raw_segment
            if self.processed_segment is None
            else self.processed_segment
        )

    @property
    def raw_segment(self) -> str:
        return " ".join([w.word for w in self.words]).strip()

    @property
    def start(self) -> float:
        return min([w.start for w in self.words]) if len(self.words) > 0 else 0.0

    @property
    def end(self) -> float:
        return max([w.end for w in self.words]) if len(self.words) > 0 else 0.0

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def json(self) -> dict:
        return {
            "spk_id": self.speaker_id,
            "start": self.start,
            "end": self.end,
            "duration": self.duration,
            "raw_segment": self.raw_segment,
            "segment": self.processed_segment
            if self.processed_segment is not None
            else self.raw_segment,
            "words": [w.json for w in self.words],
        }


class TranscriptionResult:
    """Transcription result manages transcription results, post-processing and formating for transcription results."""

    def __init__(self, transcriptions: List[Tuple[dict, float]], spk_ids: list = None):
        """Initialisation accepts list of tuple (transcription, time_offset)"""
        self.transcription_confidence = 0.0
        self.words = []
        self.segments = []
        self.diarizationSegments = []
        if transcriptions:
            self._mergeTranscription(transcriptions, spk_ids)

    def _mergeTranscription(
        self, transcriptions: List[Tuple[dict, float]], spk_ids: list = None
    ) -> None:
        """Merges transcription results applying offsets"""
        self.transcription_confidence = 0.0
        num_words = 0
        for transcription, offset in transcriptions:
            for w in transcription["words"]:
                word = Word(**w)
                word.apply_offset(offset)
                self.words.append(word)
                self.transcription_confidence += word.conf
            num_words += len(transcription["words"])
        if num_words:
            self.transcription_confidence /= num_words
        self.words.sort(key=lambda x: x.start)

        if spk_ids:
            for (transcription, offset), id in zip(transcriptions, spk_ids):
                seg_words = []
                for w in transcription["words"]:
                    word = Word(**w)
                    word.apply_offset(offset)
                    seg_words.append(word)
                if seg_words:
                    self.segments.append(SpeechSegment(id, seg_words))
            self.segments = sorted(self.segments, key=lambda seg: seg.start)

    def setTranscription(self, words: List[dict]):
        for w in words:
            self.words.append(Word(**w))
        self.transcription_confidence = sum([w.conf for w in self.words]) / len(
            self.words
        ) if len(self.words) else 0.0

    def setDiarizationResult(self, diarizationResult: Union[str, dict]):
        """Create speech segments using word and diarization data"""
        diarization_data = (
            json.loads(diarizationResult)
            if isinstance(diarizationResult, str)
            else diarizationResult
        )

        # Get segments ordered by start time
        self.diarizationSegments = [
            DiarizationSegment(**segment)
            for segment in sorted(
                diarization_data["segments"], key=lambda x: x["seg_begin"]
            )
        ]

        # Filter out segments that are included in others (i.e. which end before previous segment)
        self.diarizationSegments = [self.diarizationSegments[i] for i in range(len(self.diarizationSegments)) \
            if i == 0 or self.diarizationSegments[i].seg_end > self.diarizationSegments[i-1].seg_end]

        self.words.sort(key=lambda x: x.start)

        # Interpolates speaker change timestamps
        # Starts the first segment at 0.0, ends the last segment at max(word.ends)
        # If two consecutive diarization segments are not joint, find the middle point
        self.diarizationSegments[0].seg_begin = 0.0
        if len(self.words):
            self.diarizationSegments[-1].seg_end = max(
                self.diarizationSegments[-1].seg_end, self.words[-1].end
            )
        for first_segment, second_segment in zip(
            self.diarizationSegments[:-1], self.diarizationSegments[1:]
        ):
            # When there is a gap or an overlap between the two segments
            if first_segment.seg_end != second_segment.seg_begin:
                middle_point = (
                    first_segment.seg_end
                    + (second_segment.seg_begin - first_segment.seg_end) / 2
                )
                first_segment.seg_end = second_segment.seg_begin = middle_point

        seg_index = 0
        previous_id = None
        current_id = self.diarizationSegments[seg_index].spk_id
        current_words = []

        # Iterate over segments and words to create speech segments
        for i, word in enumerate(self.words):
            while not self._resolveWordSegment(i, self.diarizationSegments[seg_index]):
                # Next segment
                if seg_index + 1 < len(self.diarizationSegments):
                    seg_index += 1
                    next_id = self.diarizationSegments[seg_index].spk_id
                else:
                    break
                if len(current_words):
                    if current_id != previous_id: # Flush current segment
                        self.segments.append(SpeechSegment(current_id, current_words))
                    else: # Merge with previous segment
                        self.segments[-1].words += current_words
                    previous_id = current_id
                current_id = next_id
                current_words = []
            current_words.append(word)
        if len(current_words):
            if current_id != previous_id: # Flush current segment
                self.segments.append(SpeechSegment(current_id, current_words))
            else: # Merge with previous segment
                self.segments[-1].words += current_words

    def _resolveWordSegment(
        self, word_index: int, current_diarization_seg: dict
    ) -> bool:
        """Applies word placement rules and decides if the word belong to the current_segment (True) or to the next (False)"""
        word_start = self.words[word_index].start
        word_end = self.words[word_index].end
        # Word completely within current segment
        if word_end <= current_diarization_seg.seg_end:
            return True
        # Word completely outside current segment
        if word_start >= current_diarization_seg.seg_end:
            return False
        # Word straddling two segments
        if not word_index:
            return False
        if word_index == len(self.words) - 1:
            return True
        # Decide based on the distance with the previous and the next words
        if (
            word_start - self.words[word_index - 1].end
            <= self.words[word_index + 1].start - word_end
        ):
            return True
        return False

    def setNoDiarization(self):
        """Convert word data into a speech segment when there is no diarization"""
        self.segments.append(SpeechSegment(None, self.words))

    def setProcessedSegment(self, processed_segments: Union[List[str], str]):
        """Add the processed_segment value to segments"""
        if type(processed_segments) == str:
            processed_segments = [processed_segments]
        for seg, proc_seg in zip(self.segments, processed_segments):
            seg.processed_segment = proc_seg

    @property
    def final_transcription(self) -> str:
        return " \n".join(
            [seg.toString(include_spkid=True) for seg in self.segments]
        ).strip()

    @property
    def raw_transcription(self) -> str:
        """Return the raw transcription.

        Returns:
            str: Transcription without any other processing
        """
        return " ".join([w.word for w in self.words]).strip()

    @classmethod
    def fromDict(cls, resultDict: dict):
        """Create TranscriptionResult from dictionnary"""
        result = TranscriptionResult(None)
        result.transcription_confidence = resultDict["confidence"]
        for segment in resultDict["segments"]:
            seg = SpeechSegment(
                segment["spk_id"], [Word(**w) for w in segment["words"]]
            )
            seg.processed_segment = segment["segment"]
            result.segments.append(seg)

        result.diarizationSegments = [
            DiarizationSegment(**diarizationSegment)
            for diarizationSegment in resultDict["diarization_segments"]
        ]

        return result

    def final_result(self) -> dict:
        return {
            "transcription_result": self.final_transcription,
            "raw_transcription": self.raw_transcription,
            "confidence": self.transcription_confidence,
            "segments": [s.json for s in self.segments],
            "diarization_segments": [seg.json for seg in self.diarizationSegments],
        }
