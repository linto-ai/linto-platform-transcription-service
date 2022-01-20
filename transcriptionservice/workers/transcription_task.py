import os
import io
import requests
import time
import json

from pymongo import MongoClient

from transcriptionservice.workers.audio import transcoding, splitFile
from transcriptionservice.workers.celeryapp import celery
from transcriptionservice.server.mongodb.mongotutils import check_for_result
from transcriptionservice.server.mongodb.db_client import DBClient
from transcriptionservice.workers.utils import TranscriptionConfig, TranscriptionResult

__all__ = ["transcription_task"]

# Create shared mongoclient
db_info = {"db_host" : os.environ.get("MONGO_HOST", None),
           "db_port" : int(os.environ.get("MONGO_PORT", None)),
           "service_name" : os.environ.get("SERVICE_NAME", None),
           "db_name": "result_db"}

language = os.environ.get("LANGUAGE", None)
           
db_client = DBClient(db_info)

@celery.task(name="transcription_task", bind=True)
def transcription_task(self, task_info: dict, file_path: str):
    """ Transcription task processes a transcription request. 
    
    task_info contains the following field:
    - "transcription_config" : Transcription configuration
    - "result_db" : Connexion info to the result database
    - "service": Name of the transcription service
    - "hash": Audio File Hash
    - "keep_audio": If False, the audio file is deleted after the task.
    """
    self.update_state(state="STARTED", meta={"current": 0, "total": 1, "step": "Started"})
    print(task_info)

    config = TranscriptionConfig(task_info["transcription_config"])

    # Configuration check
    ## Subtitle requires diarization
    if config.subtitleConfig["enableSubtitle"]:
        config.diarizationConfig["enableDiarization"] = True

    # Setup flags
    do_diarization = config.diarizationConfig["enableDiarization"]
    do_punctuation = config.enablePunctuation

    # Check that required services are available # TODO test
    worker_list = celery.control.inspect().active_queues().keys()
    worker_names = set(k.split("@")[0] for k in worker_list)

    if not "{}_worker".format(task_info["service_name"]) in worker_names:
        raise Exception("No transcription service running for {}".format(task_info["service_name"]))
    
    if do_diarization and not "diarization_worker" in worker_names:
        raise Exception("Request required diarization but no diarization service is running.")

    if do_punctuation and not f"punctuation_{language}" in worker_names:
        raise Exception("Request required punctuation but no punctuation service is running.")

    # Preprocessing
    # Transtyping
    file_name = transcoding(file_path)
    
    # Split using VAD
    subfiles, total_duration = splitFile(file_name)
    print(f'Input file has been split into {len(subfiles)} subfiles')

    # Progress monitoring
    total_step = 4 + do_diarization + do_punctuation
    current_step = 1
    speakers = None
    self.update_state(state="STARTED", meta={"current": current_step, "total": total_step, "step": "Transcription (0%)"})
    
    # Transcription
    transJobIds = []
    for subfile_path, offset, duration in subfiles:
        transJobId = celery.send_task(name="transcribe_task", queue=task_info["service_name"], args=[subfile_path, True])
        transJobIds.append((transJobId, offset, duration, subfile_path))

    # Diarization (In parallel)
    if do_diarization:
        diarJobId = celery.send_task(name="diarization_task", 
                                     queue='diarization', 
                                     args=[file_name, 
                                           config.diarizationConfig["numberOfSpeaker"], 
                                           config.diarizationConfig["maxNumberOfSpeaker"]])
    
    # Wait for all the transcription jobs
    transcriptions = []
    pc_trans = 0.0
    failed = False
    for jobId, offset, duration, subfile_path in transJobIds:
        if failed:
            jobId.revoke()
            os.remove(subfile_path)
            continue
        transcription = jobId.get(disable_sync_subtasks=False)
        if len(transJobIds) > 1:
            os.remove(subfile_path)
        if jobId.status == "FAILURE":
            failed = True
            continue
        transcriptions.append((transcription, offset))
        pc_trans += (duration / total_duration) * 100
        self.update_state(state="STARTED", meta={"current": current_step, "total": total_step, "step": f"Transcription ({pc_trans:.2f}%)"})
    
    if failed:
        raise Exception("Transcription has failed: {}".format(transcription))
    current_step+=1

    # Transcription result
    transcription_result = TranscriptionResult(transcriptions, config)

    # Diarization result
    if do_diarization:
        self.update_state(state="STARTED", meta={"current": current_step, "total": total_step, "step": "Diarization"})
        speakers = diarJobId.get(disable_sync_subtasks=False)
        if diarJobId.status == "FAILURE":
            raise Exception("Diarization has failed: {}".format(speakers))
        else:
            transcription_result.setDiarizationResult(speakers)
        current_step += 1
    else:
        transcription_result.setNoDiarization()

    # Punctuation 
    if do_punctuation:
        self.update_state(state="STARTED", meta={"current": current_step, "total": total_step, "step": "Punctuation"})
        puncJobId = celery.send_task(name="punctuation_task", queue=f'punctuation_{language}', args=[[seg.toString() for seg in transcription_result.segments]])
        try: 
            punctuated_text = puncJobId.get(disable_sync_subtasks=False)
        except Exception as e:
            raise Exception("Punctuation has failed: {}".format(str(e)))
        current_step += 1
        transcription_result.setProcessedSegment(punctuated_text)

    # Write result in database
    self.update_state(state="STARTED", meta={"current": current_step, "total": total_step, "step": "Cleaning"})
    try:
        db_client.write_result(task_info["hash"], config, transcription_result)
    except Exception as e:
        print("Failed to write result in database: {} ".format(e))
    
    # Free ressource
    if not task_info["keep_audio"]:
        try:
            os.remove(file_name)
        except Exception as e:
            print("Failed to remove ressource {}".format(file_path))

    return transcription_result.final_result()