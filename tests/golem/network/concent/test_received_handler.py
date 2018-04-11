# pylint: disable=protected-access
import gc
import factory
import importlib
import unittest
import unittest.mock as mock

from golem_messages import exceptions as msg_exceptions
from golem_messages import factories as msg_factories
from golem_messages import message

from golem import testutils
from golem.core import keysauth
from golem.model import Actor
from golem.network import history
from golem.network.concent import received_handler
from golem.network.concent.handlers_library import library
from golem.network.concent.filetransfers import ConcentFiletransferService

from tests.factories import taskserver as taskserver_factories
from tests.factories.resultpackage import ExtractedPackageFactory


class RegisterHandlersTestCase(unittest.TestCase):
    def setUp(self):
        library._handlers = {}

    def test_register_handlers(self):
        class MyHandler():
            def not_a_handler(self, msg):
                pass

            @received_handler.handler_for(message.p2p.Ping)
            def ping_handler(self, msg):
                pass

        instance = MyHandler()
        received_handler.register_handlers(instance)
        self.assertEqual(len(library._handlers), 1)
        self.assertEqual(
            library._handlers[message.p2p.Ping](),
            instance.ping_handler,
        )


@mock.patch("golem.network.history.add")
class TestOnForceReportComputedTaskResponse(unittest.TestCase):
    def setUp(self):
        self.msg = msg_factories.concents.\
            ForceReportComputedTaskResponseFactory()
        self.reasons = message.concents.ForceReportComputedTaskResponse.REASON
        ttc = self.msg.ack_report_computed_task.task_to_compute
        self.call_response = mock.call(
            msg=self.msg,
            node_id=ttc.requestor_id,
            local_role=Actor.Provider,
            remote_role=Actor.Concent,
        )
        importlib.reload(received_handler)

    def tearDown(self):
        library._handlers = {}

    def test_subtask_timeout(self, add_mock):
        self.msg.ack_report_computed_task = None
        self.msg.reject_report_computed_task = None
        self.msg.reason = self.reasons.SubtaskTimeout
        library.interpret(self.msg)
        add_mock.assert_called_once_with(
            msg=self.msg,
            node_id=None,
            local_role=Actor.Provider,
            remote_role=Actor.Concent,
        )

    def test_concent_ack(self, add_mock):
        self.msg.reason = self.reasons.ConcentAck
        self.msg.reject_report_computed_task = None
        library.interpret(self.msg)
        ttc = self.msg.ack_report_computed_task.task_to_compute
        call_inner = mock.call(
            msg=self.msg.ack_report_computed_task,
            node_id=ttc.requestor_id,
            local_role=Actor.Provider,
            remote_role=Actor.Concent,
        )
        self.assertEqual(add_mock.call_count, 2)
        add_mock.assert_has_calls([
            self.call_response,
            call_inner,
        ])

    def test_ack_from_requestor(self, add_mock):
        self.msg.reason = self.reasons.AckFromRequestor
        self.msg.reject_report_computed_task = None
        library.interpret(self.msg)
        ttc = self.msg.ack_report_computed_task.task_to_compute
        self.assertEqual(add_mock.call_count, 2)
        call_inner = mock.call(
            msg=self.msg.ack_report_computed_task,
            node_id=ttc.requestor_id,
            local_role=Actor.Provider,
            remote_role=Actor.Requestor,
        )
        add_mock.assert_has_calls([
            self.call_response,
            call_inner,
        ])

    def test_reject_from_requestor(self, add_mock):
        self.msg.reason = self.reasons.RejectFromRequestor
        self.msg.ack_report_computed_task = None
        library.interpret(self.msg)
        ttc = self.msg.reject_report_computed_task.task_to_compute
        self.assertEqual(add_mock.call_count, 2)
        call_inner = mock.call(
            msg=self.msg.reject_report_computed_task,
            node_id=ttc.requestor_id,
            local_role=Actor.Provider,
            remote_role=Actor.Requestor,
        )
        add_mock.assert_has_calls([
            self.call_response,
            call_inner,
        ])


class TaskServerMessageHandlerTestBase(
        testutils.DatabaseFixture, testutils.TestWithClient):

    def setUp(self):
        gc.collect()
        super().setUp()
        self.task_server = taskserver_factories.TaskServer(
            client=self.client,
        )
        history.MessageHistoryService()
        # received_handler.TaskServerMessageHandler is instantiated
        # in TaskServer.__init__

        self.cf_transfer = self.client.concent_filetransfers.transfer

    def tearDown(self):
        # Remove registered handlers
        del self.task_server
        gc.collect()


class TaskServerMessageHandlerTest(TaskServerMessageHandlerTestBase):

    @mock.patch("golem.network.concent.received_handler.logger.warning")
    def test_concent_service_refused(self, logger_mock):
        msg = msg_factories.concents.ServiceRefusedFactory()
        library.interpret(msg)
        self.assertIn('Concent service (%s) refused',
                      logger_mock.call_args[0][0])

    @mock.patch("golem.task.taskserver.TaskServer"
                ".receive_subtask_computation_time")
    def test_verdict_report_computed_task(
            self,
            rsct_mock):
        msg = msg_factories.concents.VerdictReportComputedTaskFactory()
        library.interpret(msg)
        self.assertEqual(
            self.client.keys_auth.ecc.verify.call_count,
            2,
        )
        rct = msg.force_report_computed_task.report_computed_task
        rsct_mock.assert_called_once_with(
            msg.ack_report_computed_task.subtask_id,
            rct.computation_time,
        )

    @mock.patch("golem.task.taskserver.TaskServer"
                ".receive_subtask_computation_time")
    @mock.patch("golem.task.taskserver.TaskServer.get_result")
    def test_verdict_report_computed_task_invalid_sig(
            self,
            get_mock,
            rsct_mock):
        self.client.keys_auth.ecc.verify.side_effect = \
            msg_exceptions.InvalidSignature
        msg = msg_factories.concents.VerdictReportComputedTaskFactory()
        library.interpret(msg)
        ttc_from_ack = msg.ack_report_computed_task.task_to_compute
        self.client.keys_auth.ecc.verify.assert_called_once_with(
            inputb=ttc_from_ack.get_short_hash(),
            sig=ttc_from_ack.sig)
        rsct_mock.assert_not_called()
        get_mock.assert_not_called()

    @mock.patch("golem.task.taskserver.TaskServer"
                ".receive_subtask_computation_time")
    @mock.patch("golem.task.taskserver.TaskServer.get_result")
    def test_verdict_report_computed_task_diff_ttc(
            self,
            get_mock,
            rsct_mock):
        msg = msg_factories.concents.VerdictReportComputedTaskFactory()
        msg.ack_report_computed_task.task_to_compute = \
            msg_factories.tasks.TaskToComputeFactory()
        self.assertNotEqual(
            msg.ack_report_computed_task.task_to_compute,
            msg.force_report_computed_task.report_computed_task.task_to_compute,
        )
        library.interpret(msg)
        rsct_mock.assert_not_called()
        get_mock.assert_not_called()

    @mock.patch(
        "golem.network.concent.helpers.process_report_computed_task"
    )
    def test_force_report_computed_task(self, helper_mock):
        msg = msg_factories.concents.ForceReportComputedTaskFactory()
        helper_mock.return_value = returned_msg = object()
        library.interpret(msg)
        helper_mock.assert_called_once_with(
            msg=msg.report_computed_task,
            ecc=mock.ANY,
            task_header_keeper=mock.ANY,
        )
        self.task_server.client.concent_service.submit_task_message \
            .assert_called_once_with(
                msg.report_computed_task.subtask_id,
                returned_msg)

    @mock.patch('golem.task.taskmanager.TaskManager.task_computation_failure')
    def test_force_get_task_result_failed(self, tcf):
        fgtrf = msg_factories.concents.ForceGetTaskResultFailedFactory()
        library.interpret(fgtrf)

        msg = history.MessageHistoryService.get_sync_as_message(
            task=fgtrf.task_id,
            subtask=fgtrf.subtask_id,
            node=fgtrf.task_to_compute.provider_id,
            msg_cls='ForceGetTaskResultFailed',
        )

        self.assertIsInstance(msg, message.concents.ForceGetTaskResultFailed)
        tcf.assert_called_once_with(
            fgtrf.subtask_id,
            'Error downloading the task result through the Concent')

    def test_force_subtask_results_response_empty(self):
        msg = message.concents.ForceSubtaskResultsResponse()
        # pylint: disable=no-member
        self.assertIsNone(msg.subtask_results_accepted)
        self.assertIsNone(msg.subtask_results_rejected)
        # pylint: enable=no-member
        with self.assertRaises(RuntimeError):
            library.interpret(msg)

    @mock.patch("golem.network.history.add")
    @mock.patch("golem.task.taskserver.TaskServer.subtask_accepted")
    def test_force_subtask_results_response_accepted(
            self,
            accepted_mock,
            add_mock):
        msg = msg_factories.concents.ForceSubtaskResultsResponseFactory()
        msg.subtask_results_rejected = None
        library.interpret(msg)
        accepted_mock.assert_called_once_with(
            subtask_id=msg.subtask_id,
            accepted_ts=msg.subtask_results_accepted.payment_ts,
        )
        add_mock.assert_called_once_with(
            msg=msg.subtask_results_accepted,
            node_id=mock.ANY,
            local_role=Actor.Provider,
            remote_role=Actor.Requestor,
        )

    @mock.patch("golem.network.history.add")
    @mock.patch("golem.task.taskserver.TaskServer.subtask_rejected")
    def test_force_subtask_results_response_rejected(
            self,
            rejected_mock,
            add_mock):
        msg = msg_factories.concents.ForceSubtaskResultsResponseFactory()
        msg.subtask_results_accepted = None
        library.interpret(msg)
        rejected_mock.assert_called_once_with(
            subtask_id=msg.subtask_id,
        )
        add_mock.assert_called_once_with(
            msg=msg.subtask_results_rejected,
            node_id=mock.ANY,
            local_role=Actor.Provider,
            remote_role=Actor.Requestor,
        )

    @mock.patch('golem.task.taskmanager.TaskManager.task_computation_failure')
    def test_fgtr_service_refused(self, tcf):
        fgtr = msg_factories.concents.ForceGetTaskResultFactory()
        sr = msg_factories.concents.ServiceRefusedFactory(
            subtask_id=fgtr.subtask_id)
        library.interpret(sr, response_to=fgtr)
        tcf.assert_called_once_with(
            fgtr.subtask_id,
            'Concent refused to assist in forced results download')

    @mock.patch('golem.task.taskmanager.TaskManager.task_computation_failure')
    def test_force_get_task_result_rejected(self, tcf):
        fgtrr = msg_factories.concents.ForceGetTaskResultRejectedFactory()
        library.interpret(fgtrr, response_to=fgtrr.force_get_task_result)
        tcf.assert_called_once_with(
            fgtrr.subtask_id,
            'Concent claims ForceGetTaskResult no longer possible'
        )

    @mock.patch('golem.network.concent.received_handler.logger.debug')
    def test_ack_force_get_task_result(self, log):
        afgtr = msg_factories.concents.AckForceGetTaskResultFactory()
        library.interpret(afgtr, response_to=afgtr.force_get_task_result)
        self.assertEqual(log.call_count, 1)


class FiletransfersTestBase(TaskServerMessageHandlerTestBase):

    def setUp(self):
        super().setUp()
        self.client.concent_filetransfers = ConcentFiletransferService(
            keys_auth=keysauth.KeysAuth(
                datadir=self.path,
                private_key_name='priv_key',
                password='password',
            )
        )

        self.cft = self.client.concent_filetransfers

        cft_patch = mock.patch(
            'golem.network.concent.filetransfers'
            '.ConcentFiletransferService.running',
            mock.Mock(return_value=True)
        )
        cft_patch.start()
        self.addCleanup(cft_patch.stop)


class FileTransferTokenTests:
    MSG_FACTORY: factory.Factory

    def setUp(self):
        super().setUp()  # noqa: pylint:disable=no-member

        self.wtr = taskserver_factories.WaitingTaskResultFactory(
            result_path=self.path)
        self.rct = msg_factories.tasks.ReportComputedTaskFactory(
            subtask_id=self.wtr.subtask_id)

    def _get_correct_message(self):
        return self.MSG_FACTORY(
            force_get_task_result__report_computed_task=self.rct)

    def _get_message_without_ftt(self):
        return self.MSG_FACTORY(file_transfer_token=None)

    def _get_message_ftt_wrong_type(self):
        raise NotImplementedError()

    @mock.patch('golem.network.concent.received_handler.logger.warning')
    def test_no_ftt(self, log_mock):
        msg = self._get_message_without_ftt()
        library.interpret(msg)
        self.cf_transfer.assert_not_called()
        log_mock.assert_called_once()
        self.assertIn('File Transfer Token invalid', log_mock.call_args[0][0])

    @mock.patch('golem.network.concent.received_handler.logger.warning')
    def test_ftt_wrong_type(self, log_mock):
        msg = self._get_message_ftt_wrong_type()
        library.interpret(msg)
        self.cf_transfer.assert_not_called()
        log_mock.assert_called_once()
        self.assertIn('File Transfer Token invalid', log_mock.call_args[0][0])


class ForceGetTaskResultUploadTest(FileTransferTokenTests,  # noqa pylint:disable=too-many-ancestors
                                   FiletransfersTestBase):
    MSG_FACTORY = msg_factories.concents.ForceGetTaskResultUploadFactory

    def _get_message_ftt_wrong_type(self):
        return self.MSG_FACTORY(file_transfer_token__download=True,
                                file_transfer_token__upload=False)

    @mock.patch('golem.network.concent.received_handler.logger.debug')
    def test_force_get_task_result_upload(self, log_mock):
        fgtru = self._get_correct_message()
        self.task_server.results_to_send[self.wtr.subtask_id] = self.wtr

        library.interpret(fgtru)

        response = ''

        with mock.patch(
            'golem.network.concent.filetransfers'
            '.ConcentFiletransferService.upload',
            mock.Mock(return_value=response)
        ) as upload_mock:
            self.cft._run()

        upload_mock.assert_called_once()
        self.assertEqual(
            upload_mock.call_args[0][0].file_path,
            self.wtr.result_path)
        self.assertEqual(
            upload_mock.call_args[0][0].file_transfer_token,
            fgtru.file_transfer_token)

        log_mock.assert_called_with(
            "Concent results upload sucessful: %r, %s",
            fgtru.subtask_id,
            response)

    @mock.patch('golem.network.concent.received_handler.logger.warning')
    def test_force_get_task_result_upload_failed(self, log_mock):
        fgtru = self._get_correct_message()
        self.task_server.results_to_send[self.wtr.subtask_id] = self.wtr

        library.interpret(fgtru)

        exception = Exception()

        with mock.patch(
            'golem.network.concent.filetransfers'
            '.ConcentFiletransferService.upload',
            mock.Mock(side_effect=exception)
        ):
            self.cft._run()

        log_mock.assert_called_with(
            "Concent upload failed: %r, %s",
            fgtru.subtask_id,
            exception)

    @mock.patch('golem.network.concent.received_handler.logger.warning')
    def test_force_get_task_result_upload_wtr_not_found(self, log_mock):
        fgtru = self._get_correct_message()
        library.interpret(fgtru)
        self.cf_transfer.assert_not_called()
        log_mock.assert_called_once()
        self.assertIn('Cannot find the subtask', log_mock.call_args[0][0])


class ForceGetTaskResultDownloadTest(FileTransferTokenTests,  # noqa pylint:disable=too-many-ancestors
                                     FiletransfersTestBase):
    MSG_FACTORY = msg_factories.concents.ForceGetTaskResultDownloadFactory

    def _get_message_ftt_wrong_type(self):
        return self.MSG_FACTORY(file_transfer_token__download=False,
                                file_transfer_token__upload=True)

    def test_force_get_task_result_download(self):
        fgtrd = self._get_correct_message()

        library.interpret(fgtrd)

        ep = ExtractedPackageFactory()

        extract = \
            self.task_server.task_manager.task_result_manager.extract = \
            mock.Mock(return_value=ep)

        verify_results = self.task_server.verify_results = mock.Mock()

        with mock.patch(
            'golem.network.concent.filetransfers'
            '.ConcentFiletransferService.download',
        ) as download_mock:
            self.cft._run()

        download_mock.assert_called_once()
        self.assertEqual(
            download_mock.call_args[0][0].file_transfer_token,
            fgtrd.file_transfer_token)

        extract.assert_called_once()
        verify_results.assert_called_once_with(
            report_computed_task=self.rct, extracted_package=ep
        )

    @mock.patch('golem.network.concent.received_handler.logger.warning')
    def test_force_get_task_result_download_failed(self, log_mock):
        fgtrd = self._get_correct_message()

        library.interpret(fgtrd)

        exception = Exception()

        with mock.patch(
            'golem.network.concent.filetransfers'
            '.ConcentFiletransferService.download',
            mock.Mock(side_effect=exception)
        ):
            self.cft._run()

        log_mock.assert_called_with(
            "Concent download failed: %r, %s",
            self.rct.subtask_id, exception
        )

    @mock.patch('golem.network.concent.received_handler.logger.error')
    def test_force_get_task_result_download_extraction_failed(self, log_mock):
        fgtrd = self._get_correct_message()

        library.interpret(fgtrd)

        exception = Exception()
        extract = \
            self.task_server.task_manager.task_result_manager.extract = \
            mock.Mock(side_effect=exception)

        with mock.patch(
            'golem.network.concent.filetransfers'
            '.ConcentFiletransferService.download',
        ):
            self.cft._run()

        extract.assert_called_once()
        log_mock.assert_called_with(
            "Concent results extraction failure: %r, %s",
            fgtrd.subtask_id, exception
        )
