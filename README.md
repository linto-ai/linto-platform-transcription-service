# LinTO Platform Transcription service
## Description
The transcription service is the API for requesting transcriptions.

The service allows you to:
* Request asynchronous transcriptions from a variety of audio or video files formats.
* Specify transcription subtask such as diarization and punctuation.
* Follow transcription task state and progress.
* Automaticaly store transcription results in a database.
* Fetch transcription results with different formats and options.

## Table of content
* [Prerequisites](#prerequisites)
* [Deploy](#deploy)
  * [Using docker run](#using-docker-run)
  * [Using docker compose](#using-docker-compose)
  * [Environement Variables](#environement-variables)
* [API](#api)
  * [/list-services](#environement-variables)
      * [Subservice resolution](#subservice-resolution)
  * [/transcribe](#transcribe)
    * [Transcription config](#transcription-config)
  * [/transcribe-multi](#transcribe-multi)
    * [MultiTranscription config](#multitranscription-config)
  * [/job/{jobid}](#job)
  * [/results/{result_id}](#results)
    * [Transcription results](#transcription-results)
  * [/job-log/{jobid}](#job-log)
  * [/docs](#docs)
* [Usage](#usage)
* [License](#license)
***

## Prerequisites
To use the transcription service you must have at least:
* One or multiple instances of [linto-platform-stt](https://github.com/linto-ai/linto-platform-stt) > 3.2.0 running and configured with the same `SERVICE_NAME`.
* A REDIS broker running at `SERVICES_BROKER`.
* A mongo DB running at `MONGO_HOST:MONGO_PORT`.

Optionnaly, for diarization or punctuation the following are needed:
* One or multiple instances of [linto-diarization-worker](https://github.com/linto-ai/linto-platform-diarization) > 1.2.0 for speaker diarization configured on the same service broker (LANGUAGE must be compatible).
* One or multiple instances of [linto-punctuation-worker](https://github.com/linto-ai/linto-platform-punctuation) > 1.2.0 for text punctuation configured on the same service broker (LANGUAGE must be compatible).

To share audio files across the different services they must be configured with the same shared volume `RESSOURCE_FOLDER`.

## Deploy
### Using docker run
1- First build the image:
```bash
cd linto-platform-transcription-service &&
docker build . -t transcription_service
```
2- Create and fill the .env
```bash
cp .envdefault .env
```
Fill the .env with the value described bellow [Environement Variables](#environement-variables)

2- Launch a container:
```bash
docker run --rm -it -p $SERVING_PORT:80 \
    -v $YOUR_SHARED_FOLDER:/opt/audio \
    --env-file .env \
    --name my_transcription_api \
    transcription_service \
    /bin/bash
```
Fill ```SERVING_PORT```, ```YOUR_SHARED_FOLDER``` with your values.

### Using docker compose
1- Create and fill the .env
```bash
cp .envdefault .env
```
Fill the .env with the value described bellow [Environement Variables](#environement-variables)

2- Compose
```bash
docker-compose up .
```

### Environement Variables

| Env variable| Description | Example |
|:-|:-|:-|
|SERVICE_NAME| STT service name, use to connect to the proper redis channel and mongo collection|my_stt_service|
|LANGUAGE| Language code as a BCP-47 code | fr-FR |
|KEEP_AUDIO|Either audio files are kept after request|1 (true) / 0 (false)|
|CONCURRENCY|Number of workers (default 10)|10|
|SERVICES_BROKER|Message broker address|redis://broker_address:6379|
|BROKER_PASS|Broker Password| Password|
|MONGO_HOST|MongoDB results url|my-mongo-service|
|MONGO_PORT|MongoDB results port|27017|
|RESOLVE_POLICY| Subservice resolve policy (default ANY) * |ANY \| DEFAULT \| STRICT |
|<SERVICE_TYPE>_DEFAULT| Default serviceName for subtask <SERVICE_TYPE> * | punctuation-1 |

*: See [Subservice Resolution](#subservice-resolution)

## API
The transcription service offers a transcription API REST to submit transcription requests.

The transcription service revolves arround 2 concepts:
* Asynchronous jobs identified with job_id: A job_id represents an ongoing transcription task.
* Transcription results identified by result_id.

Typical transcription process follows this steps:
1. Submit your file and the transcription configuration on ```/transcribe```. The route returns a 201 with the job_id
2. Use the ```/job/{job_id}``` route to follow the job's progress. When the job is finished, you'll be greated with a 201 alongside a result_id.
3. Fetch the transcription result using the ```/results/{result_id}``` route specifying your desired format and options. 

### /list-services
The list-services GET route fetch available sub-services for transcription.

It returns a json object containing list of deployed services indexed by service type. Services listed are filtered using the set LANGUAGE parameters.

```json
{
  "diarization": [ # Service type
    {
      "service_name": "diarization-1", # Service name. Used as parameter in transcription config to call this specific service.
      "service_type": "diarization", # Service type
      "service_language": "*", # Supported language
      "queue_name": "diarization-queue", # Celery queue used by this service
      "info": "A diarization service", # Information about the service.
      "instances": [ # Instances of this specific service.
        {
          "host_name": "feb42aacd8ad", # Instance unique id
          "last_alive": 1665996709, # Last heartbeat
          "version": "1.2.0", # Service version
          "concurrency": 1 # Concurrency of the instance
        }
      ]
    }
  ],
  "punctuation": [
    {
      "service_name": "punctuation-1",
      "service_type": "punctuation",
      "service_language": "fr-FR",
      "queue_name": "punctuation-queue",
      "info": "A punctuation service",
      "instances": [
        {
          "host_name": "b0e9e24349a9",
          "last_alive": 1665996709,
          "version": "1.2.0",
          "concurrency": 1
        }
      ]
    }
  ]
}

```
#### Subservice resolution
Subservice resolution is the mecanism allowing the transcription service to use the proper optionnal subservice such as diarization or punctuation prediction. Resolution is applied when no serviceName is passed along subtask configs. 

There is 3 policies to resolve service names:
* ANY: Use any compatible subservice.
* DEFAULT: Use the service default subservice (must be declared)
* STRICT: If the service is not specified, raise an error.

Resolve policy is declared at launch using the RESOLVE_POLICY environement variable: ANY | DEFAULT | STRICT (default ANY).

Default service names must be declared at launch: <SERVICE_TYPE>_DEFAULT. E.g. The default punctuation subservice is "punctuation-1", `PUNCTUATION_DEFAULT=punctuation1`.

__Language compatibily__

A subservice is compatible if its language(s) is(are) compatible with the transcription-service language:

transcription-service language <-> subservice language.
* Same BCP-27 code: fr_Fr <-> fr-FR => OK
* Language contained: fr-FR <-> fr-FR|it_IT|en_US => OK
* Star token (all_language): fr-FR <-> * => OK


### /transcribe
The /transcribe route allows POST request containing an audio file.

The route accepts multipart/form-data requests.

Response format can be application/json or text/plain as specified in the accept field of the header.

|Form Parameter| Description | Required |
|:-|:-|:-|
|transcriptionConfig|(object optionnal) A transcriptionConfig Object describing transcription parameters | See [Transcription config](#transcription-config) |
|force_sync|(boolean optionnal) If True do a synchronous request | [true \| **false** \| null] |

If the request is accepted, answer should be ```201``` with a json or text response containing the jobid.

With accept: application/json
```json
{"jobid" : "the-job-id"}
```
With accept: text/plain
```
the-job-id
```

If the **force_sync** flag is set to true, the request returns a ```200``` with the transcription (see [Transcription Results](#transcription-results)) using the same accept options as the /result/{result_id} route.  

> The use of force_sync for big files is not recommended as it blocks a worker for the duration of the transcription.

Additionnaly a timestamps file can be uploaded alongside the audio file containing segments timestamps to transcribe. Timestamps file are text file containing a segment per line with optionnal speakerid such as:
```txt
# start stop [speakerid]
0.0 7.05 1
7.05 13.0
```

#### Transcription config
The transcriptionConfig object describe the transcription parameters and flags of the request. It is structured as follows:
```json
{
  "punctuationConfig": {
    "enablePunctuation": false, # Applies punctuation
    "serviceName": null # Force serviceName (See SubService resolution)
  },
  "enablePunctuation": false, # Applies punctuation (Do not use, kept for backward compatibility)
  "diarizationConfig": {
    "enableDiarization": false, #Enables speaker diarization
    "numberOfSpeaker": null, #If set, forces number of speaker
    "maxNumberOfSpeaker": null #If set and and numberOfSpeaker is not, limit the maximum number of speaker.
    "serviceName": null # Force serviceName (See SubService Resolving)
  }
}
```

ServiceNames can be filled to use a specific subservice version. Available services are available on /list-services.



### /transcribe-multi
The /transcribe-multi route allows POST request containing multiple audio files. It is assumed each file contains a speaker or a group of speaker and files taken together form a conversation.

The route accepts multipart/form-data requests.

Response format can be application/json or text/plain as specified in the accept field of the header.

|Form Parameter| Description | Required |
|:-|:-|:-|
|transcriptionConfigMulti|(object optionnal) A transcriptionConfig Object describing transcription parameters | See [MultiTranscription config](#multitranscription-config) |


If the request is accepted, answer should be ```201``` with a json or text response containing the jobid.

With accept: application/json
```json
{"jobid" : "the-job-id"}
```
With accept: text/plain
```
the-job-id
```

#### MultiTranscription config

The transcriptionConfig object describe the transcription parameters and flags of the request. It is structured as follows:
```json
{
  "punctuationConfig": {
    "enablePunctuation": false, # Applies punctuation
    "serviceName": null # Force serviceName (See SubService resolution)
  }
}
```

### /job/

The /job/{jobid} GET route allow you to get the state of the given transcription job.

Response format is application/json.

* If the job state is **started**, it returns a code ```102``` with informations on the progress.
* If the job state is **done**, it returns a code ```201``` with the ```result_id```.
* If the job state is **pending** returns a code ```404```. Pending can mean 2 things: a transcription worker is not yet available or the jobid does not exist. 
* If the job state is **failed** returns a code ```400```.

```json
{
  #Task pending or wrong jobid: 404
  {"state": "pending"}

  #Task started: 102
  {"state": "started", "progress": {"current": 1, "total": 3, "step": "Transcription (75%)"}}

  #Task completed: 201
  {"state": "done", "result_id" : "result_id"}

  #Task failed: 400
  {"state": "failed", "reason": "Something went wrong"}
}
```

### /results/
The /results/{result_id} GET route allows you to fetch transcription result associated to a result_id.

#### Transcription results
The accept header specifies the format of the result:
* application/json returns the complete result as a json object; 
```json
{
  "confidence": 0.9, # Overall transcription confidence
  "raw_transcription": "this is a transcription diarization and punctuation are set", # Raw transcription
  "segments": [ # Speech segment representing continious speech by a single speaker
    {
      "duration": 3.12991, # Segment duration
      "end": 3.12991, # Segment stop time
      "raw_segment": "this is a transcription", # Raw transcription of the speech segment
      "segment": "This is a transcription", # Processed transcription of the segment (punctuation, normalisation, ...)
      "spk_id": "spk1", # Speaker id
      "start": 0, # Segment start time
      "words": [ # Segment's word informations
        {
      "duration": 4.59,
      "end": 7.71991,
      "raw_segment": "diarization and punctuation are set",
      "segment": "Diarization and punctuation are set",
      "spk_id": "spk2",
      "start": 3.12991,
      "words": [
        {
          "conf": 0.89654,
          "end": 4.1382,
          "start": 3.12991,
          "word": "diarization"
        }
        ...
      ]
    }
      ]
    }
  ],
  "transcription_result": "spk1: This is a transcription\nspk2: Diarization and punctuation are set" # Final transcription
}
```
* text/plain returns the final transcription as text
```
spk1: This is a transcription
spk2: Diarization and punctuation are set
```
* text/vtt returns the transcription formated as WEBVTT captions.
```
WEBVTT Kind: captions; Language: en_US

00:00.000 --> 00:03.129
This is a transcription

00:03.129 --> 00:07.719
Diarization and punctuation are set
```
* text/srt returns the transcription formated as SubRip Subtitle.
```
1
00:00:00,000 --> 00:00:03,129
This is a transcription

2
00:00:03,129 --> 00:00:07,719
Diarization and punctuation are set
```
#### Query string options
Additionnaly you can specify options using query string:
* return_raw: if set to true, return the raw transcription (No punctuation and no post processing).
* convert_number: if set to true, convert numbers from characters to digits.
* wordsub: accepts multiple values formated as ```originalWord:substituteWord```. Substitute words in the final transcription.

### /job-log/
The /job-log/{jobid} GET route to is used retrieve job details for debugging. Returns logs as raw text.

### /docs
The /docs route offers access to a swagger-ui interface with the API specifications (OAS3).

It also allows to directly test requests using pre-filled modifiable parameters.

## Usage
Request exemple:

__Initial request__
```bash
curl -X POST "http://MY_HOST:MY_PORT/transcribe" -H  "accept: application/json" -H  "Content-Type: multipart/form-data" -F 'transcriptionConfig={
  "enablePunctuation": {
    "enablepunctuation": true,
    "servicename": null
  },
  "diarizationConfig": {
    "enableDiarization": true,
    "numberOfSpeaker": null,
    "maxNumberOfSpeaker": null,
    "servicename": null
  }
}' -F "force_sync=" -F "file=@MY_AUDIO.wav;type=audio/x-wav"

> {"jobid": "de37224e-fd9d-464d-9004-dcbf3c5b4300"}
```

__Request job status__
```bash
curl -X GET "http://MY_HOST:MY_PORT/job/6e3f8b5a-5b5a-4c3d-97b6-3c438d7ced25" -H  "accept: application/json"

> {"result_id": "769d9c20-ad8c-4957-9581-437172434ec0", "state": "done"}
```

__Fetch result__
```bash
curl -X GET "http://MY_HOST:MY_PORT/results/769d9c20-ad8c-4957-9581-437172434ec0" -H  "accept: text/vtt"
> WEBVTT Kind: captions; Language: en_US

00:00.000 --> 00:03.129
This is a transcription

00:03.129 --> 00:07.719
Diarization and punctuation are set
```
## License
This project is licensed under AGPLv3 license. Please refer to the LICENSE file for full description of the license.

## Acknowledgment
* [celery](https://docs.celeryproject.org/en/stable/index.html): Distributed Task Queue.
* [pymongo](https://pypi.org/project/pymongo/): A MongoDB python client.
* [text2num](https://pypi.org/project/text2num/): A text to number convertion library.
* [Supervisor](http://supervisord.org/): A Process Control System.
