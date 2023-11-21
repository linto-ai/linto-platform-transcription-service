import time
from typing import List, Tuple

from transcriptionservice.transcription.transcription_result import (
    SpeechSegment, TranscriptionResult, Word)

from .normalization import cleanText, textToNum

END_MARKERS = [".", ";", "!", "?", ":"]


class SubtitleItem:
    """SubTitleItem format a speech segment to subtitling item"""

    def __init__(self, words: List[Tuple[Word, str]], language: str = ""):
        self.words, self.final_words = zip(*words)
        self.language = language
        self.start = min([w.start for w in self.words])
        self.end = max([w.end for w in self.words])

    def formatUtterance(
        self, utterance: str, convert_numbers: bool, user_sub: List[Tuple[str, str]]
    ) -> str:
        if convert_numbers:
            utterance = textToNum(utterance, self.language)
        return cleanText(utterance, self.language, user_sub)

    def toSRT(
        self,
        index_start: int = 0,
        max_char_line: int = 40,
        return_raw: bool = False,
        convert_numbers: bool = False,
        user_sub: List[Tuple[str, str]] = [],
        max_lines: int = 2,
        display_spk: bool = False,
    ) -> Tuple[str, int]:
        """Ouput the Subtitle Item with SRT format"""
        i = 0
        output = ""
        c_w = 0
        c_l = 0
        words = []
        finals = []
        current_item = ""
        for word, final_word in zip(self.words, self.final_words):
            if c_w > max_char_line:
                current_item += "{}\n".format(" ".join(finals))
                finals = []
                c_l += 1
                c_w = 0
                if c_l >= max_lines:
                    final_item = self.formatUtterance(current_item, convert_numbers, user_sub)
                    output += "{}\n{} --> {}\n{}\n\n".format(
                        index_start + i + 1,
                        self.timeStampSRT(words[0].start),
                        self.timeStampSRT(words[-1].end),
                        final_item,
                    )
                    current_item = ""
                    i += 1
                    words = []
                    c_l = 0
            words.append(word)
            c_w += len(word.word if return_raw else final_word)
            finals.append(word.word if return_raw else final_word)
        current_item += "{}\n".format(" ".join(finals))
        final_item = self.formatUtterance(current_item, convert_numbers, user_sub)
        output += "{}\n{} --> {}\n{}\n\n".format(
            index_start + i + 1,
            self.timeStampSRT(words[0].start),
            self.timeStampSRT(words[-1].end),
            final_item,
        )
        return output, i + 1

    def toVTT(
        self,
        return_raw: bool = False,
        convert_numbers: bool = False,
        user_sub: List[Tuple[str, str]] = [],
        max_char_line: int = 40,
        max_line: int = 2,
    ) -> str:
        """Ouput the Subtitle Item with SRT format"""
        output = ""
        if len(str(self)) > max_char_line * max_line:
            words = []
            finals = []
            c = 0
            for word, final_word in zip(self.words, self.final_words):
                if c > max_char_line * max_line:
                    output += "{} --> {}\n".format(
                        self.timeStampVTT(words[0].start),
                        self.timeStampVTT(words[-1].end),
                    )
                    final_item = self.formatUtterance(
                        "{}\n\n".format(" ".join(finals)), convert_numbers, user_sub
                    )
                    output += final_item
                    words = []
                    finals = []
                    c = 0
                words.append(word)
                finals.append(word.word if return_raw else final_word)
                c += len(word.word if return_raw else final_word)
            output += "{} --> {}\n".format(
                self.timeStampVTT(words[0].start), self.timeStampVTT(words[-1].end)
            )
            final_item = self.formatUtterance(
                "{}\n\n".format(" ".join(finals)), convert_numbers, user_sub
            )
            output += final_item
            return output

        output = "{} --> {}\n".format(self.timeStampVTT(self.start), self.timeStampVTT(self.end))

        if return_raw:
            output += "{}\n\n".format(
                self.formatUtterance(
                    " ".join([w.word for w in self.words]), convert_numbers, user_sub
                )
            )
        else:
            output += "{}\n\n".format(self.formatUtterance(str(self), convert_numbers, user_sub))
        return output

    def timeStampSRT(self, t_str) -> str:
        """Format second string format to hh:mm:ss,ms SRT format"""
        t = float(t_str)
        ms = t % 1
        timeStamp = time.strftime("%H:%M:%S,", time.gmtime(t))
        return "{}{:03d}".format(timeStamp, int(ms * 1000))

    def timeStampVTT(self, t_str) -> str:
        """Format second string format to hh:mm:ss,ms VTT format"""
        t = float(t_str)
        ms = t % 1
        t = int(t)
        s = t % 60
        t -= s
        m = t // 60
        return "{:02d}:{:02d}.{:03d}".format(m, s, int(ms * 1000))

    def __str__(self) -> str:
        return " ".join(self.final_words)


class Subtitles:
    def __init__(self, transcription: TranscriptionResult, language: str):
        self.transcription = transcription
        self.language = language
        self.subtitleItems = []
        self._setupItems()

    def _setupItems(self):
        for segment in self.transcription.segments:
            self.subtitleItems.extend(self.segmentsToSubtitleItems(segment))

    def segmentsToSubtitleItems(
        self, segment: SpeechSegment, next_item_skip_t: float = 1.5
    ) -> List[SubtitleItem]:
        items = []
        current_words = []
        if segment.processed_segment is None:
            processed_words = segment.raw_segment.split(" ")
        else:
            processed_words = segment.processed_segment.split(" ")

        # TODO: there is a too strong assumption, which does not apply to ASR models like Whisper.
        #       Because Whisper can output punctuation marks that not (always) glued to the previous words.
        assert len(processed_words) == len(segment.words), "Processed word count mismatch"

        for i, word in enumerate(segment.words[:-1]):
            current_words.append((word, processed_words[i]))
            if (
                processed_words[i][-1] in END_MARKERS
                or segment.words[i + 1].start - word.end > next_item_skip_t
            ):
                items.append(SubtitleItem(current_words, self.language))
                current_words = []
        current_words.append((segment.words[-1], processed_words[-1]))
        items.append(SubtitleItem(current_words, self.language))
        return items

    def toSRT(
        self,
        return_raw: bool = False,
        convert_numbers: bool = False,
        user_sub: List[Tuple[str, str]] = [],
    ) -> str:
        output = ""
        i = 0
        for item in self.subtitleItems:
            r, n = item.toSRT(
                i,
                return_raw=return_raw,
                convert_numbers=convert_numbers,
                user_sub=user_sub,
            )
            output += r
            i += n
        return output

    def toVTT(
        self,
        return_raw: bool = False,
        convert_numbers: bool = False,
        user_sub: List[Tuple[str, str]] = [],
    ) -> str:
        output = "WEBVTT Kind: captions; Language: {}\n\n".format(self.language)
        for item in self.subtitleItems:
            output += item.toVTT(
                return_raw=return_raw,
                convert_numbers=convert_numbers,
                user_sub=user_sub,
            )
        return output
