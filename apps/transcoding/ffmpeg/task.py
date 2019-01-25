from typing import Optional

import golem_messages.message
from apps.core.task.coretask import CoreTaskTypeInfo
from apps.transcoding.common import Container, VideoCodec, AudioCodec
from apps.transcoding.ffmpeg.environment import ffmpegEnvironment
from apps.transcoding.task import TranscodingTaskOptions, \
    TranscodingTaskBuilder, TranscodingTaskDefinition, TranscodingTask


# TODO:
# Czy typować? Co robia inni (A?)
# Czym sie rozni minimal definition od full?
# poprawic impoorty
# Co to są property?
# Obsluga bledow
# LOGI

from golem.task.taskbase import Task
from golem.task.taskstate import SubtaskStatus


class ffmpegTaskTypeInfo(CoreTaskTypeInfo):
    def __init__(self):
        super().__init__('FFMPEG', TranscodingTaskDefinition,
                         TranscodingTaskOptions, TranscodingTaskBuilder)


class ffmpegTaskBuilder(TranscodingTaskBuilder):
    SUPPORTED_FILE_TYPES = [Container.MKV, Container.AVI,
                            Container.MP4]
    SUPPORTED_VIDEO_CODECS = [VideoCodec.AV1, VideoCodec.MPEG_2,
                              VideoCodec.H_264]
    SUPPORTED_AUDIO_CODECS = [AudioCodec.MP3, AudioCodec.AAC]


class ffmpegTask(TranscodingTask):
    ENVIRONMENT_CLASS = ffmpegEnvironment



    def query_extra_data(self, perf_index: float, node_id: Optional[str] = None,
                         node_name: Optional[str] = None) -> Task.ExtraData:

        self.subtasks_given[sid]['status'] = SubtaskStatus.starting
        self.subtasks_given[sid]['perf'] = perf_index
        self.subtasks_given[sid]['node_id'] = node_id
        self.subtasks_given[sid]['subtask_id'] = sid

        ctd = golem_messages.message.ComputeTaskDef()

        return Task.ExtraData(ctd=ctd)
