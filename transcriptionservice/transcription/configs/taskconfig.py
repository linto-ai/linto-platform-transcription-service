""" This module contains the configuration classe for the transcription subtasks"""

from faulthandler import is_enabled
from typing import Union

from transcriptionservice.transcription.configs.sharedconfig import Config
from transcriptionservice.transcription.utils.audio import validate_vad_method


class TaskConfig(Config):
    service_type = None
    task_name = None
    serviceName = None
    serviceQueue = None

    def __init__(self, config: Union[str, dict] = {}):
        super().__init__(config)
        self._checkConfig()
        self.isEnabled = False  # If the service is necessary for the request
        self.isAvailable = False  # If the service is resolvable (Available or interchangeable)
        self.serviceQueue = None

    def setService(self, serviceName: str, serviceQueue: str) -> None:
        self.isAvailable = True
        self.serviceName = serviceName
        self.serviceQueue = serviceQueue


class PunctuationConfig(TaskConfig):
    """PunctuationConfig parses and holds punctuation related configuration.
    Expected configuration format is as follows:
    ```json
    {
      "enableDiarization": boolean (false),
      "serviceName: str (None)
    }
    ```
    """

    service_type = "punctuation"
    task_name = "punctuation_task"

    _keys_default = {
        "enablePunctuation": False,
        "serviceName": None,
        "serviceQueue": None,
    }

    def __init__(self, config: Union[str, dict] = {}):
        super().__init__(config)
        self._checkConfig()
        self.isEnabled = self.enablePunctuation  # If the service is necessary for the request


class DiarizationConfig(TaskConfig):
    """DiarizationConfig parses and holds diarization related configuration.
    Expected configuration format is as follows:
    ```json
    {
      "enableDiarization": boolean (false),
      "numberOfSpeaker": integer (null),
      "maxNumberOfSpeaker": integer (null),
      "serviceName": string (null),
      "serviceQueue": string (null)
    }
    ```
    """

    service_type = "diarization"
    task_name = "diarization_task"

    _keys_default = {
        "enableDiarization": False,
        "numberOfSpeaker": None,
        "maxNumberOfSpeaker": None,
        "serviceName": None,
        "serviceQueue": None,
    }

    def __init__(self, config: Union[str, dict] = {}):
        super().__init__(config)
        self._checkConfig()
        self.isEnabled = self.enableDiarization

    def _checkConfig(self):
        """Check diarization parameters."""
        if self.enableDiarization in ["true", 1, True]:
            self.enableDiarization = True
            if type(self.numberOfSpeaker) is int:
                if self.numberOfSpeaker <= 0:
                    self.numberOfSpeaker = None
                elif self.numberOfSpeaker == 1:
                    self.enableDiarization = False
                    return

            if type(self.maxNumberOfSpeaker) is int:
                if self.numberOfSpeaker is not None:
                    self.maxNumberOfSpeaker = self.numberOfSpeaker
                else:
                    if self.maxNumberOfSpeaker <= 0:
                        self.maxNumberOfSpeaker = None
        else:
            self.enableDiarization = False


class VADConfig(Config):
    """VADConfig parses and holds VAD related configuration.
    Expected configuration format is as follows:
    ```json
    {
      "enableVAD": boolean (true),
      "methodName": string ("WebRTC"),
      "minDuration": float (0.0)
    }
    ```
    """

    _keys_default = {
        "enableVAD": True,
        "methodName": "WebRTC",
        "minDuration": 0,
    }

    def __init__(self, config: Union[str, dict] = {}):
        super().__init__(config)
        self._checkConfig()
        self.isEnabled = self.enableVAD

    def _checkConfig(self):
        """Check VAD parameters."""

        if self.methodName is None and not self.enableVAD:
            pass
        else:
            self.methodName = validate_vad_method(self.methodName)
