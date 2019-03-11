import json
import logging
import os
from datetime import datetime
from typing import Type

import numpy
from twisted.internet.defer import Deferred

from golem.verificator.constants import SubtaskVerificationState
from golem.verificator.rendering_verifier import FrameRenderingVerifier


logger = logging.getLogger(__name__)


# FIXME #2086
# pylint: disable=R0902
class BlenderVerifier(FrameRenderingVerifier):
    DOCKER_NAME = 'golemfactory/blender_verifier'
    DOCKER_TAG = '1.0'

    def __init__(self, verification_data, docker_task_cls: Type) -> None:
        super().__init__(verification_data)
        self.finished = Deferred()
        self.docker_task_cls = docker_task_cls
        self.timeout = 0
        self.docker_task = None

    def _get_part_size(self, subtask_info):
        if subtask_info['use_frames'] and len(subtask_info['all_frames']) \
                >= subtask_info['total_tasks']:
            resolution_y = subtask_info['resolution'][1]
        else:
            resolution_y = int(round(numpy.float32(
                numpy.float32(subtask_info['crops'][0]['borders_y'][0]) *
                numpy.float32(subtask_info['resolution'][1]))))
        return subtask_info['resolution'][0], resolution_y

    def start_verification(self):
        self.time_started = datetime.utcnow()
        try:
            self.start_rendering()
        # pylint: disable=W0703
        except Exception as exception:
            logger.error('Verification failed %r', exception)
            self.finished.errback(exception)

        return self.finished

    def stop(self):
        if self.docker_task:
            self.docker_task.end_comp()

    def start_rendering(self, timeout=0):
        self.timeout = timeout

        def success(result):
            logger.debug('Success Callback')
            self.state = SubtaskVerificationState.VERIFIED
            return self.verification_completed()

        def failure(exception):
            logger.warning('Failure callback %r', exception)
            self.state = SubtaskVerificationState.WRONG_ANSWER
            return exception

        self.finished.addCallback(success)
        self.finished.addErrback(failure)

        subtask_info = self.verification_data['subtask_info']
        work_dir = os.path.dirname(self.verification_data['results'][0])
        dir_mapping = self.docker_task_cls.specify_dir_mapping(
            resources=subtask_info['path_root'],
            temporary=os.path.dirname(work_dir),
            work=work_dir,
            output=os.path.join(work_dir, 'output'),
            logs=os.path.join(work_dir, 'logs'),
        )

        extra_data = dict(
            subtask_paths=['/golem/work/{}'.format(
                os.path.basename(i)) for i in self.verification_data['results']
            ],
            subtask_borders=[
                subtask_info['crop_window'][0],
                subtask_info['crop_window'][2],
                subtask_info['crop_window'][1],
                subtask_info['crop_window'][3],
            ],
            scene_path=subtask_info['scene_file'],
            resolution=subtask_info['resolution'],
            samples=subtask_info['samples'],
            frames=subtask_info['frames'],
            output_format=subtask_info['output_format'],
            basefilename='crop',
            entrypoint="python3 /golem/scripts_verifier/runner.py",
        )

        self.docker_task = self.docker_task_cls(
            docker_images=[(self.DOCKER_NAME, self.DOCKER_TAG)],
            extra_data=extra_data,
            dir_mapping=dir_mapping,
            timeout=self.timeout)

        def error(exception):
            logger.warning('Verification process exception %s', exception)
            self.finished.errback(exception)

        def callback(*_):
            with open(os.path.join(dir_mapping.output, 'verdict.json'), 'r') \
                    as f:
                verdict = json.load(f)

            logger.info(
                'Subtask %s verification verdict: %s',
                subtask_info['subtask_id'],
                verdict,
            )
            if verdict['verdict']:
                self.finished.callback(True)
            else:
                self.finished.errback(
                    Exception('Verification result negative', verdict))

        d = self.docker_task.start()
        d.addErrback(error)
        d.addCallback(callback)
