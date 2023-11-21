#!/usr/bin/env python3

import logging
import os

from celery.result import AsyncResult
from celery.result import states as task_states
from celery import current_app
from celery.signals import after_task_publish

from flask import Flask, json, request

from transcriptionservice import logger
from transcriptionservice.broker.discovery import list_available_services
from transcriptionservice.server.confparser import createParser
from transcriptionservice.server.formating import formatResult
from transcriptionservice.server.mongodb.db_client import DBClient
from transcriptionservice.server.serving import GunicornServing
from transcriptionservice.server.swagger import setupSwaggerUI
from transcriptionservice.server.utils import fileHash, read_timestamps, requestlog
from transcriptionservice.server.utils.ressources import write_ressource
from transcriptionservice.transcription.configs.transcriptionconfig import (
    TranscriptionConfig,
    TranscriptionConfigMulti,
)
from transcriptionservice.transcription.transcription_task import (
    transcription_task,
    transcription_task_multi,
)

AUDIO_FOLDER = "/opt/audio"
SUPPORTED_HEADER_FORMAT = ["text/plain", "application/json", "text/vtt", "text/srt"]

app = Flask("__services_manager__")
app.config["JSON_AS_ASCII"] = False
app.config["JSON_SORT_KEYS"] = False


@app.route("/healthcheck", methods=["GET"])
def healthcheck():
    """Server healthcheck"""
    return "1", 200


@app.route("/list-services", methods=["GET"])
def list_subservices():
    return list_available_services(as_json=True, ensure_alive=True), 200


@app.route("/job/<jobid>", methods=["GET"])
def jobstatus(jobid):
    try:
        task = AsyncResult(jobid)
    except Exception as error:
        return ({"state": "failed", "reason": error.message}, 500)
    state = task.state

    if state == "SENT": # See below
        return json.dumps({"state": "pending"}), 202
    elif state == task_states.STARTED:
        return (
            json.dumps({"state": "started", "steps": task.info.get("steps", {})}),
            202,
        )
    elif state == task_states.SUCCESS:
        result_id = task.get()
        return json.dumps({"state": "done", "result_id": result_id}), 201
    elif state == task_states.PENDING:
        return json.dumps({"state": "failed", "reason": f"Unknown jobid {jobid}"}), 404
    elif state == task_states.FAILURE:
        return json.dumps({"state": "failed", "reason": str(task.result)}), 500
    else:
        return json.dumps({"state": "failed", "reason": f"Task returned an unknown state {state}"}), 500

# This is to distinguish between a pending state meaning that the task is unknown,
# and a pending state meaning that the task is waiting for a worker to start.
# see https://stackoverflow.com/questions/9824172/find-out-whether-celery-task-exists
@after_task_publish.connect
def update_sent_state(sender=None, headers=None, **kwargs):
    # the task may not exist if sent using `send_task` which
    # sends tasks by name, so fall back to the default result backend
    # if that is the case.
    task = current_app.tasks.get(sender)
    backend = task.backend if task else current_app.backend
    backend.store_result(headers['id'], None, "SENT")

@app.route("/results/<result_id>", methods=["GET"])
def results(result_id):
    # Expected format
    expected_format = request.headers.get("accept")
    if not expected_format in SUPPORTED_HEADER_FORMAT:
        return (
            "Accept format {} not supported. Supported MIME types are :{}".format(
                expected_format, " ".join(SUPPORTED_HEADER_FORMAT)
            ),
            400,
        )

    # Result
    result = db_client.fetch_result(result_id)
    if result is None:
        return f"No result associated with id {result_id}", 404
    logger.debug(f"Returning result fo result_id {result_id}")

    # Query parameters
    return_raw = request.args.get("return_raw", False) in [1, True, "true"]
    convert_numbers = request.args.get("convert_numbers", False) in [1, True, "true"]
    sub_list = request.args.getlist("wordsub", None)
    try:
        sub_list = [
            tuple(elem.split(":"))
            for elem in sub_list
            if elem.strip() != "" and ":" in elem
        ]
    except:
        logger.warning("Could not parse substitution items: {}".format(sub_list))
        sub_list = []

    return (
        formatResult(
            result,
            expected_format,
            raw_return=return_raw,
            convert_numbers=convert_numbers,
            user_sub=sub_list,
        ),
        200,
    )


@app.route("/transcribe-multi", methods=["POST"])
def transcription_multi():
    """Route for multiple audio file transcription"""

    files = request.files.getlist("file")
    if not len(files):
        return "Not file attached to request", 400

    if len(files) == 1:
        return "For single file transcription, use the /transcribe route", 400

    # Header check
    expected_format = request.headers.get("accept")
    if not expected_format in SUPPORTED_HEADER_FORMAT:
        return (
            "Accept format {} not supported. Supported MIME types are :{}".format(
                expected_format, " ".join(SUPPORTED_HEADER_FORMAT)
            ),
            400,
        )

    # Files
    random_hash = fileHash(os.urandom(32))
    audios = []
    for audio_file in files:
        file_buffer = audio_file.read()
        file_hash = fileHash(file_buffer)
        file_ext = audio_file.filename.split(".")[-1]
        try:
            file_path = write_ressource(file_buffer, f"{file_hash}_{random_hash}", AUDIO_FOLDER, file_ext)
        except Exception as e:
            logger.error("Failed to write ressource: {}".format(e))
            return "Server Error: Failed to write ressource", 500
        audios.append(
            {
                "filename": audio_file.filename,
                "hash": file_hash,
                "file_path": file_path,
            }
        )
    # Parse transcription config
    try:
        transcription_config = TranscriptionConfigMulti(
            request.form.get("transcriptionConfig", {})
        )
        logger.debug(transcription_config)
    except Exception:
        logger.debug(request.form.get("transcriptionConfig", {}))
        return "Failed to interpret transcription config", 400

    # Task info
    task_info = {
        "transcription_config": transcription_config.toJson(),
        "service_name": config.service_name,
        "hash": file_hash,
        "keep_audio": config.keep_audio,
    }

    task = transcription_task_multi.apply_async(
        queue=config.service_name + "_requests", args=[task_info, audios]
    )
    logger.debug(f"Create trancription task with id {task.id}")
    return (
        json.dumps({"jobid": task.id})
        if expected_format == "application/json"
        else task.id
    ), 201


@app.route("/transcribe", methods=["POST"])
def transcription():
    # Get file and generate hash
    if not len(list(request.files.keys())):
        return "Not file attached to request", 400

    elif len(list(request.files.keys())) > 1:
        logger.warning(
            "Received multiple files at once. Multifile is not supported yet, n>1 file are ignored"
        )

    # Files
    ## Audio file
    file_key = list(request.files.keys())[0]
    file_buffer = request.files[file_key].read()
    extension = request.files[file_key].filename.split(".")[-1]
    file_hash = fileHash(file_buffer)

    # Timestamps file
    if "timestamps" in request.files.keys():
        timestamps_buffer = request.files["timestamps"].read()
        timestamps = read_timestamps(timestamps_buffer)
    else:
        timestamps = None

    # Header check
    expected_format = request.headers.get("accept")
    if not expected_format in SUPPORTED_HEADER_FORMAT:
        return (
            "Accept format {} not supported. Supported MIME types are :{}".format(
                expected_format, " ".join(SUPPORTED_HEADER_FORMAT)
            ),
            400,
        )
    logger.debug(request.headers.get("accept"))

    # Request flags
    force_sync = request.form.get("force_sync", False) in [1, True, "true"]
    logger.debug(f"force_sync: {force_sync}")

    # Parse transcription config
    try:
        transcription_config = TranscriptionConfig(
            request.form.get("transcriptionConfig", {})
        )
        logger.debug(transcription_config)
    except Exception:
        logger.debug(request.form.get("transcriptionConfig", {}))
        return "Failed to interpret transcription config", 400

    # The hash depends on options (of what comes before STT)
    file_hash = f"{file_hash} {timestamps if timestamps is not None else transcription_config.vadConfig.toJson()}".encode("utf8")
    file_hash = fileHash(file_hash)

    requestlog(logger, request.remote_addr, transcription_config, file_hash, False)

    # Create ressource
    random_hash = fileHash(os.urandom(32))
    try:
        file_path = write_ressource(file_buffer, f"{file_hash}_{random_hash}", AUDIO_FOLDER, extension)
    except Exception as e:
        logger.error("Failed to write ressource: {}".format(e))
        return "Server Error: Failed to write ressource", 500

    logger.debug("Create transcription task")

    task_info = {
        "transcription_config": transcription_config.toJson(),
        "service_name": config.service_name,
        "hash": file_hash,
        "keep_audio": config.keep_audio,
        "timestamps": timestamps,
    }

    task = transcription_task.apply_async(
        queue=config.service_name + "_requests", args=[task_info, file_path]
    )
    logger.debug(f"Create trancription task with id {task.id}")
    # Forced synchronous
    if force_sync:
        result_id = task.get()
        state = task.status
        if state == "SUCCESS":
            result = db_client.fetch_result(result_id)
            return formatResult(result, expected_format), 200
        else:
            return json.dumps({"state": "failed", "reason": str(task.result)}), 400

    return (
        json.dumps({"jobid": task.id})
        if expected_format == "application/json"
        else task.id
    ), 201


@app.route("/revoke/<jobid>", methods=["GET"])
def revoke(jobid):
    AsyncResult(jobid).revoke()
    return "done", 200


@app.route("/job-log/<jobid>", methods=["GET"])
def getlogs(jobid):
    if os.path.exists(f"/usr/src/app/logs/{jobid}.txt"):
        with open(f"/usr/src/app/logs/{jobid}.txt", "r") as logfile:
            return "\n".join(logfile.readlines()), 200
    else:
        return f"No log found for jobid {jobid}", 400


@app.errorhandler(405)
def method_not_allowed(error):
    return "The method is not allowed for the requested URL", 405


@app.errorhandler(404)
def page_not_found(error):
    return "The requested URL was not found", 404


@app.errorhandler(500)
def server_error(error):
    logger.error(error)
    return "Server Error", 500


if __name__ == "__main__":
    parser = createParser()  # Parser definition at server/utils/confparser.py

    config = parser.parse_args()
    logger.setLevel(logging.DEBUG if config.debug else logging.INFO)

    try:
        # Setup SwaggerUI
        if config.swagger_path is not None:
            setupSwaggerUI(app, config)
            logger.debug("Swagger UI set.")
    except Exception as e:
        logger.warning("Could not setup swagger: {}".format(str(e)))

    # Results database info
    db_info = {
        "db_host": config.mongo_uri,
        "db_port": config.mongo_port,
        "service_name": config.service_name,
        "db_name": "transcriptiondb",
    }

    db_client = DBClient(db_info)

    logger.info("Starting ingress")
    logger.debug(config)
    serving = GunicornServing(
        app,
        {
            "bind": "{}:{}".format("0.0.0.0", 80),
            "workers": config.concurrency + 1,
            # "timeout": 3600 * 24,
        },
    )

    try:
        serving.run()
    except KeyboardInterrupt:
        logger.info("Process interrupted by user")
    finally:
        db_client.close()
