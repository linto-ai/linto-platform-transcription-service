#!/usr/bin/env python3
import os
import logging
import argparse

import celery
from celery.result import AsyncResult
from celery.result import states as task_states
from flask import Flask, request, abort, Response, json

from transcriptionservice.server.serving import GunicornServing
from transcriptionservice.workers.transcription_task import transcription_task
from transcriptionservice.server.confparser import createParser
from transcriptionservice.server.swagger import setupSwaggerUI
from transcriptionservice.server.utils import fileHash, requestlog, formatResult
from transcriptionservice.workers.utils import TranscriptionConfig, TranscriptionResult
from transcriptionservice.server.utils.ressources import write_ressource
from transcriptionservice.server.mongodb.db_client import DBClient

AUDIO_FOLDER = "/opt/audio"
SUPPORTED_HEADER_FORMAT = ["text/plain", "application/json", "text/vtt", "text/srt"]

app = Flask("__services_manager__")
app.config["JSON_AS_ASCII"] = False
app.config["JSON_SORT_KEYS"] = False

logging.basicConfig(format='%(asctime)s %(name)s %(levelname)s: %(message)s', datefmt='%d/%m/%Y %H:%M:%S')
logger = logging.getLogger("__services_manager__")

@app.route('/healthcheck', methods=["GET"])
def healthcheck():
    """ Server healthcheck """
    return "1", 200

@app.route('/job/<jobid>', methods=["GET"])
def jobstatus(jobid):
    task = AsyncResult(jobid)
    state = task.status

    if state == task_states.STARTED:
        return json.dumps({"state": "started", "steps" : task.info.get("steps", {})}), 202
    elif state == task_states.SUCCESS:
        result_id = task.get()
        return json.dumps({"state": "done", "result_id": result_id}), 201
    elif state == task_states.PENDING:
        return json.dumps({"state": "pending",}), 404
    elif state == task_states.FAILURE:
        return json.dumps({"state": "failed", "reason": str(task.result)}), 500
    else:
        return "Task returned an unknown state", 400

@app.route('/results/<result_id>', methods=["GET"])
def results(result_id):
    # Expected format
    expected_format = request.headers.get('accept')
    if not expected_format in SUPPORTED_HEADER_FORMAT:
        return "Accept format {} not supported. Supported MIME types are :{}".format(expected_format, " ".join(SUPPORTED_HEADER_FORMAT)), 400
    
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
        sub_list = [tuple(elem.split(":")) for elem in sub_list if elem.strip() != "" and ":" in elem]
    except:
        logger.warning("Could not parse substitution items: {}".format(sub_list))
        sub_list = []

    return formatResult(result, expected_format, raw_return=return_raw, convert_numbers=convert_numbers, user_sub=sub_list), 200

@app.route('/transcribe', methods=["POST"])
def transcription():
    # Get file and generate hash
    if not len(list(request.files.keys())):
        return "Not file attached to request", 400
    
    elif len(list(request.files.keys())) > 1:
        logger.warning("Received multiple files at once. Multifile is not supported yet, n>1 file are ignored")
    
    file_key = list(request.files.keys())[0]
    file_buffer = request.files[file_key].read()
    extension = request.files[file_key].filename.split(".")[-1]
    file_hash = fileHash(file_buffer)

    # Header check
    expected_format = request.headers.get('accept')
    if not expected_format in SUPPORTED_HEADER_FORMAT:
        return "Accept format {} not supported. Supported MIME types are :{}".format(expected_format, " ".join(SUPPORTED_HEADER_FORMAT)), 400
    logger.debug(request.headers.get('accept'))

    # Request flags
    force_sync = request.form.get("force_sync", False) in [1, True, "true"]
    logger.debug(f"force_sync: {force_sync}")

    # Parse transcription config
    try:
        transcription_config = TranscriptionConfig(request.form.get("transcriptionConfig", {}))
        logger.debug(transcription_config)
    except Exception:
        logger.debug(request.form.get("transcriptionConfig", {}))
        return "Failed to interpret transcription config", 400

    requestlog(logger, request.remote_addr, transcription_config, file_hash, False)

    # If no previous result
    # Create ressource
    try:
        file_path = write_ressource(file_buffer, file_hash, AUDIO_FOLDER, extension)
    except Exception as e:
        logger.error("Failed to write ressource: {}".format(e))
        return "Server Error: Failed to write ressource", 500

    logger.debug("Create transcription task")

    task_info = {"transcription_config": transcription_config.toJson(),
                 "service_name": config.service_name,
                 "hash": file_hash, 
                 "keep_audio": config.keep_audio}
    
    task = transcription_task.apply_async(queue=config.service_name+'_requests', args=[task_info, file_path])
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

    return (json.dumps({"jobid" : task.id}) if expected_format == "application/json" else task.id), 201

@app.route('/revoke/<jobid>', methods=["GET"])
def revoke(jobid):
    AsyncResult(jobid).revoke()
    return "done", 200
    
@app.errorhandler(405)
def method_not_allowed(error):
    return 'The method is not allowed for the requested URL', 405

@app.errorhandler(404)
def page_not_found(error):
    return 'The requested URL was not found', 404

@app.errorhandler(500)
def server_error(error):
    logger.error(error)
    return 'Server Error', 500

if __name__ == '__main__':
    parser = createParser() # Parser definition at server/utils/confparser.py

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
    db_info = {"db_host" : config.mongo_uri, 
               "db_port" : config.mongo_port, 
               "service_name" : config.service_name, 
               "db_name": "transcriptiondb"}
    
    db_client = DBClient(db_info)

    logger.info("Starting ingress")
    logger.debug(config)
    serving = GunicornServing(app, {'bind': '{}:{}'.format('0.0.0.0', 80),
                                    'workers': config.concurrency + 1,
                                    'timeout' : 3600})

    try:
        serving.run()
    except KeyboardInterrupt:
        logger.info("Process interrupted by user")
    finally:
        db_client.close()