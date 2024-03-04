from datetime import datetime
from time import time
from uuid import uuid4

from pymongo import MongoClient, errors

from transcriptionservice.transcription.configs.transcriptionconfig import \
    TranscriptionConfig
from transcriptionservice.transcription.transcription_result import \
    TranscriptionResult

""" The Databases is structured as follows:

A transcription service uses a database named transcriptiondb in which there are 2 collections:
- A collection named after the SERVICE_NAME to store raw transcription result associated with the associated running linto-stt service.
Those transcriptions are indexed using the audio file hashcode before transcoding and contain the transcription datetime and words information.
- A collection named "results" to store final transcriptions (includes diarization, punctuation data and post-processing). This collection is shared by all running
transcription services. The final transcription are indexed using a unique result_id and contains in addition to the result itself data related to 
origin and the configurations used.

"""


def mongo_error_handler(func):
    def inner_func(*args, **kwargs):
        try:
            res = func(*args, **kwargs)
        except errors.ServerSelectionTimeoutError:
            raise Exception("Could not connect to database")
        except Exception as e:
            raise Exception("Database error: {}".format(e))
        else:
            return res

    return inner_func


class DBClient:
    """DBClient setups and maintains a connexion to a MongoDB database."""

    def __init__(self, db_info: dict):
        """db_info object is a dictionary structured as follow:
        {
           "db_host" : database host,
           "db_port" : database listening port,
           "service_name" : service name used as collection name,
           "db_name": database's name
        }
        """
        self.client = MongoClient(
            db_info["db_host"],
            port=db_info["db_port"],
            connect=False,
            serverSelectionTimeoutMS=3000,
        )
        self.transcriptions_collection = self.client[db_info["db_name"]][db_info["service_name"]]
        self.results_collection = self.client[db_info["db_name"]]["results"]
        self.isset = True

    @mongo_error_handler
    def fetch_transcription(self, file_hash: str) -> dict:
        """Fetch transcription result in the SERVICE_NAME collection using file_hash as id"""
        result = self.transcriptions_collection.find_one({"_id": file_hash})
        return result["transcription"] if result is not None else None

    @mongo_error_handler
    def fetch_result(self, ressource_id: str) -> dict:
        """Fetch final result in the results collections using result_id as id"""
        result = self.results_collection.find_one({"_id": ressource_id})
        return result["result"] if result is not None else None

    @mongo_error_handler
    def push_transcription(self, file_hash: str, words: list):
        """Insert transcription result in the SERVICE_NAME collection using file_hash as id"""
        self.transcriptions_collection.find_one_and_update(
            {"_id": file_hash},
            {
                "$set": {
                    "datetime": datetime.fromtimestamp(time()).isoformat(),
                    "transcription": {"words": [w.json for w in words]},
                }
            },
            upsert=True,
        )

    @mongo_error_handler
    def push_result(
        self,
        file_hash: str,
        job_id: str,
        origin: str,
        service_name: str,
        config: TranscriptionConfig,
        result: TranscriptionResult,
    ) -> str:
        """Insert final result in the results collection and returns a result_id"""
        ressource_id = str(uuid4())
        self.results_collection.find_one_and_update(
            {"_id": ressource_id},
            {
                "$set": {
                    "hash": file_hash,
                    "job_id": job_id,
                    "origin": origin,
                    "service_name": service_name,
                    "datetime": datetime.fromtimestamp(time()).isoformat(),
                    "config": config.toJson(),
                    "result": result.final_result(),
                }
            },
            upsert=True,
        )
        return ressource_id

    def close(self):
        """Close client connexion"""
        if self.isset:
            self.client.close()
