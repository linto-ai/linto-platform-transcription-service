import re

__all__ = ["clean_text", "speakers_format", "mergeTranscriptions"]

def clean_text(text: str) -> str:
    """ Remove extra symbols from text """
    text = re.sub(r"<unk>", "", text)  # remove <unk> symbol
    text = re.sub(r"#nonterm:[^ ]* ", "", text)  # remove entity's mark
    text = re.sub(r"' ", "'", text)  # remove space after quote '
    text = re.sub(r" +", " ", text)  # remove multiple spaces
    text = text.strip()
    return text

def speakers_format(trans_data: dict, speakers_data: dict) -> dict:
    # Merge transcription and diarization into one json
    words = sorted(trans_data["words"], key=lambda x: x["start"])
    segments = sorted(speakers_data["segments"], key=lambda x: x["seg_begin"])
    output_speakers = []
    output_lines = []
    spk_index = 0
    current_speaker = {"speaker_id" : segments[spk_index]["spk_id"]} 
    current_speaker["words"] = []
    
    for word in words:
        if word["start"] > segments[spk_index]["seg_end"]: # Next segment
            if len(current_speaker["words"]): # Add to output
                output_lines.append(clean_text("{}: {}".format(current_speaker["speaker_id"], 
                    " ".join([item["word"] for item in current_speaker["words"]]))))
                current_speaker["start"] = current_speaker["words"][0]["start"]
                current_speaker["end"] = current_speaker["words"][-1]["end"]
                output_speakers.append(current_speaker)
            if spk_index + 1 < len(segments):
                spk_index += 1
            current_speaker = {"speaker_id" : segments[spk_index]["spk_id"]}
            current_speaker["words"] = []
        current_speaker["words"].append(word)
    if len(current_speaker["words"]):
        output_lines.append(clean_text("{}: {}".format(current_speaker["speaker_id"], 
            " ".join([item["word"] for item in current_speaker["words"]]))))
        output_speakers.append(current_speaker)
    return {"confidence-score": trans_data["confidence-score"], "speakers" : output_speakers, "text" : output_lines}

def mergeTranscriptions(transcriptions: list) -> dict:
    """ Merge transcription result into one transcription applying offsets """
    output = {"text" : "", "words" : [], "confidence-score" : 0.0}
    for transcription, offset in transcriptions:
        output["text"] += transcription["text"] + " "
        
        for word in transcription["words"]:
            offset_word = {"word" : word["word"], "start" : word["start"] + offset, "end" : word["end"] + offset, "conf" : word["conf"]}
            output["words"].append(offset_word)
        output["confidence-score"] += transcription["confidence-score"]
    output["confidence-score"] /= len(transcription)

    return output